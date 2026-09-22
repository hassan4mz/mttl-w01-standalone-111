#!/usr/bin/env python3
from __future__ import annotations
import argparse,asyncio,base64,concurrent.futures,hashlib,hmac,json,logging,logging.handlers,os,re,secrets,socket,sqlite3,struct,threading,time,uuid
from collections import deque
from dataclasses import dataclass,field
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from ha_bridge import HAController
from backup_manager import BackupManager
from firmware_manager import FirmwareStore,OTAHTTPServer,version_number

BACKEND_VERSION='0.2.11'
DEFAULT_POLL=15
ALLOWED_POLL=(10,15,30,60,120,300)
ALLOWED_DIAG_POLL=(30,60,120,300,600,1800)
OTA_TERMINAL_STATES=('SUCCESS','DELAYED_SUCCESS','CANCELLED_RESTART','FAILED_PREPARE','FAILED_CONFIG','FAILED_OFFLINE','FAILED_BUSY','FAILED_ACK','FAILED_DOWNLOAD','FAILED_VERSION','FAILED_INTERNAL','RESULT_UNKNOWN')
EVENT_RE=re.compile(r'^up:event:onoff:([0-4]):(on|off)$',re.I)
ONOFF_RE=re.compile(r'^up:onoff:([1-4]):(on|off)$',re.I)
CHILDLOCK_RE=re.compile(r'^up:(?:childlock|cl):(\d{1,3})$',re.I)
POWER_REPORT_RE=re.compile(r'^(?:up:)?power_report:([1-5]):(-?\d+)$',re.I)
MAC_RE=re.compile(r'(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])|(?<![0-9A-Fa-f])[0-9A-Fa-f]{12}(?![0-9A-Fa-f])')
GETINFO_RE=re.compile(
 r'(?P<channel>[1-5]):(?P<runtime>-?\d+);(?P<relay>on|off);(?P<state>-?\d+);'
 r'(?P<overload>[^;:]+);(?P<overheat>[^;:]+);(?P<power>-?\d+);'
 r'(?P<energy>[0-9A-Fa-f]{8});(?P<previous>[0-9A-Fa-f]{8});'
 r'(?P<configuration>[0-9A-Fa-f]{8});(?P<status>[^;:]+);'
 r'(?P<event>[0-9A-Fa-f]{2});(?P<temperature>-?\d+)(?::|$)',re.I)
log=logging.getLogger('mttl-up')

class UILogHandler(logging.Handler):
 def __init__(self,max_rows=5000):
  super().__init__(logging.DEBUG);self.rows=deque(maxlen=max_rows);self.lock_rows=threading.RLock();self.seq=0
 @staticmethod
 def _safe(msg):
  msg=re.sub(r'(up:connect:[^:\s]+:)[^\r\n\s]+',r'\1<password-redacted>',str(msg))
  msg=re.sub(r'(?i)(password[=:]\s*)[^\s,}]+',r'\1<redacted>',msg)
  return msg
 @staticmethod
 def _category(msg):
  x=msg.lower()
  if 'ota ' in x or 'ota_' in x or 'firmware' in x:return 'ota'
  if x.startswith(('tx ','rx ')) or ' wire=' in x:return 'wire'
  if 'childlock' in x or 'child lock' in x:return 'childlock'
  if any(k in x for k in ('control ','onoff','relay','selector=')):return 'control'
  if any(k in x for k in ('mqtt','home assistant',' ha ','ha ')):return 'ha'
  if any(k in x for k in ('connect peer','identified ','disconnect','session','offline grace')):return 'connection'
  if any(k in x for k in ('backup','database','db ','sqlite','storage','maintenance')):return 'storage'
  return 'other'
 def emit(self,record):
  try:
   msg=self._safe(record.getMessage());cat=self._category(msg)
   with self.lock_rows:
    self.seq+=1;self.rows.append({'seq':self.seq,'ts':record.created,'level':record.levelname,'logger':record.name,'category':cat,'message':msg,'thread':record.threadName})
  except Exception:self.handleError(record)
 def snapshot(self,min_level='DEBUG',category='all',limit=300):
  lv=getattr(logging,str(min_level).upper(),logging.DEBUG);limit=max(1,min(1000,int(limit)))
  with self.lock_rows:rows=list(self.rows)
  rows=[r for r in rows if getattr(logging,r['level'],0)>=lv and (category=='all' or r['category']==category)]
  return rows[-limit:]
 def clear(self):
  with self.lock_rows:self.rows.clear()

UI_LOG=UILogHandler()

def now():return time.time()
def clean_mac(v:str)->str:return re.sub(r'[^0-9A-Fa-f]','',v).lower()
def find_mac(line:str)->str|None:
 for m in MAC_RE.finditer(line):
  x=clean_mac(m.group(0))
  if len(x)==12:return x
 return None

def queue_latest(q:asyncio.Queue,item):
 # Bound response queues so malformed/unsolicited traffic cannot grow memory forever.
 while q.full():
  try:q.get_nowait()
  except asyncio.QueueEmpty:break
 q.put_nowait(item)


def tcp_info_snapshot(writer):
 # Linux TCP_INFO is read-only transport telemetry.  It does not alter socket state.
 # The first 104 bytes are stable through tcpi_total_retrans on Linux 3.x+.
 try:
  sock=writer.get_extra_info('socket')
  if sock is None or not hasattr(socket,'TCP_INFO'):return None
  raw=sock.getsockopt(socket.IPPROTO_TCP,socket.TCP_INFO,104)
  if len(raw)<104:return None
  head=struct.unpack_from('=8B24I',raw)
  u=head[8:]
  return {
   'state':int(head[0]),'retransmits':int(head[2]),
   'rto_us':int(u[0]),'unacked':int(u[4]),'lost':int(u[6]),'retrans':int(u[7]),
   'last_data_sent_ms':int(u[9]),'last_data_recv_ms':int(u[11]),'last_ack_recv_ms':int(u[12]),
   'rtt_us':int(u[15]),'rttvar_us':int(u[16]),'snd_cwnd':int(u[18]),'total_retrans':int(u[23]),
  }
 except (AttributeError,OSError,ValueError,struct.error,TypeError):return None

def tcp_info_timeout_class(before,after):
 if not before or not after:return 'unknown',None
 delta=max(0,int(after.get('total_retrans',0))-int(before.get('total_retrans',0)))
 if int(after.get('unacked',0))==0:
  return ('acked_no_retrans' if delta==0 else 'acked_after_retrans'),delta
 return 'unacked_at_timeout',delta

def bootinfo_versions(line:str)->tuple[str|None,str|None]:
 try:
  body=line.split('up:bootinfo:',1)[1];parts=body.split(';');v=parts[3].strip() if len(parts)>=4 else ''
  if '-' in v:
   soft,fw=v.split('-',1);return soft.strip() or None,fw.strip() or None
  return v or None,None
 except Exception:return None,None

def parse_getinfo(line:str)->list[dict]:
 text=line.strip('\r\n\0 ')
 if text.startswith('up:getinfo:'):text=text[len('up:getinfo:'):]
 out=[]
 for m in GETINFO_RE.finditer(text):
  g=m.groupdict();ch=int(g['channel'])
  if ch==5:continue  # aggregate/master is not a physical outlet and is intentionally not stored
  out.append({'selector':ch,'runtime':int(g['runtime']),
   'on':g['relay'].lower()=='on','state':int(g['state']),'power_mw':int(g['power']),
   'energy_raw_wh':int(g['energy'],16),'previous_raw_wh':int(g['previous'],16),
   'configuration':g['configuration'].upper(),'temperature_c':int(g['temperature']),
   'overload':g['overload'],'overheat':g['overheat'],'status':g['status'],'event_code':g['event'].upper()})
 return out

class AuthManager:
 ITERATIONS=240000;SESSION_TTL=365*86400
 def __init__(self,store):
  self.store=store;self.lock=threading.RLock()
  if not self.store.setting_get('auth.password_hash'):
   self.store.setting_set_many({'auth.password_hash':self._hash('admin'),'auth.must_change':'1'})
  self.store.auth_session_prune(now())
 @classmethod
 def _hash(cls,password):
  salt=os.urandom(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,cls.ITERATIONS)
  enc=lambda b:base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')
  return f'pbkdf2_sha256${cls.ITERATIONS}${enc(salt)}${enc(digest)}'
 @staticmethod
 def _b64(v):return base64.urlsafe_b64decode(v+'='*((4-len(v)%4)%4))
 def verify(self,password):
  try:
   alg,it,salt,digest=(self.store.setting_get('auth.password_hash') or '').split('$',3)
   if alg!='pbkdf2_sha256':return False
   got=hashlib.pbkdf2_hmac('sha256',str(password).encode('utf-8'),self._b64(salt),int(it))
   return hmac.compare_digest(got,self._b64(digest))
  except Exception:return False
 @staticmethod
 def _token_hash(token):return hashlib.sha256(str(token).encode('utf-8')).hexdigest()
 def _prune(self):self.store.auth_session_prune(now())
 def new_session(self):
  with self.lock:
   self._prune();token=secrets.token_urlsafe(32);t=now();self.store.auth_session_create(self._token_hash(token),t+self.SESSION_TTL,t);return token
 def valid(self,token):
  if not token:return False
  with self.lock:return self.store.auth_session_valid(self._token_hash(token),now())
 def logout(self,token):
  if token:
   with self.lock:self.store.auth_session_delete(self._token_hash(token))
 def status(self,token=None):
  ok=self.valid(token);return {'authenticated':ok,'must_change_password':bool(ok and self.store.setting_get('auth.must_change')=='1')}
 def login(self,password):
  if not self.verify(password):return None
  return self.new_session()
 def change_password(self,current,new):
  if not self.verify(current):raise ValueError('현재 관리자 암호가 올바르지 않습니다')
  new=str(new or '')
  if len(new)<8:raise ValueError('새 관리자 암호는 8자 이상이어야 합니다')
  if new=='admin':raise ValueError('기본 암호 admin은 다시 사용할 수 없습니다')
  self.store.setting_set_many({'auth.password_hash':self._hash(new),'auth.must_change':'0'})
  with self.lock:self.store.auth_session_clear()
  return self.new_session()

class Store:
 EVENT_RETENTION_DAYS=30
 EVENT_MAX_ROWS=10000
 def __init__(self,path:Path):
  self.path=path;path.parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.lock=threading.RLock();self.runtime_devices={};self.runtime_channels={};self.energy_dirty_since={};self.init();self.maintenance(truncate=True)
 def init(self):
  with self.lock:
   self.db.executescript('''
   PRAGMA journal_mode=WAL;
   PRAGMA synchronous=FULL;
   PRAGMA busy_timeout=5000;
   PRAGMA wal_autocheckpoint=256;
   PRAGMA journal_size_limit=1048576;
   CREATE TABLE IF NOT EXISTS devices(mac TEXT PRIMARY KEY,peer_ip TEXT,session_id TEXT,bootinfo TEXT,soft_version TEXT,firmware_version TEXT,childlock_mask INTEGER,online INTEGER NOT NULL DEFAULT 0,last_seen REAL,first_seen REAL,alias TEXT,removed_at REAL);
   CREATE TABLE IF NOT EXISTS channels(mac TEXT NOT NULL,selector INTEGER NOT NULL,name TEXT,on_state INTEGER,power_mw INTEGER,temperature_c INTEGER,energy_raw_wh INTEGER,energy_lifetime_wh INTEGER NOT NULL DEFAULT 0,energy_highwater_wh INTEGER,energy_epoch INTEGER NOT NULL DEFAULT 0,configuration TEXT,runtime INTEGER,state_code INTEGER,previous_raw_wh INTEGER,overload TEXT,overheat TEXT,status TEXT,event_code TEXT,last_seen REAL,PRIMARY KEY(mac,selector));
   CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,mac TEXT,kind TEXT NOT NULL,detail TEXT);
   CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
   CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,expires_at REAL NOT NULL,created_at REAL NOT NULL);
   CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);
   CREATE TABLE IF NOT EXISTS firmware_profiles(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('official','custom')),version TEXT,label TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,model TEXT NOT NULL,filename TEXT,original_filename TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
   CREATE TABLE IF NOT EXISTS ota_jobs(id TEXT PRIMARY KEY,mac TEXT NOT NULL,from_version TEXT,profile_id TEXT NOT NULL,target_version TEXT,firmware_sha256 TEXT NOT NULL,state TEXT NOT NULL,detail TEXT,queued_at REAL NOT NULL,started_at REAL,updated_at REAL NOT NULL,completed_at REAL);
   CREATE INDEX IF NOT EXISTS idx_ota_jobs_mac_queued ON ota_jobs(mac,queued_at DESC);
   ''')
   cols={r[1] for r in self.db.execute('PRAGMA table_info(devices)')}
   if 'firmware_version' not in cols:self.db.execute('ALTER TABLE devices ADD COLUMN firmware_version TEXT')
   if 'soft_version' not in cols:self.db.execute('ALTER TABLE devices ADD COLUMN soft_version TEXT')
   if 'alias' not in cols:self.db.execute('ALTER TABLE devices ADD COLUMN alias TEXT')
   if 'removed_at' not in cols:self.db.execute('ALTER TABLE devices ADD COLUMN removed_at REAL')
   if 'childlock_mask' not in cols:self.db.execute('ALTER TABLE devices ADD COLUMN childlock_mask INTEGER')
   ccols={r[1] for r in self.db.execute('PRAGMA table_info(channels)')}
   if 'name' not in ccols:self.db.execute('ALTER TABLE channels ADD COLUMN name TEXT')
   for name,kind in [('runtime','INTEGER'),('state_code','INTEGER'),('previous_raw_wh','INTEGER'),('overload','TEXT'),('overheat','TEXT'),('status','TEXT'),('event_code','TEXT')]:
    if name not in ccols:self.db.execute(f'ALTER TABLE channels ADD COLUMN {name} {kind}')
   self.db.execute('UPDATE devices SET online=0,session_id=NULL,childlock_mask=NULL')
   self.db.commit()
 def maintenance(self,truncate=False):
  cutoff=now()-self.EVENT_RETENTION_DAYS*86400
  with self.lock:
   self.db.execute('DELETE FROM events WHERE ts<?',(cutoff,))
   n=self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
   if n>self.EVENT_MAX_ROWS:
    drop=n-self.EVENT_MAX_ROWS;self.db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)',(drop,))
   self.db.commit()
   try:self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)' if truncate else 'PRAGMA wal_checkpoint(PASSIVE)').fetchall()
   except sqlite3.OperationalError:pass
   try:self.db.execute('PRAGMA optimize')
   except sqlite3.OperationalError:pass
 def storage_stats(self):
  with self.lock:
   events=self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
   devices=self.db.execute('SELECT COUNT(*) FROM devices WHERE removed_at IS NULL').fetchone()[0]
   removed=self.db.execute('SELECT COUNT(*) FROM devices WHERE removed_at IS NOT NULL').fetchone()[0]
   pages=self.db.execute('PRAGMA page_count').fetchone()[0];page_size=self.db.execute('PRAGMA page_size').fetchone()[0];free=self.db.execute('PRAGMA freelist_count').fetchone()[0]
  wal=Path(str(self.path)+'-wal')
  return {'db_bytes':self.path.stat().st_size if self.path.exists() else 0,'wal_bytes':wal.stat().st_size if wal.exists() else 0,'page_bytes':pages*page_size,'free_pages':free,'events':events,'active_devices':devices,'removed_devices':removed,'event_retention_days':self.EVENT_RETENTION_DAYS,'event_max_rows':self.EVENT_MAX_ROWS}
 def event(self,mac,kind,detail=''):
  t=now()
  with self.lock:
   self.db.execute('INSERT INTO events(ts,mac,kind,detail) VALUES(?,?,?,?)',(t,mac,str(kind),str(detail)));self.db.commit()
  return t
 def setting_get(self,key):
  with self.lock:
   r=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone();return None if not r else r['value']
 def setting_set(self,key,value):
  with self.lock:self.db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)));self.db.commit()
 def setting_set_many(self,values):
  with self.lock:
   self.db.executemany('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',[(str(k),str(v)) for k,v in values.items()]);self.db.commit()
 def setting_apply(self,values,deletes=()):
  # Apply a logically related configuration generation atomically. This avoids mixed
  # old/new settings after sudden power loss in the middle of a multi-field update.
  with self.lock:
   self.db.executemany('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',[(str(k),str(v)) for k,v in values.items()])
   if deletes:self.db.executemany('DELETE FROM settings WHERE key=?',[(str(k),) for k in deletes])
   self.db.commit()
 def setting_delete(self,key):
  with self.lock:self.db.execute('DELETE FROM settings WHERE key=?',(key,));self.db.commit()
 def auth_session_create(self,token_hash,expires_at,created_at):
  with self.lock:
   self.db.execute('INSERT OR REPLACE INTO auth_sessions(token_hash,expires_at,created_at) VALUES(?,?,?)',(str(token_hash),float(expires_at),float(created_at)));self.db.commit()
 def auth_session_valid(self,token_hash,t):
  with self.lock:
   r=self.db.execute('SELECT expires_at FROM auth_sessions WHERE token_hash=?',(str(token_hash),)).fetchone()
   if not r:return False
   if float(r['expires_at'])<=float(t):
    self.db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(str(token_hash),));self.db.commit();return False
   return True
 def auth_session_delete(self,token_hash):
  with self.lock:self.db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(str(token_hash),));self.db.commit()
 def auth_session_clear(self):
  with self.lock:self.db.execute('DELETE FROM auth_sessions');self.db.commit()
 def auth_session_prune(self,t):
  with self.lock:self.db.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(float(t),));self.db.commit()
 def identify(self,mac,peer,session,boot):
  t=now();soft,fw=bootinfo_versions(boot)
  with self.lock:
   self.db.execute('INSERT INTO devices(mac,peer_ip,session_id,bootinfo,soft_version,firmware_version,online,last_seen,first_seen) VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(mac) DO UPDATE SET peer_ip=excluded.peer_ip,session_id=excluded.session_id,bootinfo=excluded.bootinfo,soft_version=COALESCE(excluded.soft_version,devices.soft_version),firmware_version=COALESCE(excluded.firmware_version,devices.firmware_version),online=1,last_seen=excluded.last_seen',(mac,peer,session,boot,soft,fw,t,t))
   self.db.execute('INSERT INTO events(ts,mac,kind,detail) VALUES(?,?,?,?)',(t,mac,'connect',peer));self.db.commit();rt={'peer_ip':peer,'session_id':session,'bootinfo':boot,'online':1,'last_seen':t};rt.update({'soft_version':soft} if soft else {});rt.update({'firmware_version':fw} if fw else {});self.runtime_devices[mac]=rt
 def set_soft_version(self,mac,value):
  with self.lock:self.db.execute('UPDATE devices SET soft_version=?,last_seen=? WHERE mac=?',(value,now(),mac));self.db.commit()
 def set_childlock(self,mac,mask):
  # Child Lock is session/runtime state. Firmware flash is authoritative and every
  # new TCP session re-reads it, so persisting it only creates stale data + DB churn.
  mask=int(mask)
  if mask not in range(32):raise ValueError('childlock mask must be 0..31')
  with self.lock:
   rt=self.runtime_devices.setdefault(mac,{})
   rt['childlock_mask']=mask;rt['last_seen']=now()
  return mask
 def disconnect(self,mac,session):
  with self.lock:
   row=self.db.execute('SELECT session_id FROM devices WHERE mac=?',(mac,)).fetchone()
   if row and row['session_id']==session:
    self._flush_energy_locked(mac,True);t=now();self.db.execute('UPDATE devices SET online=0,last_seen=? WHERE mac=?',(t,mac));self.db.execute('INSERT INTO events(ts,mac,kind,detail) VALUES(?,?,?,?)',(t,mac,'disconnect',session));self.db.commit();self.runtime_devices.setdefault(mac,{})['online']=0;self.runtime_devices[mac]['last_seen']=t;return True
   return False
 def set_alias(self,mac,alias):
  alias=' '.join(str(alias or '').split()).strip()
  if len(alias)>64:raise ValueError('장치 이름은 64자 이하로 입력하세요')
  with self.lock:
   if not self.db.execute('SELECT 1 FROM devices WHERE mac=?',(mac,)).fetchone():raise KeyError('device not found')
   self.db.execute('UPDATE devices SET alias=? WHERE mac=?',(alias or None,mac));self.db.commit()
  return self.device(mac)
 def set_channel_name(self,mac,selector,name):
  selector=int(selector)
  if selector not in (1,2,3,4):raise ValueError('채널 번호는 1~4여야 합니다')
  name=' '.join(str(name or '').split()).strip()
  if len(name)>64:raise ValueError('채널 이름은 64자 이하로 입력하세요')
  with self.lock:
   if not self.db.execute('SELECT 1 FROM devices WHERE mac=?',(mac,)).fetchone():raise KeyError('device not found')
   self.db.execute('INSERT INTO channels(mac,selector,name) VALUES(?,?,?) ON CONFLICT(mac,selector) DO UPDATE SET name=excluded.name',(mac,selector,name or None))
   cached=self.runtime_channels.get((mac,selector))
   if cached is not None:cached['name']=name or None
   self.db.commit()
  return self.device(mac)
 def remove_device(self,mac):
  with self.lock:
   if not self.db.execute('SELECT 1 FROM devices WHERE mac=?',(mac,)).fetchone():raise KeyError('device not found')
   self.db.execute('UPDATE devices SET removed_at=? WHERE mac=?',(now(),mac));self.db.commit()
  return self.device(mac)
 def restore_device(self,mac):
  with self.lock:
   if not self.db.execute('SELECT 1 FROM devices WHERE mac=?',(mac,)).fetchone():raise KeyError('device not found')
   self.db.execute('UPDATE devices SET removed_at=NULL WHERE mac=?',(mac,));self.db.commit()
  return self.device(mac)
 def delete_device_permanently(self,mac):
  # Destructive lifecycle operation: remove device history, energy continuity and per-device settings atomically.
  with self.lock:
   if not self.db.execute('SELECT 1 FROM devices WHERE mac=?',(mac,)).fetchone():raise KeyError('device not found')
   try:
    self.db.execute('BEGIN IMMEDIATE')
    self.db.execute('DELETE FROM events WHERE mac=?',(mac,))
    self.db.execute('DELETE FROM channels WHERE mac=?',(mac,))
    self.db.execute("DELETE FROM settings WHERE key LIKE ? ESCAPE '\\'",(f'diag.{mac}.%',))
    self.db.execute('DELETE FROM devices WHERE mac=?',(mac,))
    self.db.commit()
   except Exception:
    self.db.rollback();raise
   self.runtime_devices.pop(mac,None);self.energy_dirty_since.pop(mac,None)
   for k in [k for k in self.runtime_channels if k[0]==mac]:self.runtime_channels.pop(k,None)
  return True
 def is_removed(self,mac):
  with self.lock:
   r=self.db.execute('SELECT removed_at FROM devices WHERE mac=?',(mac,)).fetchone();return bool(r and r['removed_at'] is not None)
 def _runtime_channel(self,mac,selector):
  key=(mac,int(selector));z=self.runtime_channels.get(key)
  if z is None:
   r=self.db.execute('SELECT * FROM channels WHERE mac=? AND selector=?',(mac,int(selector))).fetchone();z=dict(r) if r else {'mac':mac,'selector':int(selector),'energy_lifetime_wh':0,'energy_epoch':0};self.runtime_channels[key]=z
  return z
 def set_relay(self,mac,selector,on):
  t=now()
  with self.lock:
   z=self._runtime_channel(mac,selector);z['on_state']=int(bool(on));z['last_seen']=t;self.runtime_devices.setdefault(mac,{})['last_seen']=t
 def _flush_energy_locked(self,mac,force=False):
  dirty=self.energy_dirty_since.get(mac);rows=[self.runtime_channels[k] for k in sorted(self.runtime_channels) if k[0]==mac and 1<=k[1]<=4]
  if not rows or dirty is None:return False
  persisted={r['selector']:r for r in self.db.execute('SELECT selector,energy_lifetime_wh FROM channels WHERE mac=?',(mac,))}
  delta=sum(abs(int(x.get('energy_lifetime_wh') or 0)-int((persisted.get(int(x['selector'])) or {'energy_lifetime_wh':0})['energy_lifetime_wh'] or 0)) for x in rows)
  if not force and delta<10 and now()-dirty<300:return False
  for x in rows:
   sel=int(x['selector'])
   self.db.execute("""INSERT INTO channels(mac,selector,energy_raw_wh,energy_lifetime_wh,energy_highwater_wh,energy_epoch) VALUES(?,?,?,?,?,?)
    ON CONFLICT(mac,selector) DO UPDATE SET energy_raw_wh=excluded.energy_raw_wh,energy_lifetime_wh=excluded.energy_lifetime_wh,energy_highwater_wh=excluded.energy_highwater_wh,energy_epoch=excluded.energy_epoch""",
    (mac,sel,int(x.get('energy_raw_wh') or 0),int(x.get('energy_lifetime_wh') or 0),int(x.get('energy_highwater_wh') or 0),int(x.get('energy_epoch') or 0)))
  self.db.commit();self.energy_dirty_since.pop(mac,None);return True
 def flush_energy(self,mac,force=False):
  with self.lock:return self._flush_energy_locked(mac,force)
 def apply_snapshot(self,mac,records):
  if not records:return
  t=now();curr={int(x['selector']):int(x['energy_raw_wh']) for x in records}
  with self.lock:
   prev={sel:dict(self._runtime_channel(mac,sel)) for sel in curr}
   drops=sum(1 for sel,v in curr.items() if prev.get(sel,{}).get('energy_raw_wh') not in (None,0) and v<int(prev[sel]['energy_raw_wh']))
   had_energy=any(int(prev.get(sel,{}).get('energy_raw_wh') or 0)>0 for sel in range(1,5))
   reset=drops>=2 or (had_energy and len(curr)>=4 and all(curr.get(sel,0)==0 for sel in range(1,5)))
   changed_energy=False
   for x in records:
    sel=int(x['selector']);raw=int(x['energy_raw_wh']);p=prev.get(sel,{})
    oldraw=p.get('energy_raw_wh');oldlife=int(p.get('energy_lifetime_wh') or 0);oldhigh=int(p.get('energy_highwater_wh') or 0);oldepoch=int(p.get('energy_epoch') or 0)
    if oldraw is None:
     base=self.db.execute('SELECT energy_highwater_wh,energy_lifetime_wh,energy_epoch FROM channels WHERE mac=? AND selector=?',(mac,sel)).fetchone()
     if base and base['energy_highwater_wh'] is not None:
      bh=int(base['energy_highwater_wh']);bl=int(base['energy_lifetime_wh'] or 0);be=int(base['energy_epoch'] or 0);lifetime=bl+(raw-bh) if raw>=bh else bl;high=max(bh,raw);epoch=be
     else:lifetime=raw;high=raw;epoch=0
    elif reset:lifetime=oldlife+raw;high=raw;epoch=oldepoch+1
    elif raw>=int(oldraw):lifetime=oldlife+(raw-int(oldraw));high=max(oldhigh,raw);epoch=oldepoch
    else:lifetime=oldlife;high=oldhigh;epoch=oldepoch
    z=self._runtime_channel(mac,sel);z.update({'mac':mac,'selector':sel,'on_state':int(bool(x['on'])),'power_mw':int(x['power_mw']),'temperature_c':int(x['temperature_c']),'energy_raw_wh':raw,'energy_lifetime_wh':lifetime,'energy_highwater_wh':high,'energy_epoch':epoch,'configuration':x.get('configuration'),'runtime':int(x.get('runtime',0)),'state_code':int(x.get('state',0)),'previous_raw_wh':int(x.get('previous_raw_wh',0)),'overload':x.get('overload'),'overheat':x.get('overheat'),'status':x.get('status'),'event_code':x.get('event_code'),'last_seen':t})
    if lifetime!=oldlife or reset:changed_energy=True
   self.runtime_devices.setdefault(mac,{})['last_seen']=t
   if changed_energy:self.energy_dirty_since.setdefault(mac,t)
   self._flush_energy_locked(mac,force=reset)
 def _device_dict(self,d):
  z=dict(d);z.pop('childlock_mask',None);z.update(self.runtime_devices.get(z['mac']) or {});z['online']=bool(z['online']);z['removed']=z.get('removed_at') is not None
  rows={int(x['selector']):dict(x) for x in self.db.execute('SELECT * FROM channels WHERE mac=? ORDER BY selector',(z['mac'],))}
  for (m,n),x in self.runtime_channels.items():
   if m==z['mac']:rows[n]=dict(x)
  z['channels']=[rows[k] for k in sorted(rows)];return z
 def devices(self):
  with self.lock:return [self._device_dict(d) for d in self.db.execute('SELECT * FROM devices WHERE removed_at IS NULL ORDER BY first_seen')]
 def removed_devices(self):
  with self.lock:return [self._device_dict(d) for d in self.db.execute('SELECT * FROM devices WHERE removed_at IS NOT NULL ORDER BY removed_at DESC')]
 def device(self,mac):
  with self.lock:
   d=self.db.execute('SELECT * FROM devices WHERE mac=?',(mac,)).fetchone();return None if not d else self._device_dict(d)


 def firmware_profile_upsert(self,pid,kind,version,label,sha256,size,model,filename=None,original_filename=None):
  if kind not in ('official','custom'):raise ValueError('invalid firmware profile kind')
  t=now()
  with self.lock:
   self.db.execute("""INSERT INTO firmware_profiles(id,kind,version,label,sha256,size,model,filename,original_filename,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,version=excluded.version,label=excluded.label,sha256=excluded.sha256,size=excluded.size,model=excluded.model,filename=COALESCE(excluded.filename,firmware_profiles.filename),original_filename=COALESCE(excluded.original_filename,firmware_profiles.original_filename),updated_at=excluded.updated_at""",(pid,kind,version,label,sha256,int(size),model,filename,original_filename,t,t));self.db.commit()
 def firmware_profile_set_filename(self,pid,filename):
  with self.lock:self.db.execute('UPDATE firmware_profiles SET filename=?,updated_at=? WHERE id=?',(filename,now(),pid));self.db.commit()
 def firmware_profile(self,pid):
  with self.lock:
   r=self.db.execute('SELECT * FROM firmware_profiles WHERE id=?',(pid,)).fetchone();return None if not r else dict(r)
 def firmware_profiles(self):
  with self.lock:return [dict(r) for r in self.db.execute("SELECT * FROM firmware_profiles ORDER BY CASE kind WHEN 'official' THEN 0 ELSE 1 END,created_at,id")]
 def firmware_profile_delete(self,pid):
  with self.lock:self.db.execute("DELETE FROM firmware_profiles WHERE id=? AND kind='custom'",(pid,));self.db.commit()
 def firmware_profile_delete_inactive(self,pid):
  # Internal migration cleanup only. Public/custom deletion keeps its stricter kind guard.
  with self.lock:self.db.execute('DELETE FROM firmware_profiles WHERE id=?',(pid,));self.db.commit()
 def ota_job_create(self,jid,mac,from_version,profile_id,target_version,firmware_sha256):
  t=now()
  with self.lock:
   self.db.execute('INSERT INTO ota_jobs(id,mac,from_version,profile_id,target_version,firmware_sha256,state,detail,queued_at,updated_at) VALUES(?,?,?,?,?,?,?,NULL,?,?)',(jid,mac,from_version,profile_id,target_version,firmware_sha256,'QUEUED',t,t));self.db.commit();return self.ota_job(jid)
 def ota_job(self,jid):
  with self.lock:
   r=self.db.execute('SELECT * FROM ota_jobs WHERE id=?',(jid,)).fetchone();return None if not r else dict(r)
 def ota_jobs(self,limit=30):
  with self.lock:return [dict(r) for r in self.db.execute('SELECT * FROM ota_jobs ORDER BY queued_at DESC LIMIT ?',(int(limit),))]
 def ota_latest_for_mac(self,mac):
  with self.lock:
   r=self.db.execute('SELECT * FROM ota_jobs WHERE mac=? ORDER BY queued_at DESC LIMIT 1',(mac,)).fetchone();return None if not r else dict(r)
 def ota_latest_applied_for_mac(self,mac):
  # Only terminal states that conclusively verified the target version are evidence of
  # which exact firmware build SHA was installed. Failed/unknown attempts must not hide
  # an older known-good applied build.
  with self.lock:
   r=self.db.execute("SELECT * FROM ota_jobs WHERE mac=? AND state IN ('SUCCESS','DELAYED_SUCCESS') ORDER BY queued_at DESC LIMIT 1",(mac,)).fetchone();return None if not r else dict(r)
 def ota_active_for_mac(self,mac):
  q=','.join('?' for _ in OTA_TERMINAL_STATES)
  with self.lock:
   r=self.db.execute(f'SELECT * FROM ota_jobs WHERE mac=? AND state NOT IN ({q}) ORDER BY queued_at DESC LIMIT 1',(mac,*OTA_TERMINAL_STATES)).fetchone();return None if not r else dict(r)
 def ota_reconcile_startup(self):
  # RAM queue/HTTP state disappears on process restart. Never leave persisted jobs blocking a MAC.
  # Before COMMAND_SENT no device mutation was attempted, so cancellation is known-safe. After
  # COMMAND_SENT the outcome can be ambiguous and must never be retried automatically.
  q=','.join('?' for _ in OTA_TERMINAL_STATES);t=now();out=[]
  with self.lock:
   rows=list(self.db.execute(f'SELECT id,state FROM ota_jobs WHERE state NOT IN ({q}) ORDER BY queued_at',( *OTA_TERMINAL_STATES,)))
   for r in rows:
    state='CANCELLED_RESTART' if r['state'] in ('QUEUED','PREPARING') else 'RESULT_UNKNOWN'
    detail='Backend 재시작 전 OTA 명령이 전송되지 않아 작업을 취소했습니다. 다시 시작해도 안전합니다.' if state=='CANCELLED_RESTART' else 'Backend가 OTA 진행 중 재시작되어 결과를 확정할 수 없습니다. 자동 재전송하지 않습니다.'
    self.db.execute('UPDATE ota_jobs SET state=?,detail=?,updated_at=?,completed_at=? WHERE id=?',(state,detail,t,t,r['id']));out.append((r['id'],r['state'],state))
   self.db.commit()
  return out
 def ota_job_update(self,jid,state,detail=None,completed=False):
  t=now()
  with self.lock:
   self.db.execute("UPDATE ota_jobs SET state=?,detail=?,started_at=COALESCE(started_at,CASE WHEN ?<>'QUEUED' THEN ? ELSE NULL END),updated_at=?,completed_at=CASE WHEN ? THEN ? ELSE completed_at END WHERE id=?",(state,detail,state,t,t,int(bool(completed)),t,jid));self.db.commit();return self.ota_job(jid)

@dataclass
class Session:
 reader:asyncio.StreamReader;writer:asyncio.StreamWriter;peer_ip:str
 id:str=field(default_factory=lambda:uuid.uuid4().hex);mac:str|None=None;bootinfo:str='';pending_events:list[tuple[int,bool]]=field(default_factory=list)
 lock:asyncio.Lock=field(default_factory=asyncio.Lock);write_lock:asyncio.Lock=field(default_factory=asyncio.Lock)
 onoff_q:asyncio.Queue=field(default_factory=lambda:asyncio.Queue(maxsize=16));getinfo_q:asyncio.Queue=field(default_factory=lambda:asyncio.Queue(maxsize=16));query_q:asyncio.Queue=field(default_factory=lambda:asyncio.Queue(maxsize=16));power_q:asyncio.Queue=field(default_factory=lambda:asyncio.Queue(maxsize=16));childlock_q:asyncio.Queue=field(default_factory=lambda:asyncio.Queue(maxsize=8));ota_q:asyncio.Queue=field(default_factory=lambda:asyncio.Queue(maxsize=4))
 closed:asyncio.Event=field(default_factory=asyncio.Event);diag_wakeup:asyncio.Event=field(default_factory=asyncio.Event);timeouts:int=0;last_getinfo_mono:float=0.0;childlock_tainted:bool=False
 async def send(self,line):
  async with self.write_lock:
   if self.writer.is_closing():raise ConnectionError('session closed')
   wire=line.rstrip('\r\n');log.debug('TX %s',wire)
   if wire.startswith('up:childlock'):log.info('TX childlock mac=%s wire=%s',self.mac,wire)
   self.writer.write((wire+'\r\n').encode());await self.writer.drain()
 def abort_transport(self):
  # A confirmed unacked transport outage is not an application-level timeout. Abort
  # the stale kernel socket immediately so a device returning with the same 4-tuple
  # can establish a fresh connection without colliding with the predecessor PCB.
  transport=getattr(self.writer,'transport',None) or getattr(self.writer,'_transport',None)
  if transport is not None and hasattr(transport,'abort'):
   transport.abort();return True
  self.writer.close();return False

class Manager:
 ACK_TIMEOUT=3.0
 GETINFO_TIMEOUT=3.5
 PERIODIC_GETINFO_TIMEOUT=5.0
 PERIODIC_GETINFO_RECOVERY_GRACE=16.0
 CONTROL_QUEUE_TIMEOUT=5.0
 TRANSIENT_COMMAND_TTL=45.0
 VERIFY_RETRY_DELAY=0.15
 POLL_PHASE_WINDOW=2.0
 FRESH_GETINFO_GAP=5.0
 DIAG_POLL_GUARD=1.0
 def __init__(self,store:Store,poll_interval=DEFAULT_POLL):self.store=store;self.poll_interval=poll_interval;self.sessions={};self.sessions_lock=asyncio.Lock();self.loop=None;self.ha=None;self.firmware=None;self.diagnostics={};self.diag_config_cache={};self.childlock_synced_sessions={};self.pending_power={};self.pending_power_revision=0;self.transport_loss={};self.transport_loss_revision=0
 def diagnostic_config(self,mac):
  c=self.diag_config_cache.get(mac)
  if c is None:
   get=getattr(self.store,'setting_get',lambda k:None)
   try:n=int(get(f'diag.{mac}.poll_interval') or 60)
   except Exception:n=60
   if n not in ALLOWED_DIAG_POLL:n=60
   raw_v=get(f'diag.{mac}.voltage_enabled');c={'voltage_enabled':raw_v=='1','rssi_enabled':get(f'diag.{mac}.rssi_enabled')=='1','poll_interval':n};self.diag_config_cache[mac]=c
  return dict(c)
 def diagnostic_view(self,mac):
  z=self.diagnostic_config(mac);z.update({k:v for k,v in (self.diagnostics.get(mac) or {}).items() if k!='force_due'});return z
 @staticmethod
 def childlock_supported(d):
  return bool(d and version_number(d.get('firmware_version'))==111)
 @classmethod
 def childlock_verified(cls,d):
  # Runtime value only. Current-session sync marker is checked separately below.
  if not cls.childlock_supported(d):return False
  v=d.get('childlock_mask')
  return type(v) is int and v in range(32)
 def childlock_synced(self,d):
  if not self.childlock_supported(d) or not d.get('online'):return False
  mac=d.get('mac');sid=d.get('session_id');v=d.get('childlock_mask');s=self.sessions.get(mac) if mac else None
  return bool(mac and sid and s and not s.childlock_tainted and self.childlock_synced_sessions.get(mac)==sid and type(v) is int and v in range(32))
 def childlock_wire_safe(self,d,decorated=None):
  # Child Lock wire protocol is active only on the canonical 1.0.111 build.
  return self.childlock_supported(d)
 def _set_childlock_synced(self,mac,s,synced):
  if synced and s and not s.childlock_tainted and not s.closed.is_set() and self.sessions.get(mac) is s:self.childlock_synced_sessions[mac]=s.id
  else:
   sid=self.childlock_synced_sessions.get(mac)
   if s is None or sid is None or sid==getattr(s,'id',None):self.childlock_synced_sessions.pop(mac,None)
  if self.ha and not self.store.is_removed(mac):self.ha.publish_childlock_availability(mac,force=True)
 def decorate_device(self,d):
  if not d:return d
  z=dict(d);z['diagnostics']=self.diagnostic_view(z['mac']);z['childlock_supported']=self.childlock_supported(z);z['childlock_verified']=self.childlock_verified(z);z['childlock_synced']=self.childlock_synced(z);z=self.firmware.decorate_device(z) if self.firmware else z
  z['childlock_probe_safe']=self.childlock_wire_safe(z,z)
  return z
 def devices_view(self,removed=False):
  rows=self.store.removed_devices() if removed else self.store.devices();return [self.decorate_device(d) for d in rows]
 def public_device_view(self,d):
  if not d:return d
  # Guest view does not expose OTA/profile metadata; avoid firmware file hashing here.
  z=dict(d);z['diagnostics']=self.diagnostic_view(z['mac']);diag=z['diagnostics']
  pd={'voltage_enabled':bool(diag.get('voltage_enabled')),'rssi_enabled':bool(diag.get('rssi_enabled'))}
  for k in ('voltage_mv','voltage_updated_at','rssi_dbm','rssi_updated_at'):
   if k in diag:pd[k]=diag[k]
  channels=[]
  for ch in z.get('channels') or []:
   if int(ch.get('selector',0)) not in range(1,5):continue
   channels.append({k:ch.get(k) for k in ('selector','name','on_state','power_mw','temperature_c','energy_lifetime_wh','last_seen')})
  return {'mac':z['mac'],'alias':z.get('alias'),'online':bool(z.get('online')),'last_seen':z.get('last_seen'),'firmware_version':z.get('firmware_version'),'channels':channels,'diagnostics':pd}
 def public_devices_view(self):return [self.public_device_view(d) for d in self.store.devices()]
 def set_diagnostic_config(self,mac,body):
  d=self.store.device(mac)
  if not d:raise KeyError('device not found')
  old=self.diagnostic_config(mac);v=body.get('voltage_enabled',old['voltage_enabled']);r=body.get('rssi_enabled',old['rssi_enabled']);n=int(body.get('poll_interval',old['poll_interval']))
  if type(v) is not bool or type(r) is not bool:raise ValueError('voltage_enabled/rssi_enabled must be boolean')
  if n not in ALLOWED_DIAG_POLL:raise ValueError('diagnostic poll_interval must be one of 30,60,120,300,600,1800')
  c={'voltage_enabled':v,'rssi_enabled':r,'poll_interval':n};self.diag_config_cache[mac]=c
  setter=getattr(self.store,'setting_set_many',None)
  vals={f'diag.{mac}.voltage_enabled':'1' if v else '0',f'diag.{mac}.rssi_enabled':'1' if r else '0',f'diag.{mac}.poll_interval':str(n)}
  if setter:setter(vals)
  else:
   for k,x in vals.items():self.store.setting_set(k,x)
  state=self.diagnostics.setdefault(mac,{})
  if not v:state.pop('voltage_mv',None);state.pop('voltage_updated_at',None)
  if not r:state.pop('rssi_dbm',None);state.pop('rssi_updated_at',None)
  state['force_due']=True
  return self.diagnostic_view(mac)
 async def apply_diagnostic_config(self,mac,body):
  # Runtime scheduling belongs to the device asyncio loop; the HTTP thread only submits here.
  c=self.set_diagnostic_config(mac,body);s=await self.current(mac)
  if s:s.diag_wakeup.set()
  return c
 async def delete_device_permanently(self,mac):
  d=self.store.device(mac)
  if not d:raise KeyError('device not found')
  if not d.get('removed'):raise ValueError('기록 완전 삭제는 먼저 숨긴 장치에서만 할 수 있습니다')
  # Clear HA retained discovery/state while identity still exists, then fence the TCP session.
  if self.ha:self.ha.remove_device(mac)
  async with self.sessions_lock:s=self.sessions.pop(mac,None)
  if s:
   s.closed.set();s.writer.close()
   try:await s.writer.wait_closed()
   except Exception:pass
  self.diagnostics.pop(mac,None);self.diag_config_cache.pop(mac,None)
  self.pending_power.pop(mac,None);self._end_transport_loss(mac,"device permanently deleted")
  self.store.delete_device_permanently(mac)
  log.info('device permanently deleted mac=%s; next connection will register as new',mac)
  return True
 def invalidate(self,s:Session,reason:str,abort_transport=False):
  # The wire protocol has no transaction ID. After a timed-out request, a late reply
  # could be mistaken for a later transaction, so quarantine the whole TCP session.
  if s.closed.is_set():return
  if s.mac and self.sessions.get(s.mac) is s:self._begin_transport_loss(s.mac,reason)
  log.warning('invalidate session mac=%s id=%s reason=%s abort=%s',s.mac,s.id,reason,bool(abort_transport))
  s.closed.set()
  if abort_transport:s.abort_transport()
  else:s.writer.close()
 async def identify(self,s:Session,mac:str):
  s.mac=mac
  async with self.sessions_lock:
   old=self.sessions.get(mac);self.sessions[mac]=s
  if old and old is not s:
   old.closed.set();old.writer.close()
  self.store.identify(mac,s.peer_ip,s.id,s.bootinfo)
  self.childlock_synced_sessions.pop(mac,None)  # every new transport session must re-prove Child Lock state
  if self.firmware:self.firmware.on_identified(mac)
  for selector,on in s.pending_events:self.store.set_relay(mac,selector,on)
  s.pending_events.clear();log.info('identified %s peer=%s session=%s removed=%s',mac,s.peer_ip,s.id,self.store.is_removed(mac))
  if self.ha:
   self.ha.device_connected(mac)
   if not self.store.is_removed(mac):
    self.ha.publish_discovery(mac)
    # Keep retained Child Lock availability through a short transport handoff.
    # connect-sync below publishes the definitive state after it re-reads flash.
 async def current(self,mac):
  async with self.sessions_lock:s=self.sessions.get(mac)
  return s if s and not s.closed.is_set() and not s.writer.is_closing() else None
 async def request_getinfo(self,s,timeout=None,source='request'):
  timeout=self.GETINFO_TIMEOUT if timeout is None else timeout
  async with s.lock:return await self._getinfo_locked(s,timeout,source)
 async def _query_locked(self,s,key,timeout=3.0):
  while not s.query_q.empty():s.query_q.get_nowait()
  await s.send(f'up:query:{key}')
  try:return await asyncio.wait_for(s.query_q.get(),timeout)
  except asyncio.TimeoutError:self.invalidate(s,f'query timeout key={key}');raise
 async def request_query(self,s,key,timeout=3.0):
  async with s.lock:return await self._query_locked(s,key,timeout)
 async def _childlock_locked(self,s,mask=None,timeout=3.0,invalidate_on_timeout=True):
  # Selector-0 Child Lock replies carry no transaction identifier. Once one exchange
  # times out, a late reply could be mistaken for any later GET/SET on the same stream.
  # Quarantine only this protocol sub-channel until transport reconnect; normal relay,
  # telemetry and diagnostics can continue after a read-only GET timeout.
  if s.childlock_tainted:raise ConnectionError('Child Lock channel quarantined until reconnect')
  while not s.childlock_q.empty():s.childlock_q.get_nowait()
  await s.send('up:childlock' if mask is None else f'up:childlock:{int(mask)}')
  try:return await asyncio.wait_for(s.childlock_q.get(),timeout)
  except asyncio.TimeoutError:
   s.childlock_tainted=True
   self._set_childlock_synced(s.mac,s,False)
   if invalidate_on_timeout:self.invalidate(s,'childlock response timeout')
   raise
 async def request_childlock(self,s,timeout=3.0,invalidate_on_timeout=False):
  async with s.lock:mask=await self._childlock_locked(s,None,timeout,invalidate_on_timeout)
  if mask==255:
   self._set_childlock_synced(s.mac,s,False)
   raise ValueError('Child Lock state unresolved')
  if mask not in range(32):raise ValueError(f'Child Lock device returned invalid mask {mask}')
  self.store.set_childlock(s.mac,mask);self._set_childlock_synced(s.mac,s,True)
  if self.ha and not self.store.is_removed(s.mac):self.ha.publish_device(s.mac,True)
  return mask
 async def childlock(self,mac,mask=None,source='backend'):
  d=self.store.device(mac)
  if not d or d.get('removed'):raise ConnectionError('device removed or not found')
  if not self.childlock_supported(d):raise ValueError('Child Lock requires firmware 1.0.111')
  dd=self.firmware.decorate_device(d) if self.firmware else d
  if not self.childlock_wire_safe(d,dd):
   raise ValueError('Child Lock wire access is not verified for this firmware build')
  if mask is not None:
   if type(mask) is bool:raise ValueError('mask must be integer 0..31')
   mask=int(mask)
   if mask not in range(32):raise ValueError('mask must be integer 0..31')
  s=await self.current(mac)
  if not s:raise ConnectionError('device offline')
  try:await asyncio.wait_for(s.lock.acquire(),self.CONTROL_QUEUE_TIMEOUT)
  except asyncio.TimeoutError:return {'ok':False,'state':'busy','mask':self.store.device(mac).get('childlock_mask'),'error':'device transaction queue busy; command was not sent'}
  try:
   if s.closed.is_set() or self.sessions.get(mac) is not s:raise ConnectionError('device session replaced')
   if s.childlock_tainted:return {'ok':False,'state':'channel_quarantined','mask':self.store.device(mac).get('childlock_mask'),'error':'Child Lock channel is quarantined until device reconnects'}
   try:got=await self._childlock_locked(s,mask,invalidate_on_timeout=mask is not None)
   except asyncio.TimeoutError:
    self._set_childlock_synced(mac,s,False)
    log.warning('childlock device response timeout source=%s mac=%s op=%s',source,mac,'set' if mask is not None else 'read')
    return {'ok':False,'state':'device_timeout','mask':self.store.device(mac).get('childlock_mask'),'error':'Child Lock device response timeout'}
   if got==255:
    self._set_childlock_synced(mac,s,False)
    log.warning('childlock state unresolved source=%s mac=%s op=%s',source,mac,'set' if mask is not None else 'read')
    return {'ok':False,'state':'state_unresolved','mask':self.store.device(mac).get('childlock_mask'),'observed_mask':255,'error':'Child Lock state could not be confirmed by device'}
   if got not in range(32):
    self._set_childlock_synced(mac,s,False)
    log.warning('childlock invalid device mask source=%s mac=%s observed=%s',source,mac,got)
    return {'ok':False,'state':'invalid_device_mask','mask':self.store.device(mac).get('childlock_mask'),'observed_mask':got,'error':f'Child Lock device returned invalid mask {got}'}
   self.store.set_childlock(mac,got)
   self._set_childlock_synced(mac,s,True)
   if self.ha and not self.store.is_removed(mac):self.ha.publish_device(mac,True)
   if mask is not None and got!=mask:
    log.warning('childlock mismatch source=%s mac=%s requested=%s observed=%s',source,mac,mask,got)
    return {'ok':False,'state':'observed_mismatch','requested_mask':mask,'mask':got,'error':'device Child Lock mask did not match request'}
   log.info('childlock %s source=%s mac=%s mask=%s','set' if mask is not None else 'read',source,mac,got)
   return {'ok':True,'state':'confirmed','mask':got}
  finally:s.lock.release()
 async def childlock_bit(self,mac,bit,on,source='ha'):
  d=self.store.device(mac)
  if not d or d.get('removed'):raise ConnectionError('device removed or not found')
  if not self.childlock_supported(d):raise ValueError('Child Lock requires firmware 1.0.111')
  dd=self.firmware.decorate_device(d) if self.firmware else d
  if not self.childlock_wire_safe(d,dd):
   raise ValueError('Child Lock wire access is not verified for this firmware build')
  bit=int(bit)
  if bit not in range(5):raise ValueError('Child Lock bit must be 0..4')
  s=await self.current(mac)
  if not s:raise ConnectionError('device offline')
  try:await asyncio.wait_for(s.lock.acquire(),self.CONTROL_QUEUE_TIMEOUT)
  except asyncio.TimeoutError:return {'ok':False,'state':'busy','mask':d.get('childlock_mask'),'error':'device transaction queue busy; command was not sent'}
  try:
   if s.closed.is_set() or self.sessions.get(mac) is not s:raise ConnectionError('device session replaced')
   if s.childlock_tainted:return {'ok':False,'state':'channel_quarantined','mask':self.store.device(mac).get('childlock_mask'),'error':'Child Lock channel is quarantined until device reconnects'}
   try:current=await self._childlock_locked(s,None,invalidate_on_timeout=False)
   except asyncio.TimeoutError:
    self._set_childlock_synced(mac,s,False)
    log.warning('childlock device response timeout source=%s mac=%s op=read-before-bit',source,mac)
    return {'ok':False,'state':'device_timeout','mask':self.store.device(mac).get('childlock_mask'),'error':'Child Lock device response timeout'}
   if current==255:
    self._set_childlock_synced(mac,s,False)
    return {'ok':False,'state':'state_unresolved','mask':self.store.device(mac).get('childlock_mask'),'observed_mask':255,'error':'Child Lock state could not be confirmed before bit update'}
   if current not in range(32):
    self._set_childlock_synced(mac,s,False)
    return {'ok':False,'state':'invalid_device_mask','mask':self.store.device(mac).get('childlock_mask'),'observed_mask':current,'error':f'Child Lock device returned invalid mask {current}'}
   requested=(current | (1<<bit)) if on else (current & ~(1<<bit))
   if requested!=current:
    try:got=await self._childlock_locked(s,requested)
    except asyncio.TimeoutError:
     self._set_childlock_synced(mac,s,False)
     log.warning('childlock device response timeout source=%s mac=%s op=set-bit',source,mac)
     return {'ok':False,'state':'device_timeout','mask':self.store.device(mac).get('childlock_mask'),'requested_mask':requested,'error':'Child Lock device response timeout'}
   else:got=current
   if got==255:
    self._set_childlock_synced(mac,s,False)
    return {'ok':False,'state':'state_unresolved','mask':self.store.device(mac).get('childlock_mask'),'observed_mask':255,'requested_mask':requested,'error':'Child Lock state could not be confirmed after bit update'}
   if got not in range(32):
    self._set_childlock_synced(mac,s,False)
    return {'ok':False,'state':'invalid_device_mask','mask':self.store.device(mac).get('childlock_mask'),'observed_mask':got,'error':f'Child Lock device returned invalid mask {got}'}
   self.store.set_childlock(mac,got)
   self._set_childlock_synced(mac,s,True)
   if self.ha and not self.store.is_removed(mac):self.ha.publish_device(mac,True)
   ok=got==requested
   log.info('childlock bit source=%s mac=%s bit=%s on=%s current=%s requested=%s observed=%s',source,mac,bit,bool(on),current,requested,got)
   return {'ok':ok,'state':'confirmed' if ok else 'observed_mismatch','mask':got,'requested_mask':requested,'bit':bit,'on':bool(on)}
  finally:s.lock.release()

 async def _power_report_locked(self,s,channel,kind,timeout=3.0):
  while not s.power_q.empty():s.power_q.get_nowait()
  await s.send(f'up:power_report:{channel}:{kind}')
  end=time.monotonic()+timeout
  try:
   while True:
    rem=end-time.monotonic()
    if rem<=0:raise asyncio.TimeoutError
    got=await asyncio.wait_for(s.power_q.get(),rem)
    if got[0]==channel:return got[1]
  except asyncio.TimeoutError:self.invalidate(s,f'power_report timeout channel={channel} kind={kind}');raise
 async def request_power_report(self,s,channel,kind,timeout=3.0):
  async with s.lock:return await self._power_report_locked(s,channel,kind,timeout)
 def _transport_event(self,mac,kind,payload):
  fn=getattr(self.store,'event',None)
  if not fn:return False
  try:fn(mac,kind,json.dumps(payload,ensure_ascii=False,separators=(',',':')));return True
  except Exception as e:
   log.warning('transport-loss event persistence failed mac=%s kind=%s err=%s',mac,kind,e);return False
 def _expire_transport_loss(self,mac,rev=None):
  ep=self.transport_loss.get(mac)
  if not ep:return False
  if rev is not None and int(ep.get('rev') or -1)!=int(rev):return False
  if time.monotonic()<float(ep.get('deadline_mono') or 0):return False
  pending=self.pending_power.pop(mac,None) or {}
  self.transport_loss.pop(mac,None)
  age=max(0.0,time.monotonic()-float(ep.get('started_mono') or time.monotonic()))
  self._transport_event(mac,'transport_loss_expired',{'duration_sec':round(age,3),'start_reason':ep.get('reason'),'discarded_outlets':sorted(int(n) for n in pending)})
  log.warning('transport loss deadline expired mac=%s age=%.1fs discarded_pending=%s',mac,age,sorted(int(n) for n in pending))
  return True
 def _active_transport_loss(self,mac):
  self._expire_transport_loss(mac)
  return self.transport_loss.get(mac)
 def _begin_transport_loss(self,mac,reason):
  if not mac:return None
  ep=self._active_transport_loss(mac)
  if ep:return ep
  t=time.monotonic();self.transport_loss_revision+=1;rev=self.transport_loss_revision
  ep={'rev':rev,'started_mono':t,'started_wall':now(),'deadline_mono':t+self.TRANSIENT_COMMAND_TTL,'reason':str(reason)}
  self.transport_loss[mac]=ep
  self._transport_event(mac,'transport_loss_start',{'reason':str(reason),'deadline_sec':self.TRANSIENT_COMMAND_TTL})
  loop=self.loop
  if loop is not None and not loop.is_closed():
   try:ep['expiry_handle']=loop.call_later(self.TRANSIENT_COMMAND_TTL+0.01,self._expire_transport_loss,mac,rev)
   except Exception:pass
  log.warning('transport loss episode started mac=%s reason=%s hard_deadline=%.0fs',mac,reason,self.TRANSIENT_COMMAND_TTL)
  return ep
 def _end_transport_loss(self,mac,reason='resynced'):
  ep=self.transport_loss.pop(mac,None)
  if not ep:return False
  h=ep.get('expiry_handle')
  try:
   if h:h.cancel()
  except Exception:pass
  age=max(0.0,time.monotonic()-float(ep.get('started_mono') or time.monotonic()))
  self._transport_event(mac,'transport_loss_end',{'duration_sec':round(age,3),'reason':str(reason),'start_reason':ep.get('reason')})
  log.info('transport loss episode ended mac=%s reason=%s age=%.1fs',mac,reason,age)
  return True
 def _recent_transport_loss(self,d):
  if not d or d.get('removed'):return False
  mac=d.get('mac')
  return bool(mac and self._active_transport_loss(mac))
 def _queue_power_desired(self,mac,selector,on,source,tx,reason):
  ep=self._active_transport_loss(mac)
  if not ep:
   log.warning('control defer rejected outside transport-loss window tx=%s mac=%s selector=%s reason=%s',tx,mac,selector,reason)
   return {'ok':False,'state':'offline','transaction':tx,'selector':int(selector),'requested_on':bool(on),'error':'device transport-loss grace expired'}
  requested=bool(on);targets=tuple(range(1,5)) if int(selector)==0 else (int(selector),);expires=float(ep['deadline_mono'])
  self.pending_power_revision+=1;rev=self.pending_power_revision
  bucket=self.pending_power.setdefault(mac,{});updated=[];coalesced=[]
  for n in targets:
   old=bucket.get(n)
   if old and bool(old.get('on')) is requested and float(old.get('expires_mono') or 0)==expires:
    coalesced.append(n);continue
   bucket[n]={'on':requested,'source':source,'tx':tx,'reason':reason,'rev':rev,'expires_mono':expires};updated.append(n)
  remaining=max(0.0,expires-time.monotonic())
  log.warning('control deferred tx=%s source=%s mac=%s selector=%s requested=%s reason=%s remaining=%.1fs updated=%s coalesced=%s',tx,source,mac,selector,'on' if requested else 'off',reason,remaining,updated,coalesced)
  return {'ok':True,'state':'deferred','transaction':tx,'selector':int(selector),'requested_on':requested,'queued_outlets':list(targets),'updated_outlets':updated,'coalesced_outlets':coalesced,'reason':reason,'expires_in_sec':max(0,int(remaining))}
 def _pending_power_snapshot(self,mac):
  self._expire_transport_loss(mac)
  bucket=self.pending_power.get(mac)
  if not bucket:return {}
  t=time.monotonic()
  for n,x in list(bucket.items()):
   if float(x.get('expires_mono') or 0)<=t:
    log.warning('deferred control expired mac=%s outlet=%s tx=%s',mac,n,x.get('tx'));bucket.pop(n,None)
  if not bucket:self.pending_power.pop(mac,None);return {}
  return {int(n):dict(x) for n,x in bucket.items()}
 def _clear_pending_power(self,mac,outlet,rev):
  bucket=self.pending_power.get(mac);x=bucket.get(int(outlet)) if bucket else None
  if x and int(x.get('rev') or -1)==int(rev):bucket.pop(int(outlet),None)
  if bucket is not None and not bucket:self.pending_power.pop(mac,None)
 async def reconcile_pending_power(self,s):
  mac=s.mac;pending=self._pending_power_snapshot(mac)
  if not pending:return {'ok':True,'state':'none'}
  log.info('deferred control reconcile start mac=%s outlets=%s',mac,sorted(pending))
  for n in sorted(pending):
   if s.closed.is_set() or self.sessions.get(mac) is not s:return {'ok':False,'state':'session_lost'}
   current=self._pending_power_snapshot(mac).get(n)
   if not current:continue
   desired=bool(current['on']);rev=current['rev']
   d=self.store.device(mac) or {};observed=None
   for ch in d.get('channels') or []:
    if int(ch.get('selector') or -1)==n and ch.get('on_state') is not None:observed=bool(ch.get('on_state'));break
   if observed is desired:
    self._clear_pending_power(mac,n,rev);log.info('deferred control already satisfied mac=%s outlet=%s state=%s',mac,n,'on' if desired else 'off');continue
   r=await self.power(mac,n,desired,source='reconnect-replay')
   if r.get('ok') and r.get('state')=='confirmed':
    self._clear_pending_power(mac,n,rev);continue
   if r.get('state')=='deferred':return r
   log.warning('deferred control reconcile stopped mac=%s outlet=%s result=%s',mac,n,r);return r
  left=self._pending_power_snapshot(mac)
  log.info('deferred control reconcile done mac=%s remaining=%s',mac,sorted(left))
  return {'ok':not bool(left),'state':'confirmed' if not left else 'partial','remaining':sorted(left)}
 async def power(self,mac,selector,on,source='backend'):
  tx=uuid.uuid4().hex[:10]
  d=self.store.device(mac)
  if not d or d.get('removed'):raise ConnectionError('device removed or not found')
  log.info('control queued tx=%s source=%s mac=%s selector=%s requested=%s',tx,source,mac,selector,'on' if on else 'off')
  if selector not in range(5):raise ValueError('selector 0..4')
  requested=bool(on);targets=tuple(range(1,5)) if selector==0 else (selector,);done=[]
  s=await self.current(mac)
  if not s:
   if self._recent_transport_loss(d):return self._queue_power_desired(mac,selector,requested,source,tx,'transient_offline')
   raise ConnectionError('device offline')
  try:await asyncio.wait_for(s.lock.acquire(),self.CONTROL_QUEUE_TIMEOUT)
  except asyncio.TimeoutError:
   if self._active_transport_loss(mac):return self._queue_power_desired(mac,selector,requested,source,tx,'transport_busy')
   log.warning('control busy tx=%s source=%s mac=%s selector=%s',tx,source,mac,selector)
   return {'ok':False,'state':'busy','transaction':tx,'selector':selector,'requested_on':requested,'acked_outlets':[],'confirmed_outlets':[],'error':'device transaction queue busy; command was not sent'}
  try:
   # A request may have waited behind another transaction while this session was replaced.
   if s.closed.is_set() or self.sessions.get(mac) is not s:
    if self._active_transport_loss(mac):return self._queue_power_desired(mac,selector,requested,source,tx,'session_replaced')
    raise ConnectionError('device session replaced')
   log.info('control start tx=%s source=%s mac=%s selector=%s',tx,source,mac,selector)
   while not s.onoff_q.empty():s.onoff_q.get_nowait()
   for n in targets:
    try:await s.send(f"up:onoff:{n}:{'on' if requested else 'off'}")
    except (ConnectionError,ConnectionResetError,BrokenPipeError,OSError) as e:
     self.invalidate(s,f'onoff send failed selector={n}: {e}',abort_transport=True)
     return self._queue_power_desired(mac,selector,requested,source,tx,'send_failed')
    try:
     end=time.monotonic()+self.ACK_TIMEOUT
     while True:
      rem=end-time.monotonic()
      if rem<=0:raise asyncio.TimeoutError
      got=await asyncio.wait_for(s.onoff_q.get(),rem)
      if got==(n,requested):break
    except asyncio.TimeoutError:
     # Never retransmit a mutation after an ambiguous timeout. Reconnect first so a
     # delayed ACK cannot confirm a later command on the same uncorrelated stream.
     self.invalidate(s,f'onoff ACK timeout selector={n}',abort_transport=True)
     log.warning('control ambiguous tx=%s reason=ack_timeout failed_at=%s acked=%s; deferring absolute desired state',tx,n,done)
     return self._queue_power_desired(mac,selector,requested,source,tx,'ack_timeout')
    done.append(n)

   # ACK means the command path answered; final actuator state is confirmed only by
   # a fresh device snapshot. On a received-but-mismatched snapshot, one read-only
   # retry is safe and allows a short relay/state propagation window.
   records=None;observed={}
   for attempt in range(2):
    try:records=await self._getinfo_locked(s,self.GETINFO_TIMEOUT,'control-verify')
    except asyncio.TimeoutError:
     log.warning('control ambiguous tx=%s reason=verify_timeout acked=%s; deferring absolute desired state',tx,done)
     return self._queue_power_desired(mac,selector,requested,source,tx,'verify_timeout')
    observed={int(x['selector']):bool(x['on']) for x in records if int(x.get('selector',-1)) in targets}
    if all(n in observed and observed[n] is requested for n in targets):
     log.info('control confirmed tx=%s mac=%s selector=%s observed=%s',tx,mac,selector,observed)
     return {'ok':True,'state':'confirmed','transaction':tx,'selector':selector,'on':requested,'acked_outlets':list(done),'confirmed_outlets':list(targets),'observed':observed}
    if attempt==0:await asyncio.sleep(self.VERIFY_RETRY_DELAY)
   confirmed=[n for n in targets if n in observed and observed[n] is requested]
   log.warning('control mismatch tx=%s mac=%s selector=%s requested=%s observed=%s',tx,mac,selector,requested,observed)
   return {'ok':False,'state':'observed_mismatch','transaction':tx,'selector':selector,'requested_on':requested,'acked_outlets':list(done),'confirmed_outlets':confirmed,'observed':observed,'error':'device state did not converge to requested state'}
  finally:
   s.lock.release()
 async def _getinfo_locked(self,s,timeout,source='request',transport_recovery_grace=0.0):
  while not s.getinfo_q.empty():s.getinfo_q.get_nowait()
  tcp_before=tcp_info_snapshot(s.writer)
  started=time.monotonic();await s.send('up:getinfo:all')
  try:r=await asyncio.wait_for(s.getinfo_q.get(),timeout)
  except asyncio.TimeoutError:
   tcp_after=tcp_info_snapshot(s.writer);delivery,retrans_delta=tcp_info_timeout_class(tcp_before,tcp_after)
   log.warning('getinfo tcp_info timeout mac=%s source=%s delivery=%s retrans_delta=%s before=%s after=%s',s.mac,source,delivery,retrans_delta,tcp_before,tcp_after)
   # Race-safe boundary check: a reply queued just after wait_for() expired still belongs
   # to this one outstanding GETINFO request and can be consumed without ambiguity.
   try:r=s.getinfo_q.get_nowait()
   except asyncio.QueueEmpty:r=None
   if r is None and delivery=='unacked_at_timeout':
    # Production pcap proved this signature coincides with a full device IP/Wi-Fi
    # blackout, followed by ARP probing and a fresh SYN from the same source port.
    # Waiting on the old stream only makes that first SYN collide with the stale PCB.
    elapsed=time.monotonic()-started
    log.warning('getinfo network blackout mac=%s source=%s elapsed=%.3fs retrans_delta=%s; aborting stale transport for fast reconnect',s.mac,source,elapsed,retrans_delta)
    self.invalidate(s,'unacked network blackout; fast reconnect',abort_transport=True);raise
   if r is None and transport_recovery_grace>0 and delivery=='acked_after_retrans' and not s.closed.is_set() and not s.writer.is_closing():
    # If TCP eventually ACKed the request, the path is alive and a late application
    # reply can still safely satisfy this single outstanding read-only transaction.
    log.warning('getinfo transport stall grace mac=%s source=%s delivery=%s grace=%.1fs; no application retry',s.mac,source,delivery,transport_recovery_grace)
    try:r=await asyncio.wait_for(s.getinfo_q.get(),transport_recovery_grace)
    except asyncio.TimeoutError:
     tcp_final=tcp_info_snapshot(s.writer);final_delivery,final_retrans=tcp_info_timeout_class(tcp_before,tcp_final)
     log.warning('getinfo transport recovery exhausted mac=%s source=%s elapsed=%.3fs delivery=%s retrans_delta=%s final=%s',s.mac,source,time.monotonic()-started,final_delivery,final_retrans,tcp_final)
     self.invalidate(s,'getinfo timeout after transport recovery grace');raise
   if r is None:
    self.invalidate(s,'getinfo timeout');raise
   finished=time.monotonic();s.last_getinfo_mono=finished;log.warning('getinfo transport recovered mac=%s source=%s elapsed=%.3fs delivery=%s retrans_delta=%s',s.mac,source,finished-started,delivery,retrans_delta);return r
  finished=time.monotonic();s.last_getinfo_mono=finished;return r
 async def refresh(self,mac):
  d=self.store.device(mac)
  if not d or d.get('removed'):raise ConnectionError('device removed or not found')
  s=await self.current(mac)
  if not s:raise ConnectionError('device offline')
  records=await self.request_getinfo(s);return records
 @classmethod
 def poll_phase(cls,mac):
  # Deterministic 0..2 s phase. The 15 s cadence itself never receives random jitter.
  steps=int(round(cls.POLL_PHASE_WINDOW*1000.0))
  return (int(hashlib.sha256(mac.encode('ascii')).hexdigest()[:8],16)%(steps+1))/1000.0
 def next_poll_deadline(self,mac,t=None):
  # Absolute monotonic grid: phase + N*interval. Reconnects do not redefine the cadence.
  t=time.monotonic() if t is None else float(t);interval=float(self.poll_interval);phase=self.poll_phase(mac)
  if interval<=0:raise ValueError('poll_interval must be positive')
  return ((t-phase)//interval+1.0)*interval+phase
 def periodic_fresh_gap(self):
  # A 5 s configured poll must not self-suppress every second slot. For normal >=10 s
  # intervals the approved 5 s freshness window applies unchanged.
  return 0.0 if float(self.poll_interval)<=self.FRESH_GETINFO_GAP else self.FRESH_GETINFO_GAP
 def getinfo_is_fresh(self,s,t=None):
  gap=self.periodic_fresh_gap()
  if gap<=0 or s.last_getinfo_mono<=0:return False
  t=time.monotonic() if t is None else float(t)
  return 0.0<=t-s.last_getinfo_mono<gap
 def poll_slot_missed(self,deadline,t=None):
  t=time.monotonic() if t is None else float(t)
  return t-float(deadline)>=float(self.poll_interval)
 async def periodic_poll_once(self,s,t=None):
  # Periodic telemetry is strictly low priority: never queue behind another transaction.
  if s.closed.is_set() or self.sessions.get(s.mac) is not s:return 'stale'
  if self.getinfo_is_fresh(s,t):
   log.debug('skip periodic poll; recent GETINFO mac=%s',s.mac);return 'fresh'
  if s.lock.locked():
   log.debug('skip periodic poll while transaction active mac=%s',s.mac);return 'busy'
  await s.lock.acquire()  # no suspension when unlocked; the preceding check makes this non-queued
  try:
   if s.closed.is_set() or self.sessions.get(s.mac) is not s:return 'stale'
   if self.getinfo_is_fresh(s):
    log.debug('skip periodic poll; recent GETINFO mac=%s',s.mac);return 'fresh'
   await self._getinfo_locked(s,self.PERIODIC_GETINFO_TIMEOUT,'periodic',self.PERIODIC_GETINFO_RECOVERY_GRACE);s.timeouts=0;return 'polled'
  finally:s.lock.release()
 async def poll(self,s):
  # Reconnect reconciliation is immediate and intentionally ignores the MAC phase.
  try:
   await self.request_getinfo(s);s.timeouts=0
  except asyncio.TimeoutError:
   s.timeouts+=1;log.warning('initial getinfo timeout mac=%s; reconnecting to resync',s.mac);return
  except Exception as e:
   if not s.closed.is_set():log.warning('initial poll sync error mac=%s %s',s.mac,e)
   return
  # GETINFO success proves the UP parser is ready. Reconcile any short-blackout power
  # desired-state before lower-priority configuration sync. Absolute ON/OFF replay is idempotent.
  reconcile_ok=False
  try:
   rr=await self.reconcile_pending_power(s);reconcile_ok=bool(rr.get('ok')) and rr.get('state') in ('none','confirmed')
  except Exception as e:
   if not s.closed.is_set():log.warning('deferred control reconnect reconcile failed mac=%s err=%s',s.mac,e)
  if s.closed.is_set() or self.sessions.get(s.mac) is not s:return
  if reconcile_ok:self._end_transport_loss(s.mac,'getinfo+desired-state reconciled')
  # On 1.0.111 synchronize Child Lock once; no retry chain is used, and an ambiguous
  # read timeout quarantines only Child Lock.
  d=self.store.device(s.mac) if s.mac else None
  if self.childlock_supported(d):
   try:
    r=await self.childlock(s.mac,None,source='connect-sync')
    log.info('childlock connect sync mac=%s result=%s',s.mac,r)
   except Exception as e:
    self._set_childlock_synced(s.mac,s,False)
    log.warning('childlock connect sync failed mac=%s err=%s',s.mac,e)
  deadline=self.next_poll_deadline(s.mac or '')
  while not s.closed.is_set():
   wait=max(0.0,deadline-time.monotonic())
   try:await asyncio.wait_for(s.closed.wait(),wait)
   except asyncio.TimeoutError:pass
   if s.closed.is_set():break
   t=time.monotonic()
   # If the event loop missed a whole cadence, do not execute stale slots or catch up.
   if self.poll_slot_missed(deadline,t):
    log.debug('drop missed periodic poll mac=%s late=%.3fs',s.mac,t-deadline)
   else:
    try:await self.periodic_poll_once(s,t)
    except asyncio.TimeoutError:
     s.timeouts+=1;log.warning('getinfo timeout mac=%s; reconnecting to resync',s.mac);return
    except Exception as e:
     if not s.closed.is_set():log.warning('poll error mac=%s %s',s.mac,e)
   deadline=self.next_poll_deadline(s.mac or '',time.monotonic())
 async def diag_poll(self,s):
  next_due=0.0
  while not s.closed.is_set():
   if not s.mac:return
   cfg=self.diagnostic_config(s.mac);state=self.diagnostics.setdefault(s.mac,{})
   if state.pop('force_due',False):next_due=0.0
   enabled=cfg['voltage_enabled'] or cfg['rssi_enabled'];t=time.monotonic()
   next_main=self.next_poll_deadline(s.mac,t) if enabled and s.last_getinfo_mono>0 else None
   main_poll_imminent=next_main is not None and next_main-t<=self.DIAG_POLL_GUARD
   if enabled and t>=next_due and s.last_getinfo_mono>0 and not main_poll_imminent and not s.lock.locked():
    try:
     async with s.lock:
      if s.closed.is_set() or self.sessions.get(s.mac) is not s:return
      changed={}
      if cfg['voltage_enabled']:
       changed['voltage_mv']=int(await self._power_report_locked(s,1,'vol'))
       changed['voltage_updated_at']=now()
      if cfg['rssi_enabled']:
       line=await self._query_locked(s,'wifirssi')
       m=re.fullmatch(r'up:query:(-?\d+)',line,re.I)
       if not m:raise ValueError('unexpected wifirssi response: '+line)
       changed['rssi_dbm']=int(m.group(1));changed['rssi_updated_at']=now()
      state.update(changed);next_due=time.monotonic()+cfg['poll_interval']
      if self.ha and not self.store.is_removed(s.mac):self.ha.publish_diagnostics(s.mac,self.diagnostic_view(s.mac))
    except asyncio.TimeoutError:return
    except Exception as e:
     if not s.closed.is_set():log.warning('diagnostic poll failed mac=%s err=%s',s.mac,e)
     next_due=time.monotonic()+cfg['poll_interval']
   # Configuration changes wake this poller immediately; otherwise re-check every 2s
   # so normal control/GETINFO transactions always keep priority.
   try:
    await asyncio.wait_for(s.diag_wakeup.wait(),2.0);s.diag_wakeup.clear()
   except asyncio.TimeoutError:pass
 async def handle(self,reader,writer):
  peer=writer.get_extra_info('peername') or ('?',0);s=Session(reader,writer,str(peer[0]));pt=None;dt=None
  log.info('connect peer=%s',peer)
  try:
   while True:
    raw=await reader.readline()
    if not raw:break
    # Fence a superseded same-MAC connection before buffered old data can overwrite
    # state belonging to the newly identified session.
    if s.closed.is_set() or (s.mac and self.sessions.get(s.mac) is not s):break
    if len(raw)>8192:raise ValueError('line too long')
    line=raw.decode('utf-8','replace').strip('\r\n\0 ')
    if not line:continue
    log.debug('RX %s',line)
    if line.startswith(('up:cl:','up:childlock:')):log.info('RX childlock mac=%s wire=%s',s.mac,line)
    if line.startswith('up:bootinfo:'):
     s.bootinfo=line
     if not s.mac:
      m=find_mac(line)
      if m:
       await self.identify(s,m);pt=asyncio.create_task(self.poll(s),name='poll-'+m);dt=asyncio.create_task(self.diag_poll(s),name='diag-'+m)
     continue
    em=EVENT_RE.fullmatch(line)
    if em:
     event=(int(em.group(1)),em.group(2).lower()=='on')
     if event[0]==0:continue  # master event is not persisted/exposed; selector 0 remains command-only
     if not s.mac:
      if len(s.pending_events)>=16:s.pending_events.pop(0)
      s.pending_events.append(event)
     else:
      log.info('physical relay event mac=%s selector=%s state=%s',s.mac,event[0],'on' if event[1] else 'off')
      self.store.set_relay(s.mac,*event)
      if self.ha:self.ha.publish_device(s.mac)
     continue
    if not s.mac:continue
    om=ONOFF_RE.fullmatch(line)
    if om:
     x=(int(om.group(1)),om.group(2).lower()=='on');queue_latest(s.onoff_q,x)
     continue
    if line.startswith('up:getinfo:'):
     r=parse_getinfo(line);selectors=[int(x['selector']) for x in r]
     if len(r)!=4 or set(selectors)!={1,2,3,4}:
      log.warning('discard incomplete getinfo mac=%s selectors=%s',s.mac,selectors);continue
     self.store.apply_snapshot(s.mac,r);queue_latest(s.getinfo_q,r)
     if self.ha:self.ha.publish_device(s.mac)
     continue
    cm=CHILDLOCK_RE.fullmatch(line)
    if cm:
     mask=int(cm.group(1))
     if 0<=mask<=255:queue_latest(s.childlock_q,mask)
     continue
    if line.startswith('up:query:'):
     queue_latest(s.query_q,line);continue
    if line.lower().startswith('up:ota:ota_ok'):
     queue_latest(s.ota_q,'ok');continue
    pm=POWER_REPORT_RE.fullmatch(line)
    if pm:
     queue_latest(s.power_q,(int(pm.group(1)),int(pm.group(2))));continue
  except (ConnectionResetError,BrokenPipeError,asyncio.IncompleteReadError):pass
  except Exception as e:log.warning('session error peer=%s %s',peer,e)
  finally:
   s.closed.set()
   if pt:pt.cancel()
   if dt:dt.cancel()
   if s.mac:
    was_current=False
    async with self.sessions_lock:
     if self.sessions.get(s.mac) is s:
      self._begin_transport_loss(s.mac,'transport closed');self.sessions.pop(s.mac,None);was_current=True
    if self.childlock_synced_sessions.get(s.mac)==s.id:self.childlock_synced_sessions.pop(s.mac,None)
    went_offline=self.store.disconnect(s.mac,s.id) if was_current else False
    if self.ha and went_offline:
     # Main device availability already has a 30 s grace. Do not flicker the separate
     # Child Lock availability for the same short Wi-Fi/netif reset.
     self.ha.device_disconnected(s.mac)
   writer.close()
   try:await writer.wait_closed()
   except Exception:pass
   log.info('disconnect mac=%s peer=%s',s.mac,peer)

class Web(BaseHTTPRequestHandler):
 BACKUP_DEBOUNCE_SECONDS=2.0
 _backup_debounce_lock=threading.RLock();_backup_timer=None;_backup_reasons=set()
 manager:Manager=None;store:Store=None;loop=None;root:Path=None;config_path:Path=None;ha=None;backup:BackupManager=None;auth:AuthManager=None;firmware:FirmwareStore=None
 COOKIE='mttl_session'
 def log_message(self,fmt,*args):log.debug('HTTP '+fmt,*args)
 def json_body(self):
  n=min(int(self.headers.get('Content-Length','0') or 0),1024*1024);return json.loads(self.rfile.read(n) or b'{}')
 def raw_body(self,max_bytes=1024*1024):
  n=int(self.headers.get('Content-Length','0') or 0)
  if n<1 or n>max_bytes:raise ValueError('request body size out of range')
  b=self.rfile.read(n)
  if len(b)!=n:raise ValueError('incomplete request body')
  return b
 def _headers(self,ctype,length,extra=None,cache='no-store'):
  self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(length));self.send_header('Cache-Control',cache);self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer')
  for k,v in extra or []:self.send_header(k,v)
 def send_json(self,obj,status=200,extra=None):
  b=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode();self.send_response(status);self._headers('application/json; charset=utf-8',len(b),extra);self.end_headers();self.wfile.write(b)
 def send_file(self,p,ctype,cache='no-store',extra=None):
  try:b=p.read_bytes()
  except FileNotFoundError:self.send_error(404);return
  self.send_response(200);self._headers(ctype,len(b),extra=extra,cache=cache);self.end_headers();self.wfile.write(b)
 def session_token(self):
  cookie=self.headers.get('Cookie','')
  for part in cookie.split(';'):
   k,sep,v=part.strip().partition('=')
   if sep and k==self.COOKIE:return v
  return None
 def require_auth(self):
  if self.auth.valid(self.session_token()):return True
  self.send_json({'error':'authentication required'},401);return False
 @classmethod
 def cookie_header(cls,token,max_age=None):
  if max_age is None:max_age=AuthManager.SESSION_TTL
  return ('Set-Cookie',f'{cls.COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={int(max_age)}')
 def backup_now(self,reason):
  if not self.backup:return None
  return self.backup.create(self.store,reason)
 def backup_later(self,reason):
  # Coalesce bursts of related settings writes into one verified snapshot.
  if not self.backup:return
  cls=type(self)
  with cls._backup_debounce_lock:
   cls._backup_reasons.add(str(reason))
   if cls._backup_timer and cls._backup_timer.is_alive():return
   def run():
    time.sleep(cls.BACKUP_DEBOUNCE_SECONDS)
    with cls._backup_debounce_lock:
     reasons=sorted(cls._backup_reasons);cls._backup_reasons.clear();cls._backup_timer=None
    label='batch:'+','.join(reasons)[:160]
    try:self.backup.create(self.store,label)
    except Exception as e:log.warning('background backup failed reason=%s: %s',label,e)
   cls._backup_timer=threading.Thread(target=run,name='backup-debounce',daemon=True);cls._backup_timer.start()
 def do_GET(self):
  p=urlparse(self.path).path
  if p=='/':return self.send_file(self.root/'static/index.html','text/html; charset=utf-8')
  if p=='/logs.html':return self.send_file(self.root/'static/logs.html','text/html; charset=utf-8',cache='no-cache')
  if p=='/logs.js':return self.send_file(self.root/'static/logs.js','application/javascript; charset=utf-8',cache='no-cache')
  if p=='/app.js':return self.send_file(self.root/'static/app.js','application/javascript; charset=utf-8',cache='no-cache')
  if p=='/mttl-up-card.js':return self.send_file(self.root/'static/mttl-up-card.js','application/javascript; charset=utf-8',cache='no-cache')
  if p=='/api/health':return self.send_json({'ok':True,'version':BACKEND_VERSION,'devices_online':sum(1 for d in self.store.devices() if d.get('online'))})
  if p=='/api/auth/status':return self.send_json(self.auth.status(self.session_token()))
  if p=='/api/devices':
   admin=self.auth.valid(self.session_token())
   return self.send_json({'authenticated':admin,'backend_version':BACKEND_VERSION,'devices':self.manager.devices_view(False) if admin else self.manager.public_devices_view(),'removed':self.manager.devices_view(True) if admin else [],'firmware':self.firmware.status() if admin else None})
  if not self.require_auth():return
  if p=='/provision.zip':return self.send_file(self.root/'static/provision.zip','application/zip',extra=[('Content-Disposition','attachment; filename=MTTL-W01_Provision_DualMode.zip')])
  if p=='/provision-android.apk':return self.send_file(self.root/'static/provision-android.apk','application/vnd.android.package-archive',extra=[('Content-Disposition','attachment; filename=MTTL-W01_Setup_Android_1.5.8.apk')])
  if p=='/api/logs':
   q=parse_qs(urlparse(self.path).query);level=(q.get('level') or ['DEBUG'])[0].upper();cat=(q.get('category') or ['all'])[0];limit=(q.get('limit') or ['300'])[0]
   if level not in ('DEBUG','INFO','WARNING','ERROR'):level='DEBUG'
   if cat not in ('all','ota','wire','control','childlock','ha','connection','storage','other'):cat='all'
   try:limit=int(limit)
   except Exception:limit=300
   return self.send_json({'version':BACKEND_VERSION,'level':level,'category':cat,'rows':UI_LOG.snapshot(level,cat,limit)})
  if p=='/api/ha':return self.send_json(self.ha.public_config())
  if p=='/api/config':return self.send_json({'version':BACKEND_VERSION,'poll_interval':self.manager.poll_interval,'allowed':ALLOWED_POLL,'diagnostic_allowed':ALLOWED_DIAG_POLL})
  if p=='/api/storage':
   z=self.store.storage_stats();z['backup']=self.backup.status() if self.backup else None;return self.send_json(z)
  if p=='/api/backup':return self.send_json(self.backup.status() if self.backup else {'enabled':False})
  if p=='/api/firmware':return self.send_json(self.firmware.status())
  m=re.fullmatch(r'/api/firmware/download/(official-111|official-110|official-66|custom-[0-9a-f]{16})',p)
  if m:
   try:info=self.firmware.download_profile(m.group(1))
   except Exception as e:return self.send_json({'error':str(e)},409)
   prof=info['profile'];headers=[('Content-Disposition',f'attachment; filename="{info["filename"]}"'),('X-Firmware-SHA256',prof['sha256']),('X-Firmware-Version',str(prof.get('version') or ''))]
   return self.send_file(info['path'],'application/octet-stream',extra=headers)
  m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})',p)
  if m:
   d=self.manager.decorate_device(self.store.device(m.group(1).lower()));return self.send_json(d if d else {'error':'not found'},200 if d else 404)
  self.send_error(404)
 def do_PUT(self):
  p=urlparse(self.path).path
  if not self.require_auth():return
  try:
   if p=='/api/auth/password':
    body=self.json_body();token=self.auth.change_password(body.get('current_password',''),body.get('new_password',''));self.backup_later('admin_password');return self.send_json({'ok':True,'authenticated':True,'must_change_password':False},200,[self.cookie_header(token)])
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/childlock',p)
   if m:
    mac=m.group(1).lower();body=self.json_body();mask=body.get('mask')
    if type(mask) is bool or not isinstance(mask,int) or mask not in range(32):raise ValueError('mask must be JSON integer 0..31')
    f=asyncio.run_coroutine_threadsafe(self.manager.childlock(mac,mask,source='web'),self.loop)
    try:r=f.result(10)
    except concurrent.futures.TimeoutError:return self.send_json({'ok':False,'state':'indeterminate','error':'Child Lock backend deadline exceeded'},504)
    status=200 if r.get('ok') else (504 if r.get('state')=='device_timeout' else 409 if r.get('state') in ('busy','observed_mismatch') else 400);return self.send_json(r,status)
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/diagnostics',p)
   if m:
    mac=m.group(1).lower();body=self.json_body();f=asyncio.run_coroutine_threadsafe(self.manager.apply_diagnostic_config(mac,body),self.loop);cfg=f.result(3);self.backup_later('device_diagnostics')
    if self.ha:self.ha.publish_discovery(mac);self.ha.sync_diagnostic_config(mac,cfg)
    return self.send_json({'ok':True,'diagnostics':cfg})
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/outlets/([1-4])',p)
   if m:
    mac=m.group(1).lower();selector=int(m.group(2));d=self.store.set_channel_name(mac,selector,self.json_body().get('name',''))
    if self.ha and not d.get('removed'):self.ha.publish_discovery(mac)
    self.backup_later('channel_name');return self.send_json({'ok':True,'device':self.manager.decorate_device(d)})
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})',p)
   if m:
    mac=m.group(1).lower();d=self.store.set_alias(mac,self.json_body().get('alias',''))
    if self.ha and not d.get('removed'):self.ha.publish_discovery(mac)
    self.backup_later('device_alias');return self.send_json({'ok':True,'device':self.manager.decorate_device(d)})
   if p=='/api/firmware/target':
    body=self.json_body();profile=self.firmware.set_target_profile(str(body.get('profile_id','')));host=body.get('ota_host_ip')
    if host is not None and str(host).strip():self.firmware.set_host_ip(host)
    self.backup_later('firmware_target');return self.send_json({'ok':True,'profile':profile,'firmware':self.firmware.status()})
   if p=='/api/config':
    body=self.json_body();out={'ok':True}
    if 'poll_interval' in body:
     n=int(body.get('poll_interval'))
     if n not in ALLOWED_POLL:raise ValueError('poll_interval must be one of 10,15,30,60,120,300')
     self.manager.poll_interval=n;save_config(self.config_path,n);out['poll_interval']=n;self.backup_later('poll_config')
    if len(out)==1:raise ValueError('no supported config field')
    return self.send_json(out)
   if p=='/api/ha':
    r=self.ha.save(self.json_body());self.backup_later('ha_config');return self.send_json(r)
   self.send_error(404)
  except Exception as e:self.send_json({'error':str(e)},400)
 def do_POST(self):
  p=urlparse(self.path).path
  try:
   if p=='/api/auth/login':
    body=self.json_body();token=self.auth.login(body.get('password',''))
    if not token:return self.send_json({'error':'관리자 암호가 올바르지 않습니다'},401)
    return self.send_json({'ok':True,**self.auth.status(token)},200,[self.cookie_header(token)])
   if not self.require_auth():return
   if p=='/api/auth/logout':
    self.auth.logout(self.session_token());return self.send_json({'ok':True},200,[self.cookie_header('',0)])
   if p=='/api/logs/clear':UI_LOG.clear();log.info('web log buffer cleared by administrator');return self.send_json({'ok':True})
   mdev=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/(remove|restore|delete)',p)
   if mdev:
    mac=mdev.group(1).lower();action=mdev.group(2)
    if action=='remove':
     d=self.store.remove_device(mac)
     if self.ha:self.ha.remove_device(mac)
     self.backup_later('device_hide');return self.send_json({'ok':True,'device':self.manager.decorate_device(d)})
    if action=='restore':
     d=self.store.restore_device(mac)
     if self.ha:self.ha.publish_discovery(mac);self.ha.publish_device(mac,True);self.ha.publish_diagnostics(mac,self.manager.diagnostic_view(mac),True)
     self.backup_later('device_show');return self.send_json({'ok':True,'device':self.manager.decorate_device(d)})
    # Permanent delete is intentionally separate from hide/show and destroys energy continuity.
    f=asyncio.run_coroutine_threadsafe(self.manager.delete_device_permanently(mac),self.loop);f.result(5);self.backup_later('device_permanent_delete');return self.send_json({'ok':True,'deleted':True,'mac':mac})
   if p=='/api/backup':return self.send_json({'ok':True,'backup':self.backup_now('manual')})
   if p=='/api/ha/test':
    r=self.ha.test_connection(self.json_body(),5.0);return self.send_json(r,200 if r.get('ok') else 503)
   if p=='/api/ha/sync':
    if not self.ha.connected:return self.send_json({'error':'HA MQTT disconnected'},503)
    return self.send_json({'ok':bool(self.ha.sync_all())})
   m=re.fullmatch(r'/api/firmware/prepare/(official-111|official-110|official-66)',p)
   if m:
    prof=self.firmware.prepare_official(m.group(1));self.backup_later('firmware_prepare');return self.send_json({'ok':True,'profile':prof,'firmware':self.firmware.status()})
   if p=='/api/firmware/custom':
    name=self.headers.get('X-Filename','custom.fwr');wire=self.raw_body(self.firmware.MAX_FWR_BYTES);prof=self.firmware.add_custom(wire,name);self.backup_later('firmware_custom');return self.send_json({'ok':True,'profile':prof,'firmware':self.firmware.status()})
   m=re.fullmatch(r'/api/firmware/custom/(custom-[0-9a-f]{16})/delete',p)
   if m:
    self.firmware.delete_custom(m.group(1));self.backup_later('firmware_custom_delete');return self.send_json({'ok':True,'firmware':self.firmware.status()})
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/childlock/refresh',p)
   if m:
    mac=m.group(1).lower();f=asyncio.run_coroutine_threadsafe(self.manager.childlock(mac,None,source='web-refresh'),self.loop)
    try:r=f.result(10)
    except concurrent.futures.TimeoutError:return self.send_json({'ok':False,'state':'indeterminate','error':'Child Lock backend deadline exceeded'},504)
    return self.send_json(r,200 if r.get('ok') else (504 if r.get('state')=='device_timeout' else 409))
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/ota',p)
   if m:
    body=self.json_body();job=self.firmware.queue_job(m.group(1).lower(),body.get('profile_id'));return self.send_json({'ok':True,'job':job},202)
   if p=='/api/firmware/ota-all':
    body=self.json_body();pid=body.get('profile_id') or self.firmware.target_profile_id();macs=[d['mac'] for d in self.store.devices() if d.get('online') and not d.get('removed') and self.firmware.decorate_device(d).get('firmware_action')]
    return self.send_json({'ok':True,'jobs':self.firmware.queue_many(macs,pid)},202)
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/outlets/([0-4])/power',p)
   if m:
    mac=m.group(1).lower();selector=int(m.group(2));body=self.json_body()
    if type(body.get('on')) is not bool:raise ValueError('on must be JSON boolean true/false')
    f=asyncio.run_coroutine_threadsafe(self.manager.power(mac,selector,body['on'],source='web'),self.loop)
    try:r=f.result(30)
    except concurrent.futures.TimeoutError:return self.send_json({'ok':False,'state':'indeterminate','error':'backend control deadline exceeded; transaction outcome unknown'},504)
    status=200 if r.get('ok') else (504 if r.get('state')=='indeterminate' else 409);return self.send_json(r,status)
   m=re.fullmatch(r'/api/devices/([0-9a-fA-F]{12})/refresh',p)
   if m:
    f=asyncio.run_coroutine_threadsafe(self.manager.refresh(m.group(1).lower()),self.loop);return self.send_json({'ok':True,'records':f.result(6)})
   self.send_error(404)
  except ConnectionError as e:self.send_json({'error':str(e)},503)
  except Exception as e:self.send_json({'error':str(e)},400)

def load_config(path):
 try:n=int(json.loads(path.read_text()).get('poll_interval',DEFAULT_POLL));return n if n in ALLOWED_POLL else DEFAULT_POLL
 except Exception:return DEFAULT_POLL
def save_config(path,n):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'poll_interval':n},indent=2)+'\n')

async def maintenance_loop(store):
 while True:
  await asyncio.sleep(21600)
  try:
   await asyncio.to_thread(store.maintenance)
   ok,msg=await asyncio.to_thread(BackupManager.verify_db,store.path)
   if not ok:
    log.critical('live DB corruption detected during maintenance: %s; restarting for automatic recovery',msg);os._exit(70)
  except Exception as e:log.warning('storage maintenance failed: %s',e)

async def backup_loop(backup,store):
 while True:
  await asyncio.sleep(backup.INTERVAL_SECONDS)
  try:await asyncio.to_thread(backup.create,store,'scheduled')
  except Exception as e:
   ok,msg=await asyncio.to_thread(BackupManager.verify_db,store.path)
   if not ok:
    log.critical('backup failed and live DB is corrupt: %s; restarting for automatic recovery',msg);os._exit(70)
   log.warning('automatic backup failed but live DB is healthy: %s',e)

def secure_data_permissions(data_dir):
 try:os.chmod(data_dir,0o700)
 except OSError:pass
 try:
  for p in data_dir.rglob('*'):
   if p.is_symlink():continue
   try:os.chmod(p,0o700 if p.is_dir() else 0o600)
   except OSError:pass
 except OSError:pass

async def amain(args):
 root=Path(__file__).resolve().parent;data_dir=Path(args.data);data_dir.mkdir(parents=True,exist_ok=True);secure_data_permissions(data_dir);backup=BackupManager(data_dir);db_path=data_dir/'mttl_up.db';recovery=backup.recover_if_needed(db_path);store=Store(db_path);auth=AuthManager(store);poll=load_config(data_dir/'config.json');mgr=Manager(store,poll);mgr.loop=asyncio.get_running_loop();fw=FirmwareStore(store,data_dir);mgr.firmware=fw;fw.start(mgr.loop,mgr);ha=HAController(store,mgr,data_dir);mgr.ha=ha;ha.bootstrap_from_env();ha.reconfigure();await asyncio.to_thread(backup.create,store,'startup' if recovery.get('action')!='restored' else 'post_recovery');secure_data_permissions(data_dir);mt=asyncio.create_task(maintenance_loop(store),name='storage-maintenance');bt=asyncio.create_task(backup_loop(backup,store),name='verified-backup')
 Web.manager=mgr;Web.store=store;Web.loop=mgr.loop;Web.root=root;Web.config_path=data_dir/'config.json';Web.ha=ha;Web.backup=backup;Web.auth=auth;Web.firmware=fw
 http=ThreadingHTTPServer((args.http_host,args.http_port),Web);th=threading.Thread(target=http.serve_forever,daemon=True,name='http');th.start()
 ota_http=OTAHTTPServer((args.http_host,args.ota_http_port),fw);ota_th=threading.Thread(target=ota_http.serve_forever,daemon=True,name='ota-http');ota_th.start()
 srv=await asyncio.start_server(mgr.handle,args.listen_host,args.listen_port,limit=16384)
 log.info('MTTL UP backend TCP %s:%d / Web HTTP %s:%d / OTA HTTP %s:%d / poll=%ss',args.listen_host,args.listen_port,args.http_host,args.http_port,args.http_host,args.ota_http_port,poll)
 try:
  async with srv:await srv.serve_forever()
 finally:mt.cancel();bt.cancel();ha.stop();await fw.stop();http.shutdown();http.server_close();ota_http.shutdown();ota_http.server_close()
def main():
 os.umask(0o077)
 ap=argparse.ArgumentParser(description='Standalone MTTL-W01 UP/PC-Tool backend')
 ap.add_argument('--listen-host',default='0.0.0.0');ap.add_argument('--listen-port',type=int,default=10086);ap.add_argument('--http-host',default='0.0.0.0');ap.add_argument('--http-port',type=int,default=8080);ap.add_argument('--ota-http-port',type=int,default=10080);ap.add_argument('--data',default=str(Path(__file__).resolve().parent/'data'));ap.add_argument('--debug',action='store_true');a=ap.parse_args()
 level=('DEBUG' if a.debug else os.getenv('LOG_LEVEL','INFO')).strip().upper()
 if level not in {'INFO','DEBUG','WARNING','ERROR'}:level='INFO'
 root=logging.getLogger();root.handlers.clear();root.setLevel(logging.DEBUG)
 fmt=logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
 console=logging.StreamHandler();console.setLevel(getattr(logging,level));console.setFormatter(fmt);root.addHandler(console)
 UI_LOG.setFormatter(fmt);root.addHandler(UI_LOG)
 log_dir=Path(a.data)/'logs';log_dir.mkdir(parents=True,exist_ok=True)
 fileh=logging.handlers.RotatingFileHandler(log_dir/'backend.log',maxBytes=5*1024*1024,backupCount=2,encoding='utf-8');fileh.setLevel(logging.DEBUG);fileh.setFormatter(fmt);root.addHandler(fileh)
 log.info('backend version=%s console_level=%s ui/file capture=DEBUG',BACKEND_VERSION,level);asyncio.run(amain(a))
if __name__=='__main__':main()
