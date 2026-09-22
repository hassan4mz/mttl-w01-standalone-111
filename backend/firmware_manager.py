from __future__ import annotations
import asyncio,socket,logging,os,re,threading,time,uuid,ipaddress
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from firmware_patch import (
    SRC_SHA,FWR66_SHA,FWR110_SHA,FWR111_SHA,
    build_from_source,inspect_wire,prepare_source,sha,
)

log=logging.getLogger('mttl-up')
PROFILE_112_LEGACY='official-112'
PROFILE_112_LG='official-112-lg'  # legacy migration id only
PROFILE_112_LOCAL='official-112-local'
PROFILE_113='official-113'
PROFILE_111='official-111'
PROFILE_110='official-110'
PROFILE_66='official-66'
VERSION_RE=re.compile(r'^(?:1\.0\.)?(\d+)$')

def canonical_version(v):
    if v is None:return None
    s=str(v).strip()
    m=VERSION_RE.fullmatch(s)
    return f'1.0.{int(m.group(1))}' if m else s

def version_number(v):
    m=VERSION_RE.fullmatch(str(v or '').strip())
    return int(m.group(1)) if m else None

def now():return time.time()

class FirmwareError(RuntimeError):pass

class FirmwareStore:
    """Validated firmware profiles + sequential OTA queue coordinator.

    Binary FWR files live under data/firmware. SQLite keeps only metadata/jobs.
    /ota.bin is exposed only while one job is active and only to the selected device peer IP.
    """
    ACK_TIMEOUT=5.0
    HTTP_GET_TIMEOUT=20.0
    HTTP_TRANSFER_TIMEOUT=30.0
    RECONNECT_TIMEOUT=120.0
    VERIFY_TIMEOUT=15.0
    MAX_FWR_BYTES=1024*1024
    def __init__(self,store,data_dir:Path):
        self.store=store;self.data_dir=Path(data_dir);self.dir=self.data_dir/'firmware';self.dir.mkdir(parents=True,exist_ok=True)
        self.source_dir=self.data_dir/'firmware-source';self.source_dir.mkdir(parents=True,exist_ok=True)
        try:
            os.chmod(self.dir,0o700);os.chmod(self.source_dir,0o700)
        except OSError:pass
        self._migrate_local_source_cache()
        self.manager=None;self.loop=None;self.queue=None;self.worker_task=None
        self._active_lock=threading.RLock();self._active=None
        self._reconnect_events={};self._generation={}
        if not self.store.setting_get('firmware.ota_host_ip') and os.getenv('OTA_HOST_IP','').strip():self.set_host_ip(os.getenv('OTA_HOST_IP').strip())
        self._sync_official_metadata()
        # Canonical 1.0.111 is rebuilt from exact hardware-verified 1.0.110.
        # Experimental 112/113/114 identities never become active official targets.
        target=self.store.setting_get('firmware.target_profile')
        if not target or target in (PROFILE_113,PROFILE_112_LEGACY,PROFILE_112_LOCAL,PROFILE_112_LG):
            self.store.setting_set('firmware.target_profile',PROFILE_111)
        self.store.setting_set('firmware.release_channel_version','111-canonical-ota49217')
    def _migrate_local_source_cache(self):
        """Move/copy only exact known source FWRs into /data/firmware-source.

        This is a local runtime migration; Docker/release artifacts never contain FWR bytes.
        """
        known=((self.dir/'MTTL-W01_V1.0.105.fwr','MTTL-W01_V1.0.105.fwr',SRC_SHA),
               (self.dir/'official_1.0.66.fwr','comMTTL-W01_1.0.66_LGU.fwr',FWR66_SHA))
        for old,name,expected in known:
            dst=self.source_dir/name
            try:
                if old.is_file() and sha(old.read_bytes())==expected:
                    if not dst.is_file():dst.write_bytes(old.read_bytes());os.chmod(dst,0o600)
                    # 1.0.105 is source-only and should not remain in the generated runtime directory.
                    if old.name=='MTTL-W01_V1.0.105.fwr':old.unlink(missing_ok=True)
            except OSError:pass

    def _sync_official_metadata(self):
        self.store.firmware_profile_upsert(PROFILE_111,'official','1.0.111','UP + Child Lock · 1.0.111',FWR111_SHA,327944,'LGU+-TAP-HW002','official_1.0.111.fwr')
        self.store.firmware_profile_upsert(PROFILE_110,'official','1.0.110','UP · 1.0.110',FWR110_SHA,327944,'LGU+-TAP-HW002','official_1.0.110.fwr')
        self.store.firmware_profile_upsert(PROFILE_66,'official','1.0.66','LG U+ Stock · 1.0.66',FWR66_SHA,327944,'LGU+-TAP-HW002','official_1.0.66.fwr')
        # Keep only active official profiles in the operational store. Legacy IDs remain
        # recognized above for migration/rejection, but their metadata/binaries belong in re/ history.
        for pid in (PROFILE_113,PROFILE_112_LEGACY,PROFILE_112_LOCAL,PROFILE_112_LG):
            self.store.firmware_profile_delete_inactive(pid)
        for filename in ('official_1.0.112.fwr','official_1.0.113.fwr','official_1.0.114.fwr'):
            (self.dir/filename).unlink(missing_ok=True)
    def start(self,loop,manager):
        self.loop=loop;self.manager=manager;self.queue=asyncio.Queue()
        for jid,old,new in self.store.ota_reconcile_startup():log.warning('OTA startup reconcile job=%s %s -> %s',jid,old,new)
        self.worker_task=asyncio.create_task(self._worker(),name='ota-worker')
    async def stop(self):
        if not self.worker_task:return
        self.worker_task.cancel()
        try:await self.worker_task
        except asyncio.CancelledError:pass
        self.worker_task=None
    def target_profile_id(self):
        x=self.store.setting_get('firmware.target_profile') or PROFILE_111
        if x in (PROFILE_113,PROFILE_112_LEGACY,PROFILE_112_LOCAL,PROFILE_112_LG):x=PROFILE_111
        return x if self.store.firmware_profile(x) else PROFILE_111
    def set_target_profile(self,profile_id):
        if profile_id in (PROFILE_113,PROFILE_112_LEGACY,PROFILE_112_LOCAL,PROFILE_112_LG):raise FirmwareError('experimental/withdrawn firmware cannot be selected; active official targets are 1.0.111, 1.0.110 and 1.0.66')
        p=self.store.firmware_profile(profile_id)
        if not p:raise FirmwareError('firmware profile not found')
        self.store.setting_set('firmware.target_profile',profile_id);return p
    def _profile_path(self,p):
        fn=p.get('filename') if isinstance(p,dict) else p['filename']
        return self.dir/fn if fn else None
    def profile_ready(self,p):
        pid=p.get('id') if isinstance(p,dict) else p['id']
        path=self._profile_path(p)
        if not path or not path.is_file():return False
        try:
            b=path.read_bytes();meta=inspect_wire(b)
            return meta['sha256']==p['sha256'] and meta['size']==int(p['size']) and canonical_version(meta.get('version'))==canonical_version(p['version'])
        except Exception:return False
    def profiles(self):
        out=[]
        for row in self.store.firmware_profiles():
            p=dict(row)
            if p['id'] in (PROFILE_113,PROFILE_112_LEGACY,PROFILE_112_LOCAL,PROFILE_112_LG):continue
            p['ready']=self.profile_ready(p);p['selected']=p['id']==self.target_profile_id()
            out.append(p)
        official_order={PROFILE_111:0,PROFILE_110:1,PROFILE_66:2}
        out.sort(key=lambda p:(0,official_order.get(p['id'],99)) if p.get('kind')=='official' else (1,str(p.get('label') or p['id'])))
        return out
    def status(self):
        jobs=self.store.ota_jobs(limit=30)
        with self._active_lock:active=dict(self._active['public']) if self._active else None
        return {'target_profile':self.target_profile_id(),'profiles':self.profiles(),'active':active,'jobs':jobs,'queue_depth':self.queue.qsize() if self.queue else 0,'ota_host_ip':self.store.setting_get('firmware.ota_host_ip') or '','release':'1.0.111-canonical','release_status':'HARDWARE_OTA_BOOT_VERIFIED','ota_primary_port':49217,'ota_fallback_port':80}
    def set_host_ip(self,ip):
        import ipaddress
        try:x=str(ipaddress.ip_address(str(ip).strip()))
        except Exception:raise FirmwareError('OTA 서버 IP는 유효한 IPv4/IPv6 주소여야 합니다')
        if ':' in x:raise FirmwareError('현재 MTTL OTA는 IPv4 주소만 지원합니다')
        self.store.setting_set('firmware.ota_host_ip',x);return x
    def _official_filename(self,pid):
        return {PROFILE_111:'official_1.0.111.fwr',PROFILE_110:'official_1.0.110.fwr',PROFILE_66:'official_1.0.66.fwr'}[pid]
    def prepare_official(self,pid):
        if pid not in (PROFILE_111,PROFILE_110,PROFILE_66):raise FirmwareError('experimental/withdrawn firmware cannot be prepared; active targets are 1.0.111, 1.0.110 and 1.0.66')
        p=dict(self.store.firmware_profile(pid));path=self.dir/self._official_filename(pid)
        if path.is_file():
            try:
                b=path.read_bytes();meta=inspect_wire(b)
                if meta['sha256']==p['sha256']:
                    if p.get('filename')!=path.name:self.store.firmware_profile_set_filename(pid,path.name)
                    return dict(self.store.firmware_profile(pid))
            except Exception:pass
            path.unlink(missing_ok=True)
        try:
            kind,source=prepare_source(self.source_dir)
            version={PROFILE_111:'111',PROFILE_110:'110',PROFILE_66:'66'}[pid]
            wire=build_from_source(source.read_bytes(),version);meta=inspect_wire(wire)
        except Exception as exc:
            raise FirmwareError(
                '펌웨어 source 준비 실패: '+str(exc)+
                ' / 자동 다운로드가 안 되면 사용자가 확보한 정확한 1.0.105 또는 LG U+ 1.0.66 FWR을 '+str(self.source_dir)+' 에 넣고 다시 준비/검증하세요.'
            ) from exc
        if meta['sha256']!=p['sha256']:raise FirmwareError('generated official firmware SHA mismatch')
        path.write_bytes(wire);os.chmod(path,0o600);self.store.firmware_profile_set_filename(pid,path.name)
        log.info('firmware prepared locally pid=%s source=%s file=%s sha=%s',pid,kind,source.name,meta['sha256'])
        return dict(self.store.firmware_profile(pid))
    def validate_custom(self,wire:bytes,filename='custom.fwr'):
        if not wire or len(wire)>self.MAX_FWR_BYTES:raise FirmwareError('FWR 크기가 허용 범위를 벗어났습니다')
        try:meta=inspect_wire(wire)
        except Exception as e:raise FirmwareError(f'FWR 검증 실패: {e}')
        if meta['model']!='LGU+-TAP-HW002':raise FirmwareError('지원하지 않는 모델입니다')
        version=canonical_version(meta.get('version'))
        if not version:raise FirmwareError('펌웨어 버전을 판별할 수 없습니다')
        return {**meta,'version':version,'original_filename':Path(filename or 'custom.fwr').name}
    def add_custom(self,wire:bytes,filename='custom.fwr'):
        meta=self.validate_custom(wire,filename);pid='custom-'+meta['sha256'][:16];fn=pid+'.fwr';path=self.dir/fn
        path.write_bytes(wire);os.chmod(path,0o600)
        label=f"사용자 지정 {meta['version']}"
        self.store.firmware_profile_upsert(pid,'custom',meta['version'],label,meta['sha256'],meta['size'],meta['model'],fn,meta['original_filename'])
        return dict(self.store.firmware_profile(pid))
    def delete_custom(self,pid):
        p=self.store.firmware_profile(pid)
        if not p:raise FirmwareError('firmware profile not found')
        if p['kind']!='custom':raise FirmwareError('official firmware cannot be deleted')
        if self.target_profile_id()==pid:self.store.setting_set('firmware.target_profile',PROFILE_111)
        path=self._profile_path(p)
        if path:path.unlink(missing_ok=True)
        self.store.firmware_profile_delete(pid)
    def ensure_ready(self,pid):
        p=self.store.firmware_profile(pid)
        if not p:raise FirmwareError('firmware profile not found')
        if p['kind']=='official':
            if pid not in (PROFILE_111,PROFILE_110,PROFILE_66):raise FirmwareError('official firmware is withdrawn/inactive; only 1.0.111, 1.0.110 and 1.0.66 are active')
            if not self.profile_ready(p):p=self.prepare_official(pid)
        if not self.profile_ready(p):raise FirmwareError('선택한 펌웨어 파일이 준비되지 않았거나 검증에 실패했습니다')
        return dict(self.store.firmware_profile(pid))
    def download_profile(self,pid):
        p=self.store.firmware_profile(pid)
        if not p:raise FirmwareError('firmware profile not found')
        p=dict(p)
        if p['kind']=='official' and pid not in (PROFILE_111,PROFILE_110,PROFILE_66):raise FirmwareError('official firmware is withdrawn/inactive; only 1.0.111, 1.0.110 and 1.0.66 are downloadable')
        if not self.profile_ready(p):raise FirmwareError('펌웨어 파일이 준비/검증되지 않았습니다')
        path=self._profile_path(p)
        if not path or path.resolve().parent!=self.dir.resolve():raise FirmwareError('invalid firmware path')
        if pid==PROFILE_111:name='comMTTL-W01_1.0.111_UP_CHILDLOCK_OTA49217.fwr'
        elif pid==PROFILE_110:name='comMTTL-W01_1.0.110_UP.fwr'
        elif pid==PROFILE_66:name='comMTTL-W01_1.0.66_LGU.fwr'
        else:
            raw=Path(p.get('original_filename') or f'{pid}.fwr').name
            name=re.sub(r'[^A-Za-z0-9._-]+','_',raw).strip('._') or f'{pid}.fwr'
            if not name.lower().endswith('.fwr'):name+='.fwr'
        return {'profile':p,'path':path,'filename':name}
    def decorate_device(self,d):
        if not d:return d
        z=dict(d);pid=self.target_profile_id();p=self.store.firmware_profile(pid);target=canonical_version(p['version']) if p else None;current=canonical_version(z.get('firmware_version'))
        z['firmware_target']={'profile_id':pid,'version':target,'label':p['label'] if p else None,'kind':p['kind'] if p else None,'ready':self.profile_ready(p) if p else False}
        applied=self.store.ota_latest_applied_for_mac(z['mac'])
        same_version=bool(target and current==target)
        exact_build=bool(same_version and applied and canonical_version(applied.get('target_version'))==target and p and applied.get('firmware_sha256')==p.get('sha256'))
        z['firmware_current_normalized']=current;z['firmware_build_current']=exact_build
        if not target:z['firmware_action']=None
        elif same_version:
            # Same-version reflash is offered only when OTA history proves a different build SHA.
            z['firmware_action']='reflash' if applied and canonical_version(applied.get('target_version'))==target and p and applied.get('firmware_sha256')!=p.get('sha256') else None
        else:z['firmware_action']='downgrade' if version_number(current) is not None and version_number(target) is not None and version_number(current)>version_number(target) else 'update'
        z['ota_job']=self.store.ota_latest_for_mac(z['mac']);z['ota_applied']=applied
        return z
    def queue_job(self,mac,pid=None):
        if not self.loop or not self.queue:raise FirmwareError('OTA worker is not running')
        d=self.store.device(mac)
        if not d or d.get('removed'):raise FirmwareError('device removed or not found')
        if not d.get('online'):raise FirmwareError('장치가 오프라인입니다')
        pid=pid or self.target_profile_id();p=self.store.firmware_profile(pid)
        if not p:raise FirmwareError('firmware profile not found')
        if p['kind']=='official' and pid not in (PROFILE_111,PROFILE_110,PROFILE_66):raise FirmwareError('official firmware is withdrawn/inactive; only 1.0.111, 1.0.110 and 1.0.66 may be queued')
        # A MAC may have only one non-terminal job.
        existing=self.store.ota_active_for_mac(mac)
        if existing:return existing
        jid=uuid.uuid4().hex;job=self.store.ota_job_create(jid,mac,canonical_version(d.get('firmware_version')),pid,canonical_version(p['version']),p['sha256'])
        self.loop.call_soon_threadsafe(self.queue.put_nowait,jid);return job
    def queue_many(self,macs,pid=None):
        out=[]
        for mac in macs:
            try:out.append(self.queue_job(mac,pid))
            except FirmwareError as e:out.append({'mac':mac,'error':str(e)})
        return out
    def on_identified(self,mac):
        self._generation[mac]=self._generation.get(mac,0)+1
        ev=self._reconnect_events.get(mac)
        if ev:ev.set()
        # A previously result-unknown job is reconciled asynchronously after the new session is fully registered.
        j=self.store.ota_latest_for_mac(mac)
        if j and j.get('state')=='RESULT_UNKNOWN' and self.loop:
            self.loop.create_task(self._verify_delayed(mac,j['id']),name='ota-delayed-verify-'+mac)
    async def _verify_delayed(self,mac,jid):
        await asyncio.sleep(0.5)
        j=self.store.ota_job(jid);d=self.store.device(mac)
        if not j or j.get('state')!='RESULT_UNKNOWN' or not d:return
        if canonical_version(d.get('firmware_version'))!=canonical_version(j.get('target_version')):return
        s=await self.manager.current(mac)
        if not s:return
        try:await asyncio.wait_for(self.manager.request_getinfo(s),self.VERIFY_TIMEOUT)
        except Exception as e:
            log.info('delayed OTA verification pending job=%s mac=%s err=%s',jid,mac,e);return
        self._transition(jid,'DELAYED_SUCCESS','장치가 늦게 재연결되어 목표 버전과 GETINFO를 확인했습니다',completed=True)
    def _http_source_allowed(self,source_ip,peer_ip):
        try:
            src=ipaddress.ip_address(source_ip); peer=ipaddress.ip_address(peer_ip)
        except ValueError:return False
        if src==peer:return True
        host=self.store.setting_get('firmware.ota_host_ip') or ''
        try:
            ota=ipaddress.ip_address(host)
            if ota.version!=4:return False
            lan=ipaddress.ip_network(f'{ota}/24',strict=False)
            first_host=next(lan.hosts())
        except (ValueError,StopIteration):return False
        # UP can be SNATed by the LAN router while the OTA HTTP GET goes directly to
        # the backend. In that case UP peer is conventionally the first LAN host/gateway,
        # while HTTP carries the device's real LAN source. Keep the exception narrowly
        # scoped to the configured OTA server's /24 and only the first-host gateway peer.
        return bool(peer==first_host and src.is_private and src in lan)
    def active_http_request(self,source_ip,path):
        with self._active_lock:
            a=self._active
            if not a or path!='/ota.bin':return None
            if not self._http_source_allowed(source_ip,a['peer_ip']):
                log.warning('OTA HTTP source rejected job=%s mac=%s source=%s up_peer=%s ota_host=%s',a['job_id'],a['public']['mac'],source_ip,a['peer_ip'],self.store.setting_get('firmware.ota_host_ip') or '')
                return None
            if source_ip!=a['peer_ip']:
                log.info('OTA HTTP source accepted via gateway-NAT exception job=%s mac=%s source=%s up_peer=%s',a['job_id'],a['public']['mac'],source_ip,a['peer_ip'])
            a['http_started'].set();return {'path':a['path'],'sha256':a['sha256'],'job_id':a['job_id']}
    def active_http_complete(self,job_id,bytes_sent,error=None):
        with self._active_lock:
            a=self._active
            if not a or a['job_id']!=job_id:return
            a['bytes_sent']=bytes_sent;a['http_error']=error;a['http_done'].set()
    def _activate_http(self,job,p,path,peer_ip):
        with self._active_lock:
            self._active={'job_id':job['id'],'peer_ip':peer_ip,'path':str(path),'sha256':p['sha256'],'http_started':threading.Event(),'http_done':threading.Event(),'bytes_sent':0,'http_error':None,'public':{'job_id':job['id'],'mac':job['mac'],'state':'WAITING_HTTP','target_version':job['target_version']}}
            return self._active
    def _deactivate_http(self,jid):
        with self._active_lock:
            if self._active and self._active['job_id']==jid:self._active=None
    def _transition(self,jid,state,detail=None,completed=False):
        log.info('ota job=%s state=%s detail=%s',jid,state,detail or '')
        return self.store.ota_job_update(jid,state,detail,completed=completed)
    async def _wait_ota_acceptance(self,s,active,timeout=None):
        # Firmware revisions do not always emit the UP ota_ok line before switching into
        # HTTP download mode.  A peer-authenticated /ota.bin GET is stronger evidence that
        # the command was accepted, so race the uncorrelated UP ACK against HTTP start.
        # Never retransmit the mutating command after an ambiguous result.
        timeout=self.HTTP_GET_TIMEOUT if timeout is None else float(timeout)
        deadline=asyncio.get_running_loop().time()+timeout
        while True:
            if active['http_started'].is_set():return 'http',None
            try:ack=s.ota_q.get_nowait()
            except asyncio.QueueEmpty:ack=None
            if ack is not None:return 'ack',ack
            remain=deadline-asyncio.get_running_loop().time()
            if remain<=0:return None,None
            await asyncio.sleep(min(0.05,remain))
    async def _worker(self):
        while True:
            jid=await self.queue.get()
            try:await self._run_job(jid)
            except asyncio.CancelledError:raise
            except Exception as e:
                log.exception('OTA worker failed job=%s',jid);self._transition(jid,'FAILED_INTERNAL',str(e),completed=True)
            finally:self.queue.task_done();self._deactivate_http(jid)
    async def _run_job(self,jid):
        job=self.store.ota_job(jid)
        if not job:return
        mac=job['mac'];pid=job['profile_id']
        self._transition(jid,'PREPARING','펌웨어 검증/준비 중')
        try:p=await asyncio.to_thread(self.ensure_ready,pid)
        except Exception as e:return self._transition(jid,'FAILED_PREPARE',str(e),completed=True)
        host=self.store.setting_get('firmware.ota_host_ip') or ''
        if not host:return self._transition(jid,'FAILED_CONFIG','설정에서 OTA 서버 IP를 먼저 지정하세요',completed=True)
        d=self.store.device(mac);s=await self.manager.current(mac)
        if not d or not s:return self._transition(jid,'FAILED_OFFLINE','장치가 OTA 시작 전에 오프라인이 되었습니다',completed=True)
        peer_ip=s.peer_ip;base_generation=self._generation.get(mac,0);path=self._profile_path(p);active=self._activate_http(job,p,path,peer_ip)
        # One device transaction lock is held only through command ACK. After ACK the device may close TCP while downloading/rebooting.
        try:await asyncio.wait_for(s.lock.acquire(),5.0)
        except asyncio.TimeoutError:return self._transition(jid,'FAILED_BUSY','장치 명령 처리 중이라 OTA를 시작하지 못했습니다',completed=True)
        try:
            if s.closed.is_set() or await self.manager.current(mac) is not s:return self._transition(jid,'FAILED_OFFLINE','device session replaced before OTA command',completed=True)
            while not s.ota_q.empty():s.ota_q.get_nowait()
            self._transition(jid,'COMMAND_SENT',f'up:ota:{host}')
            try:await s.send(f'up:ota:{host}')
            except Exception as e:
                self.manager.invalidate(s,'OTA command send error')
                return self._transition(jid,'RESULT_UNKNOWN',f'OTA 명령 전송 결과 불명: {e}; 자동 재전송하지 않음',completed=True)
            source,ack=await self._wait_ota_acceptance(s,active)
            if source is None:
                # No transaction ID exists on this stream. Fence it so a very late ota_ok
                # cannot be mistaken for a future OTA, but do not ever retransmit here.
                self.manager.invalidate(s,'OTA acceptance timeout')
                return self._transition(jid,'RESULT_UNKNOWN','OTA ACK 및 /ota.bin 요청이 제한시간 내 확인되지 않았습니다; 명령 재전송하지 않음',completed=True)
            if source=='ack':
                if ack!='ok':return self._transition(jid,'FAILED_ACK',str(ack),completed=True)
                self._transition(jid,'ACKED','장치가 OTA 서버 주소를 수락했습니다')
            else:
                self._transition(jid,'ACKED','장치의 /ota.bin 요청으로 OTA 수락을 확인했습니다 (UP ACK 생략/지연)')
        finally:
            if s.lock.locked():s.lock.release()
        started=active['http_started'].is_set() or await asyncio.to_thread(active['http_started'].wait,self.HTTP_GET_TIMEOUT)
        if not started:return self._transition(jid,'FAILED_DOWNLOAD','장치가 /ota.bin을 요청하지 않았습니다',completed=True)
        self._transition(jid,'DOWNLOADING','장치가 /ota.bin 다운로드를 시작했습니다')
        done=await asyncio.to_thread(active['http_done'].wait,self.HTTP_TRANSFER_TIMEOUT)
        if not done:return self._transition(jid,'RESULT_UNKNOWN','HTTP 전송 완료 여부를 확인하지 못했습니다',completed=True)
        if active['http_error']:return self._transition(jid,'RESULT_UNKNOWN','HTTP 전송 오류: '+active['http_error'],completed=True)
        if active['bytes_sent']!=int(p['size']):return self._transition(jid,'RESULT_UNKNOWN',f"부분 전송 {active['bytes_sent']}/{p['size']}",completed=True)
        self._transition(jid,'TRANSFERRED',f"{active['bytes_sent']} bytes 전송 완료")
        # Only reconnects that happen AFTER the full HTTP transfer can prove an OTA reboot.
        # A GETINFO timeout/reconnect while the file is still downloading must never be
        # mistaken for the post-flash reboot. Some firmware also reconnects once with the
        # old version before it actually commits/reboots, so keep waiting within the
        # reconnect window instead of failing on the first old-version session.
        base=self._generation.get(mac,0);target=canonical_version(p['version']);deadline=asyncio.get_running_loop().time()+self.RECONNECT_TIMEOUT
        last_actual=None;seen_post_transfer=False
        self._transition(jid,'RECONNECTING','전송 완료 후 OTA 재부팅/TCP10086 재접속 대기')
        while True:
            remain=deadline-asyncio.get_running_loop().time()
            if remain<=0:
                if seen_post_transfer:return self._transition(jid,'FAILED_VERSION',f'재부팅 대기시간 동안 목표 {target} 미확인; 마지막 버전 {last_actual or "unknown"}',completed=True)
                return self._transition(jid,'RESULT_UNKNOWN','전송 완료 후 재부팅/재접속 제한시간 초과; 나중에 재접속 시 다시 판정',completed=True)
            ev=asyncio.Event();self._reconnect_events[mac]=ev
            try:
                if self._generation.get(mac,0)<=base:
                    try:await asyncio.wait_for(ev.wait(),remain)
                    except asyncio.TimeoutError:
                        if seen_post_transfer:return self._transition(jid,'FAILED_VERSION',f'재부팅 대기시간 동안 목표 {target} 미확인; 마지막 버전 {last_actual or "unknown"}',completed=True)
                        return self._transition(jid,'RESULT_UNKNOWN','전송 완료 후 재부팅/재접속 제한시간 초과; 나중에 재접속 시 다시 판정',completed=True)
            finally:
                if self._reconnect_events.get(mac) is ev:self._reconnect_events.pop(mac,None)
            gen=self._generation.get(mac,0)
            if gen<=base:continue
            base=gen;seen_post_transfer=True
            ns=await self.manager.current(mac);nd=self.store.device(mac)
            if not ns or not nd:continue
            actual=canonical_version(nd.get('firmware_version'));last_actual=actual
            self._transition(jid,'VERIFYING',f'재접속 버전 확인: {actual or "?"} / 목표 {target}')
            if actual!=target:
                self._transition(jid,'RECONNECTING',f'아직 {actual or "unknown"}; OTA 적용/재부팅 추가 대기')
                continue
            try:await asyncio.wait_for(self.manager.request_getinfo(ns),self.VERIFY_TIMEOUT)
            except Exception as e:return self._transition(jid,'RESULT_UNKNOWN',f'버전은 일치하지만 GETINFO 확인 실패: {e}',completed=True)
            return self._transition(jid,'SUCCESS',f'FW {target} 및 GETINFO 확인 완료',completed=True)

class OTAHTTPHandler(BaseHTTPRequestHandler):
    server_version='MTTL-OTA/1.0'
    def log_message(self,fmt,*args):log.debug('OTA HTTP '+fmt,*args)
    def do_GET(self):
        src=str(self.client_address[0]);info=self.server.firmware.active_http_request(src,urlparse(self.path).path)
        if not info:
            self.send_response(404);self.send_header('Content-Length','0');self.end_headers();return
        path=Path(info['path']);sent=0;err=None
        try:
            size=path.stat().st_size
            self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(size));self.send_header('Connection','close');self.end_headers()
            with path.open('rb') as f:
                while True:
                    b=f.read(65536)
                    if not b:break
                    self.wfile.write(b);sent+=len(b)
            self.wfile.flush();log.info('OTA HTTP complete job=%s peer=%s bytes=%d',info['job_id'],src,sent)
        except Exception as e:
            err=str(e);log.warning('OTA HTTP error job=%s peer=%s sent=%d err=%s',info['job_id'],src,sent,e)
        finally:self.server.firmware.active_http_complete(info['job_id'],sent,err)

class OTAHTTPServer(ThreadingHTTPServer):
    daemon_threads=True;allow_reuse_address=True
    def __init__(self,addr,firmware):self.firmware=firmware;super().__init__(addr,OTAHTTPHandler)
