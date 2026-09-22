from __future__ import annotations
import asyncio,hashlib,json,logging,os,re,threading,time,uuid
from pathlib import Path
from cryptography.fernet import Fernet,InvalidToken
import paho.mqtt.client as mqtt

log=logging.getLogger('mttl-up.ha')

DEFAULTS={
    'enabled':False,'host':'','port':1883,'username':'','tls':False,
    'topic_prefix':'mttl','discovery_prefix':'homeassistant','birth_topic':'homeassistant/status',
    'power_threshold_w':0.1,'power_interval_s':30,'energy_threshold_wh':1,'energy_interval_s':60
}

def _env_bool(name,default=False):
    v=os.getenv(name)
    if v is None:return default
    return v.strip().lower() in {'1','true','yes','on','y'}

class SecretBox:
    def __init__(self,data_dir:Path):
        self.path=data_dir/'ha_secret.key';self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists():
            key=Fernet.generate_key()
            try:
                fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as f:f.write(key+b'\n')
            except FileExistsError:pass
        try:os.chmod(self.path,0o600)
        except OSError:pass
        key=self.path.read_bytes().strip();self.fernet=Fernet(key)
    def encrypt(self,value:str)->str:return self.fernet.encrypt(value.encode('utf-8')).decode('ascii')
    def decrypt(self,token:str)->str:
        try:return self.fernet.decrypt(token.encode('ascii')).decode('utf-8')
        except (InvalidToken,UnicodeError,ValueError) as e:raise RuntimeError('HA MQTT password 복호화 실패') from e

class HAController:
    DEVICE_OFFLINE_GRACE=30.0
    def __init__(self,store,manager,data_dir:Path):
        self.store=store;self.manager=manager;self.secret=SecretBox(data_dir);self.client=None;self.connected=False
        self.lock=threading.RLock();self.last_error=None;self.connected_since=None;self._generation=0;self._offline_timers={};self._last_state={}
    def _get(self,key,default=None):
        v=self.store.setting_get('ha.'+key)
        return default if v is None else v
    def bootstrap_from_env(self)->bool:
        """Seed HA MQTT settings once from Docker env without overriding later Web-UI edits."""
        if self.store.setting_get('ha.bootstrap_done')=='1':return False
        # Environment is an install-time bootstrap only. Existing persistent Web-UI settings always win.
        if any(self.store.setting_get('ha.'+k) is not None for k in ('enabled','host','port','username','password_enc','tls','topic_prefix','discovery_prefix','birth_topic')):
            self.store.setting_set('ha.bootstrap_done','1');return False
        host=(os.getenv('HA_MQTT_HOST') or '').strip()
        if not host:return False
        def env_nonempty(name):
            v=os.getenv(name);return None if v is None or v=='' else v
        password=env_nonempty('HA_MQTT_PASSWORD')
        password_file=env_nonempty('HA_MQTT_PASSWORD_FILE')
        if password_file:
            password=Path(password_file).read_text(encoding='utf-8').rstrip('\r\n')
        body={'host':host,'enabled':_env_bool('HA_MQTT_ENABLED',True)}
        v=env_nonempty('HA_MQTT_PORT')
        if v is not None:body['port']=int(v)
        v=os.getenv('HA_MQTT_USERNAME')
        if v is not None:body['username']=v
        if password not in (None,''):body['password']=password
        v=env_nonempty('HA_MQTT_TLS')
        if v is not None:body['tls']=_env_bool('HA_MQTT_TLS')
        for key,env_name in [('topic_prefix','HA_MQTT_TOPIC_PREFIX'),('discovery_prefix','HA_MQTT_DISCOVERY_PREFIX'),('birth_topic','HA_MQTT_BIRTH_TOPIC')]:
            v=env_nonempty(env_name)
            if v is not None:body[key]=v
        # Persist through the same validation/encryption path used by Web UI, but do not start a client yet.
        old_reconfigure=self.reconfigure
        self.reconfigure=lambda:None
        try:self.save(body)
        finally:self.reconfigure=old_reconfigure
        self.store.setting_set('ha.bootstrap_done','1')
        log.info('HA MQTT settings bootstrapped from environment; future restarts use persisted /data settings')
        return True
    def config(self,include_password=False)->dict:
        c=dict(DEFAULTS)
        c['enabled']=self._get('enabled','0')=='1';c['host']=self._get('host','');c['port']=int(self._get('port','1883') or 1883)
        c['username']=self._get('username','');c['tls']=self._get('tls','0')=='1';c['topic_prefix']=(self._get('topic_prefix','mttl') or 'mttl').strip('/')
        c['discovery_prefix']=(self._get('discovery_prefix','homeassistant') or 'homeassistant').strip('/');c['birth_topic']=(self._get('birth_topic','homeassistant/status') or 'homeassistant/status').strip('/')
        c['power_threshold_w']=float(self._get('power_threshold_w','0.1') or 0.1);c['power_interval_s']=int(self._get('power_interval_s','30') or 30);c['energy_threshold_wh']=int(self._get('energy_threshold_wh','1') or 1);c['energy_interval_s']=int(self._get('energy_interval_s','60') or 60)
        token=self._get('password_enc','');c['password_set']=bool(token)
        if include_password:c['password']=self.secret.decrypt(token) if token else ''
        return c
    def public_config(self)->dict:
        c=self.config(False);c.update({'connected':self.connected,'last_error':self.last_error,'connected_since':self.connected_since});return c
    @staticmethod
    def _validate_topic(v,name):
        v=str(v or '').strip().strip('/')
        if not v or '+' in v or '#' in v or '\x00' in v:raise ValueError(f'{name}가 올바르지 않습니다')
        return v
    def save(self,body:dict)->dict:
        old=self.config(False)
        enabled=bool(body['enabled']) if 'enabled' in body else old['enabled'];host=str(body.get('host',old['host'])).strip();port=int(body.get('port',old['port']));username=str(body.get('username',old['username']))
        tls=bool(body['tls']) if 'tls' in body else old['tls'];topic=self._validate_topic(body.get('topic_prefix',old['topic_prefix']),'topic_prefix');disc=self._validate_topic(body.get('discovery_prefix',old['discovery_prefix']),'discovery_prefix');birth=self._validate_topic(body.get('birth_topic',old['birth_topic']),'birth_topic')
        pth=float(body.get('power_threshold_w',old['power_threshold_w']));pint=int(body.get('power_interval_s',old['power_interval_s']));eth=int(body.get('energy_threshold_wh',old['energy_threshold_wh']));eint=int(body.get('energy_interval_s',old['energy_interval_s']))
        if pth not in {0.1,0.2,0.3,0.5,1.0}:raise ValueError('Power 변화량은 0.1/0.2/0.3/0.5/1W 중 하나여야 합니다')
        if pint not in {15,30,60,120,300,600,1800} or eint not in {15,30,60,120,300,600,1800}:raise ValueError('HA 보고 주기가 올바르지 않습니다')
        if eth not in {1,2,5,10,20}:raise ValueError('Energy 변화량은 1/2/5/10/20Wh 중 하나여야 합니다')
        if enabled and not host:raise ValueError('HA MQTT broker 주소가 필요합니다')
        if port<1 or port>65535:raise ValueError('MQTT port는 1~65535여야 합니다')
        # Clear retained discovery/state on the currently connected broker before disabling or moving
        # the integration. Once reconfigure() stops the client, those retained topics can no longer be removed.
        old_endpoint=(old['host'],old['port'],old['topic_prefix'],old['discovery_prefix'],old['birth_topic'])
        new_endpoint=(host,port,topic,disc,birth)
        if old['enabled'] and (not enabled or old_endpoint!=new_endpoint):self._clear_all_retained(old)
        vals={'enabled':'1' if enabled else '0','host':host,'port':str(port),'username':username,'tls':'1' if tls else '0','topic_prefix':topic,'discovery_prefix':disc,'birth_topic':birth,'power_threshold_w':str(pth),'power_interval_s':str(pint),'energy_threshold_wh':str(eth),'energy_interval_s':str(eint)}
        persisted={'ha.'+k:v for k,v in vals.items()};deletes=[]
        if body.get('clear_password'):deletes.append('ha.password_enc')
        elif 'password' in body and body.get('password') not in (None,''):persisted['ha.password_enc']=self.secret.encrypt(str(body['password']))
        self.store.setting_apply(persisted,deletes)
        self.reconfigure();return self.public_config()
    @staticmethod
    def bridge_client_id(config):
        # Stable per logical bridge/topic namespace.  A reconnect takes over any half-open
        # predecessor session before the new bridge is exposed online, preventing a late old LWT.
        digest=hashlib.sha256(str(config.get('topic_prefix') or 'mttl').encode('utf-8')).hexdigest()[:12]
        return 'mttl-up-ha-'+digest

    def _new_client(self,client_id):
        try:return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=client_id,protocol=mqtt.MQTTv311)
        except AttributeError:return mqtt.Client(client_id=client_id,protocol=mqtt.MQTTv311)
    @staticmethod
    def _reason_failed(reason)->bool:
        try:return bool(reason.is_failure)
        except Exception:
            try:return int(reason)!=0
            except Exception:return False
    def _cancel_device_offline(self,mac):
        with self.lock:t=self._offline_timers.pop(mac,None)
        if t:t.cancel()
    def device_connected(self,mac):
        self._cancel_device_offline(mac)
    def _publish_device_offline_now(self,mac):
        with self.lock:self._offline_timers.pop(mac,None)
        d=self.store.device(mac)
        if not d or d.get('removed') or d.get('online'):return False
        ok=self._pub(f'{self.device_base(mac)}/availability','offline',True)
        if ok:log.info('HA device unavailable published mac=%s after %.0fs grace',mac,self.DEVICE_OFFLINE_GRACE)
        return ok
    def device_disconnected(self,mac):
        d=self.store.device(mac)
        if not d or d.get('removed') or d.get('online'):return False
        with self.lock:
            old=self._offline_timers.get(mac)
            if old and old.is_alive():return True
            t=threading.Timer(self.DEVICE_OFFLINE_GRACE,self._publish_device_offline_now,args=(mac,));t.daemon=True;self._offline_timers[mac]=t;t.start()
        log.info('HA device offline grace started mac=%s grace=%.0fs',mac,self.DEVICE_OFFLINE_GRACE)
        return True
    def _retained_topics(self,mac,c=None):
        c=c or self.config(False);base=self.device_base(mac,c)
        topics=[self.discovery_topic(mac,c),f'{base}/availability',f'{base}/outlet/0/state',f'{base}/power/total_w',f'{base}/energy/total_kwh',f'{base}/diagnostic/voltage_v',f'{base}/diagnostic/rssi_dbm']
        for n in range(1,5):topics.extend([f'{base}/outlet/{n}/state',f'{base}/power/{n}_w',f'{base}/energy/{n}_kwh',f'{base}/temperature/{n}_c'])
        topics.append(f'{base}/childlock/availability')
        for bit in range(5):topics.append(f'{base}/childlock/{bit}/state')
        return topics
    def _clear_all_retained(self,c=None,wait_timeout=2.0):
        c=c or self.config(False)
        with self.lock:client=self.client;connected=self.connected
        if not connected or client is None:return False
        rows=[];seen=set()
        for d in [*self.store.devices(),*self.store.removed_devices()]:
            mac=d.get('mac')
            if not mac or mac in seen:continue
            seen.add(mac);rows.extend(self._retained_topics(mac,c))
        rows.append(self.bridge_topic(c))
        last=None
        try:
            for topic in rows:last=client.publish(topic,'',qos=1,retain=True)
            if last is not None:last.wait_for_publish(wait_timeout)
            log.info('HA retained discovery/state cleared devices=%s before reconfigure/disable',len(seen))
            return True
        except Exception as e:
            log.warning('HA retained cleanup failed before reconfigure/disable: %s',e);return False
    def remove_device(self,mac):
        self._cancel_device_offline(mac)
        if not self.connected:return False
        # Retained deletion and the local suppression cache must move together. Otherwise a later
        # restore can see an old cached value and incorrectly skip republishing an empty broker topic.
        ok=True
        for topic in self._retained_topics(mac):ok=self._clear_retained_topic(topic) and ok
        log.info('HA discovery/state removed mac=%s',mac)
        return ok
    def _stop_locked(self):
        timers=list(self._offline_timers.values());self._offline_timers.clear()
        for t in timers:t.cancel()
        c=self.client;self.client=None;self.connected=False;self.connected_since=None;self._last_state.clear()
        if c:
            try:c.publish(self.bridge_topic(),payload='offline',qos=1,retain=True).wait_for_publish(1)
            except Exception:pass
            try:c.disconnect()
            except Exception:pass
            try:c.loop_stop()
            except Exception:pass
    def stop(self):
        with self.lock:self._generation+=1;self._stop_locked()
    def reconfigure(self):
        with self.lock:
            self._generation+=1;generation=self._generation;self._stop_locked();self.last_error=None;c=self.config(True)
            if not c['enabled']:return
            client=self._new_client(self.bridge_client_id(c));self.client=client
            if c['username'] or c['password']:client.username_pw_set(c['username'] or None,c['password'] or None)
            if c['tls']:client.tls_set()
            client.will_set(self.bridge_topic(c),'offline',qos=1,retain=True)
            client.on_connect=lambda cl,ud,flags,reason,*rest:self._on_connect(generation,cl,reason)
            client.on_disconnect=lambda cl,ud,*rest:self._on_disconnect(generation,cl,rest)
            client.on_message=lambda cl,ud,msg:self._on_message(generation,msg)
            try:client.connect_async(c['host'],c['port'],60);client.loop_start()
            except Exception as e:self.last_error=str(e);log.warning('HA MQTT start failed: %s',e)
    def _on_connect(self,generation,client,reason):
        with self.lock:
            if generation!=self._generation or client is not self.client:return
            failed=self._reason_failed(reason);self.connected=not failed
            if failed:self.last_error=f'MQTT CONNACK: {reason}';return
            self.last_error=None;self.connected_since=time.time();c=self.config(False);self._last_state.clear()
            client.subscribe(c['birth_topic'],qos=0);client.subscribe(f"{c['topic_prefix']}/+/outlet/+/set",qos=1);client.subscribe(f"{c['topic_prefix']}/+/childlock/+/set",qos=1)
        # Keep bridge availability offline while refreshing retained discovery/state. This
        # prevents HA from briefly exposing stale retained values during broker reconnect.
        self.sync_all(False)
        with self.lock:
            if generation==self._generation and client is self.client and self.connected:
                client.publish(self.bridge_topic(c),'online',qos=1,retain=True)
    def _on_disconnect(self,generation,client,args):
        with self.lock:
            if generation!=self._generation or client is not self.client:return
            self.connected=False;self.connected_since=None
            if args:self.last_error=f'MQTT disconnected: {args[-2] if len(args)>=2 else args[-1]}'
    def _on_message(self,generation,msg):
        if generation!=self._generation:return
        c=self.config(False)
        if msg.topic==c['birth_topic']:
            if msg.payload.decode('utf-8','ignore').strip().lower()=='online':
                # Gate stale retained values while Home Assistant rebuilds MQTT entities.
                self._pub(self.bridge_topic(c),'offline');self.sync_all(False);self._pub(self.bridge_topic(c),'online')
            return
        # A retained actuator command is historical desired state, not a new user action.
        # Replaying it after backend/MQTT reconnect could override a later physical change.
        if getattr(msg,'retain',False):
            log.warning('ignoring retained HA command topic=%s',getattr(msg,'topic','?'))
            return
        prefix=c['topic_prefix'].split('/');parts=msg.topic.split('/')
        if parts[:len(prefix)]!=prefix:return
        p=parts[len(prefix):];raw=msg.payload.decode('utf-8','ignore').strip().upper()
        if raw not in {'ON','OFF','1','0','TRUE','FALSE'}:return
        on=raw in {'ON','1','TRUE'}
        try:
            if len(p)!=4 or p[3]!='set':return
            mac=p[0]
            if not re.fullmatch(r'[0-9a-fA-F]{12}',mac):return
            mac=mac.lower()
            if p[1]=='outlet' and p[2] in {'0','1','2','3','4'}:
                selector=int(p[2]);kind='outlet';fut=asyncio.run_coroutine_threadsafe(self.manager.power(mac,selector,on,source='ha'),self.manager.loop)
            elif p[1]=='childlock' and p[2] in {'0','1','2','3','4'}:
                selector=int(p[2]);kind='childlock';fut=asyncio.run_coroutine_threadsafe(self.manager.childlock_bit(mac,selector,on,source='ha'),self.manager.loop)
            else:return
            def done(f):
                try:
                    result=f.result()
                    if isinstance(result,dict) and not result.get('ok'):
                        log.warning('HA %s command not confirmed mac=%s selector=%s state=%s result=%s',kind,mac,selector,result.get('state'),result)
                except Exception as e:log.warning('HA %s command failed mac=%s selector=%s: %s',kind,mac,selector,e)
                self.publish_device(mac)
            fut.add_done_callback(done)
        except Exception as e:log.warning('HA command dispatch error: %s',e)
    def bridge_topic(self,c=None):
        c=c or self.config(False);return f"{c['topic_prefix']}/bridge/availability"
    def device_base(self,mac,c=None):c=c or self.config(False);return f"{c['topic_prefix']}/{mac}"
    def discovery_topic(self,mac,c=None):c=c or self.config(False);return f"{c['discovery_prefix']}/device/mttl_{mac}/config"
    def _diag(self,mac):
        try:
            if self.manager and hasattr(self.manager,'diagnostic_view'):return self.manager.diagnostic_view(mac)
        except Exception:pass
        return {'voltage_enabled':False,'rssi_enabled':False,'poll_interval':60}
    def configuration_url(self,mac):
        root=(os.getenv('MTTL_PUBLIC_WEB_URL') or '').strip().rstrip('/')
        if not root:return None
        if not re.match(r'^https?://',root,re.I):
            log.warning('ignoring invalid MTTL_PUBLIC_WEB_URL=%r',root);return None
        return f'{root}/#device={mac}'
    def discovery_payload(self,mac):
        c=self.config(False);base=self.device_base(mac,c);d=self.store.device(mac) or {};cmps={};diag=self._diag(mac)
        # Selector 0 is the firmware master command path. Expose it as HA 'All'
        # control while keeping physical outlets 1..4 as separate controls.
        cmps['switch_all']={'p':'switch','name':'All','unique_id':f'mttl_{mac}_switch_all','command_topic':f'{base}/outlet/0/set','state_topic':f'{base}/outlet/0/state','payload_on':'ON','payload_off':'OFF','icon':'mdi:power-plug'}
        cmps['power_total']={'p':'sensor','name':'Total Power','unique_id':f'mttl_{mac}_power_total','state_topic':f'{base}/power/total_w','device_class':'power','state_class':'measurement','unit_of_measurement':'W','suggested_display_precision':3}
        cmps['energy_total']={'p':'sensor','name':'Total Energy','unique_id':f'mttl_{mac}_energy_total','state_topic':f'{base}/energy/total_kwh','device_class':'energy','state_class':'total_increasing','unit_of_measurement':'kWh','suggested_display_precision':3}
        channel_names={int(ch.get('selector')):(str(ch.get('name') or '').strip() or f"Outlet {int(ch.get('selector'))}") for ch in d.get('channels',[]) if str(ch.get('selector','')).isdigit() and 1<=int(ch.get('selector'))<=4}
        device_name=d.get('alias') or f'MTTL-W01 {mac[-6:].upper()}'
        childlock_supported=bool(self.manager and hasattr(self.manager,'childlock_supported') and self.manager.childlock_supported(d))
        if childlock_supported:
            lock_names=['ALL Button Lock']+[f'{channel_names.get(n,f"Outlet {n}")} Button Lock' for n in range(1,5)]
            lock_availability=[{'topic':f'{base}/availability'},{'topic':self.bridge_topic(c)},{'topic':f'{base}/childlock/availability'}]
            for bit,name in enumerate(lock_names):
                cmps[f'childlock_{bit}']={'p':'switch','name':name,'unique_id':f'mttl_{mac}_childlock_{bit}','command_topic':f'{base}/childlock/{bit}/set','state_topic':f'{base}/childlock/{bit}/state','payload_on':'ON','payload_off':'OFF','icon':'mdi:lock','entity_category':'config','availability':lock_availability,'availability_mode':'all'}
        for n in range(1,5):
            name=channel_names.get(n,f'Outlet {n}')
            cmps[f'switch_{n}']={'p':'switch','name':name,'unique_id':f'mttl_{mac}_switch_{n}','command_topic':f'{base}/outlet/{n}/set','state_topic':f'{base}/outlet/{n}/state','payload_on':'ON','payload_off':'OFF','icon':'mdi:power-socket-de'}
            cmps[f'power_{n}']={'p':'sensor','name':f'{name} Power','unique_id':f'mttl_{mac}_power_{n}','state_topic':f'{base}/power/{n}_w','device_class':'power','state_class':'measurement','unit_of_measurement':'W','suggested_display_precision':3}
            cmps[f'energy_{n}']={'p':'sensor','name':f'{name} Energy','unique_id':f'mttl_{mac}_energy_{n}','state_topic':f'{base}/energy/{n}_kwh','device_class':'energy','state_class':'total_increasing','unit_of_measurement':'kWh','suggested_display_precision':3}
        for n in range(1,5):
            name=channel_names.get(n,f'Outlet {n}')
            cmps[f'temperature_{n}']={'p':'sensor','name':f'{name} Temperature','unique_id':f'mttl_{mac}_temperature_{n}','state_topic':f'{base}/temperature/{n}_c','device_class':'temperature','state_class':'measurement','unit_of_measurement':'°C','suggested_display_precision':0}
        if diag.get('voltage_enabled'):
            cmps['voltage']={'p':'sensor','name':'Voltage','unique_id':f'mttl_{mac}_voltage','state_topic':f'{base}/diagnostic/voltage_v','device_class':'voltage','state_class':'measurement','unit_of_measurement':'V','suggested_display_precision':1}
        if diag.get('rssi_enabled'):
            cmps['rssi']={'p':'sensor','name':'Wi-Fi RSSI','unique_id':f'mttl_{mac}_rssi','state_topic':f'{base}/diagnostic/rssi_dbm','device_class':'signal_strength','state_class':'measurement','unit_of_measurement':'dBm','entity_category':'diagnostic'}
        cmps['backend_bridge']={'p':'binary_sensor','name':'Backend HA Bridge','unique_id':f'mttl_{mac}_backend_bridge','state_topic':self.bridge_topic(c),'payload_on':'online','payload_off':'offline','device_class':'connectivity','entity_category':'diagnostic'}
        device={'identifiers':[f'mttl_{mac}'],'name':device_name,'manufacturer':'LG U+ / Tonly','model':'MTTL-W01','serial_number':mac,'sw_version':d.get('firmware_version') or d.get('soft_version')}
        configuration_url=self.configuration_url(mac)
        if configuration_url:device['configuration_url']=configuration_url
        return {'device':device,'origin':{'name':'MTTL-W01 UP Backend','sw_version':'0.2.11'},'availability':[{'topic':f'{base}/availability'},{'topic':self.bridge_topic(c)}],'availability_mode':'all','components':cmps}
    def _pub(self,topic,payload,retain=True):
        with self.lock:c=self.client;ok=self.connected and c is not None
        if not ok:return False
        try:
            info=c.publish(topic,payload,qos=1,retain=retain)
            rc=getattr(info,'rc',mqtt.MQTT_ERR_SUCCESS)
            if rc!=mqtt.MQTT_ERR_SUCCESS:
                log.warning('HA MQTT publish rejected topic=%s rc=%s',topic,rc);return False
            return True
        except Exception as e:
            log.warning('HA MQTT publish failed topic=%s: %s',topic,e);return False
    def _clear_retained_topic(self,topic):
        ok=self._pub(topic,'',True)
        # Forget even when the broker publish failed: the next live sample must be attempted again
        # rather than being suppressed by a value that no longer has a trustworthy broker copy.
        with self.lock:self._last_state.pop(topic,None)
        return ok
    def _pub_changed(self,topic,payload,kind='state',numeric=None,threshold=0.0,min_interval=0,force=False,max_interval=None):
        t=time.monotonic();old=self._last_state.get(topic)
        if not force and old is not None:
            old_payload,old_num,old_t=old;elapsed=t-old_t
            if kind=='state' and str(payload)==str(old_payload):return False
            if kind=='numeric':
                delta=abs(float(numeric)-float(old_num)) if old_num is not None else float('inf');due=max_interval is not None and elapsed>=max_interval
                if not due and (elapsed<min_interval or delta+1e-12<threshold):return False
        if self._pub(topic,payload,True):self._last_state[topic]=(str(payload),numeric,t);return True
        return False
    def publish_discovery(self,mac):
        if not self.connected:return False
        d=self.store.device(mac)
        if not d or d.get('removed'):return False
        ok=self._pub(self.discovery_topic(mac),json.dumps(self.discovery_payload(mac),ensure_ascii=False,separators=(',',':')),True)
        supported=bool(self.manager and hasattr(self.manager,'childlock_supported') and self.manager.childlock_supported(d))
        if not supported:
            base=self.device_base(mac);self._clear_retained_topic(f'{base}/childlock/availability')
            for bit in range(5):self._clear_retained_topic(f'{base}/childlock/{bit}/state')
        return ok
    def publish_childlock_availability(self,mac,force=False):
        if not self.connected:return False
        d=self.store.device(mac)
        if not d or d.get('removed'):return False
        supported=bool(self.manager and hasattr(self.manager,'childlock_supported') and self.manager.childlock_supported(d))
        if not supported:return self._clear_retained_topic(f'{self.device_base(mac)}/childlock/availability')
        synced=bool(self.manager and hasattr(self.manager,'childlock_synced') and self.manager.childlock_synced(d))
        return self._pub_changed(f'{self.device_base(mac)}/childlock/availability','online' if synced else 'offline',force=force)
    def publish_device(self,mac,force=False):
        if not self.connected:return False
        d=self.store.device(mac)
        if not d or d.get('removed'):return False
        if not d.get('online'):
            self.device_disconnected(mac);return True
        self.device_connected(mac);c=self.config(False);base=self.device_base(mac,c)
        # Availability is deliberately not suppression-cached. The reference mttl_w01 bridge also
        # republishes availability every cycle, making retained broker state self-healing after a
        # power-cycle, delayed LWT or prior retained cleanup.
        self._pub(f'{base}/availability','online',True);outlets=[]
        for ch in d.get('channels',[]):
            n=int(ch['selector'])
            if not 1<=n<=4:continue
            outlets.append(ch)
            if ch.get('on_state') is not None:self._pub_changed(f'{base}/outlet/{n}/state','ON' if ch.get('on_state') else 'OFF',force=force)
            if ch.get('power_mw') is not None:
                v=float(ch['power_mw'])/1000.0;self._pub_changed(f'{base}/power/{n}_w',f'{v:.3f}','numeric',v,c['power_threshold_w'],c['power_interval_s'],force)
            if ch.get('energy_lifetime_wh') is not None:
                wh=float(ch['energy_lifetime_wh']);self._pub_changed(f'{base}/energy/{n}_kwh',f'{wh/1000.0:.6f}','numeric',wh,c['energy_threshold_wh'],c['energy_interval_s'],force)
            if ch.get('temperature_c') is not None:self._pub_changed(f'{base}/temperature/{n}_c',str(ch['temperature_c']),force=force)
        if len(outlets)==4 and all(x.get('on_state') is not None for x in outlets):
            self._pub_changed(f'{base}/outlet/0/state','ON' if all(bool(x.get('on_state')) for x in outlets) else 'OFF',force=force)
        if self.manager and hasattr(self.manager,'childlock_supported') and self.manager.childlock_supported(d):
            self.publish_childlock_availability(mac,force=force)
            if hasattr(self.manager,'childlock_synced') and self.manager.childlock_synced(d):
                mask=d.get('childlock_mask')
                if type(mask) is int and mask in range(32):
                    for bit in range(5):self._pub_changed(f'{base}/childlock/{bit}/state','ON' if mask&(1<<bit) else 'OFF',force=force)
        if len(outlets)==4 and all(x.get('power_mw') is not None for x in outlets):
            v=sum(float(x['power_mw']) for x in outlets)/1000.0;self._pub_changed(f'{base}/power/total_w',f'{v:.3f}','numeric',v,c['power_threshold_w'],c['power_interval_s'],force)
        if len(outlets)==4 and all(x.get('energy_lifetime_wh') is not None for x in outlets):
            wh=sum(float(x['energy_lifetime_wh']) for x in outlets);self._pub_changed(f'{base}/energy/total_kwh',f'{wh/1000.0:.6f}','numeric',wh,c['energy_threshold_wh'],c['energy_interval_s'],force)
        return True
    def publish_diagnostics(self,mac,diag=None,force=False):
        if not self.connected:return False
        d=self.store.device(mac)
        if not d or d.get('removed') or not d.get('online'):return False
        diag=diag or self._diag(mac);base=self.device_base(mac)
        if diag.get('voltage_enabled') and diag.get('voltage_mv') is not None:
            v=float(diag['voltage_mv'])/1000.0;self._pub_changed(f'{base}/diagnostic/voltage_v',f'{v:.3f}','numeric',v,0.1,0,force)
        if diag.get('rssi_enabled') and diag.get('rssi_dbm') is not None:
            v=int(diag['rssi_dbm']);self._pub_changed(f'{base}/diagnostic/rssi_dbm',str(v),'numeric',v,10,0,force,300)
        return True
    def sync_diagnostic_config(self,mac,diag=None):
        if not self.connected:return False
        diag=diag or self._diag(mac);base=self.device_base(mac)
        if not diag.get('voltage_enabled'):self._clear_retained_topic(f'{base}/diagnostic/voltage_v')
        if not diag.get('rssi_enabled'):self._clear_retained_topic(f'{base}/diagnostic/rssi_dbm')
        # Re-enabling a diagnostic sensor must republish even when the sampled value is identical
        # to the value that existed before its retained topic was cleared.
        self.publish_diagnostics(mac,diag,True);return True
    def sync_all(self,publish_bridge=True):
        if not self.connected:return False
        # Refresh every retained discovery/state value first.  Only then open the bridge
        # availability gate so Home Assistant cannot expose a stale retained snapshot.
        for d in self.store.removed_devices():self.remove_device(d['mac'])
        for d in self.store.devices():self.publish_discovery(d['mac']);self.publish_device(d['mac'],True);self.publish_diagnostics(d['mac'],self._diag(d['mac']),True)
        if publish_bridge:self._pub(self.bridge_topic(),'online')
        return True
    def test_connection(self,override=None,timeout=5.0):
        saved=self.config(True);x=dict(saved);override=override or {}
        for k in ('host','port','username','tls'):
            if k in override and override[k] not in (None,''):x[k]=override[k]
        if override.get('password') not in (None,''):x['password']=str(override['password'])
        x['port']=int(x['port']);x['host']=str(x['host']).strip()
        if not x['host']:raise ValueError('HA MQTT broker 주소가 필요합니다')
        ev=threading.Event();result={'ok':False,'error':'timeout'};client=self._new_client('mttl-up-ha-test-'+uuid.uuid4().hex[:8])
        if x.get('username') or x.get('password'):client.username_pw_set(x.get('username') or None,x.get('password') or None)
        if x.get('tls'):client.tls_set()
        def on_connect(cl,ud,flags,reason,*rest):
            result['ok']=not self._reason_failed(reason);result['error']=None if result['ok'] else f'CONNACK {reason}';ev.set()
        client.on_connect=on_connect
        try:
            client.connect_async(x['host'],x['port'],30);client.loop_start();ev.wait(timeout)
        except Exception as e:result={'ok':False,'error':str(e)}
        finally:
            try:client.disconnect()
            except Exception:pass
            try:client.loop_stop()
            except Exception:pass
        return result
