from __future__ import annotations
"""Tuya Cloud bridge for MTTL-W01.

Mirrors this backend's device state to a Tuya Smart / Life smart-home account
over the official Tuya OpenAPI (https://openapi.tuyacn.com or
https://openapi-usa.tuya.com). Once mirrored, the strips appear in the Tuya
app and can be linked to Alexa and Google Home from the Tuya app without any
additional hub, and can also be controlled directly through the Tuya cloud
API itself.

Direction of control:
  * Tuya app / Alexa / Google Home -> Tuya cloud -> virtual device here ->
    LAN TCP control of the real strip (Manager.power via the backend loop).
  * Real strip state changes -> polled snapshot -> pushed to Tuya cloud as
    dp updates so the cloud mirror stays accurate.

The Tuya custom "Smart Life IoT" plan includes a limited free API quota,
which is sufficient for a handful of power strips polling at normal rates.
"""
import asyncio, concurrent.futures, hashlib, hmac, json, logging, os, re, threading, time
import urllib.error, urllib.request
from pathlib import Path

log = logging.getLogger('mttl-up.tuya')

ENDPOINTS = {
    'data': {'cn': 'https://openapi.tuyacn.com', 'us': 'https://openapi-tuyaus.com',
             'eu': 'https://openapi-eutuya.com', 'in': 'https://openapi-intuya.com',
             'sg': 'https://openapi-sg.iotgw.com'},
    'wx': {'cn': 'https://wx.tuyacn.com', 'us': 'https://wx-usa.tuya.com',
           'eu': 'https://wx-eu.tuya.com', 'in': 'https://wx-in.iotgw.com',
           'sg': 'https://wx.tuyacn.com'},
}
DEVICES_GET = '/v1.0/devices'
DEVICE_SCHEMA = '/v1.0/thingmodels/schema/%s'
DEVICE_DETAIL = '/v1.0/devices/%s'
DEVICE_STATUS = '/v1.0/devices/%s/status'
DEVICE_FUNCTIONS = '/v1.0/devices/%s/functions'
VIRTUAL_CREATE = '/v4.0/device-virtual/virtual-devices'
TOKEN_URL = '/v1.0/token?grant_type=1'
API_EXPIRY_SKEW = 60.0
TOKEN_REFRESH_AHEAD = 300.0
HTTP_TIMEOUT = 15.0
SYNC_INTERVAL_DEFAULT = 20.0
MIN_SYNC_INTERVAL = 5.0
MAX_DEVICES_PER_BRIDGE = 8


def _env_bool(name, default=False):
    v = os.getenv(name)
    if v is None: return default
    return v.strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


class TuyaAPIError(RuntimeError):
    def __init__(self, msg, code=None, status=None):
        super().__init__(msg); self.code = code; self.status = status


class TuyaClient:
    """Minimal signed Tuya OpenAPI v2.0 client (stdlib only)."""

    def __init__(self, access_id, access_secret, region='auto'):
        self.access_id = str(access_id or '').strip()
        self.access_secret = str(access_secret or '').strip()
        self.region = str(region or 'auto').strip().lower()
        if self.region not in ENDPOINTS['data']: self.region = 'auto'
        self._token = None; self._token_expire = 0.0; self._lock = threading.RLock()

    @staticmethod
    def _sha256(b): return hashlib.sha256(b).hexdigest()

    def _base_url(self):
        if self.region != 'auto': return ENDPOINTS['data'][self.region]
        return ENDPOINTS['data']['cn']

    def _signed_request(self, method, path, body_obj=None, retry=True):
        body = json.dumps(body_obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8') if body_obj is not None else b''
        nonce = hashlib.md5(os.urandom(16)).hexdigest()
        t = str(int(time.time() * 1000))
        token = self._token or ''
        # Tuya OpenAPI v2.0 signing: stringToSign = client_id[+access_token]
        # + timestamp + nonce [+ SHA256(body)] + URI; HMAC key = secret[+token].
        # GET/DELETE requests are signed without a content hash.
        content_hash = self._sha256(body) if body else ''
        sign_str = self.access_id + (token or '') + t + nonce + content_hash + path
        hmac_key = (self.access_secret + token) if token else self.access_secret
        signature = hmac.new(hmac_key.encode(), sign_str.encode(), hashlib.sha256).hexdigest().upper()
        url = self._base_url() + path
        req = urllib.request.Request(url, data=body if body else None, method=method.upper())
        req.add_header('client_id', self.access_id)
        req.add_header('sign', signature)
        req.add_header('t', t)
        req.add_header('nonce', nonce)
        req.add_header('Content-Type', 'application/json; charset=utf-8')
        req.add_header('Sign-Method', 'HMAC-SHA256')
        if token: req.add_header('access_token', token)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                payload = json.loads(r.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            try: payload = json.loads(e.read().decode('utf-8') or '{}')
            except Exception: payload = {}
            payload.setdefault('success', False)
            payload['_http_status'] = e.code
        except Exception as e:
            raise TuyaAPIError(f'Tuya network error: {e}')
        if payload.get('code') == 1004 and retry:  # invalid/expired access token
            self.refresh_token(force=True)
            return self._signed_request(method, path, body_obj, retry=False)
        if not payload.get('success', False):
            status = payload.get('_http_status') or (400 if payload.get('code') not in (None, 'ns_ok') else 200)
            raise TuyaAPIError(f"Tuya API error {payload.get('code')}: {payload.get('msg')}", payload.get('code'), status)
        return payload.get('result')

    def refresh_token(self, force=False):
        with self._lock:
            if not force and self._token and time.time() < self._token_expire - TOKEN_REFRESH_AHEAD:
                return self._token
            body = self._signed_request('GET', TOKEN_URL, None, retry=False)
            self._token = body['access_token']
            self._token_expire = time.time() + float(body.get('expire_time', 3600)) - API_EXPIRY_SKEW
            log.info('Tuya access token refreshed expire_in=%ss', int(float(body.get('expire_time', 3600))))
            return self._token

    # public wrappers -----------------------------------------------------
    def get(self, path): return self._signed_request('GET', path)
    def post(self, path, body): return self._signed_request('POST', path, body)
    def put(self, path, body): return self._signed_request('PUT', path, body)
    def delete(self, path): return self._signed_request('DELETE', path)


class TuyaWebhookAuth:
    """Validates inbound Tuya webhook callback signatures (cloud -> local)."""
    def __init__(self, secret):
        self.secret = str(secret or '').strip()
    def verify(self, timestamp, nonce, signature, body_bytes=None):
        if not self.secret: return False
        try: ts = int(timestamp)
        except Exception: return False
        if abs(time.time() - ts / 1000.0) > 300: return False  # reject stale/invalid stamps
        content_hash = hashlib.sha256(body_bytes or b'').hexdigest()
        sign_str = self.secret + (str(timestamp) or '') + (nonce or '') + content_hash
        expected = hmac.new(self.secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest().upper()
        return bool(signature) and hmac.compare_digest(expected, signature.upper())


class TuyaBridge:
    """Keeps a set of Tuya cloud virtual devices mirroring local MTTL strips."""

    SETTING_PREFIX = 'tuya.'
    KEYS = ('enabled', 'access_id', 'access_secret_enc', 'region', 'biz_code',
            'virtual_ids', 'bootstrap_done')

    def __init__(self, store, manager, data_dir: Path, secret_box):
        self.store = store; self.manager = manager; self.secret = secret_box
        self.loop = None
        self.client = None
        self.lock = threading.RLock()
        self.last_error = None; self.last_sync_at = None
        self.connected = False          # last API call succeeded
        self._stop = threading.Event()
        self._thread = None
        self._last_pushed = {}          # mac -> {dpcode: value} dedupe cache
        self._pending = {}              # (mac, selector) -> (on, monotonic deadline)
        self._virtual_ids_cache = None
        self._client_lock = threading.RLock()
        self._rate_window = []          # recent control timestamps for free-tier fairness

    # ---- settings persistence --------------------------------------------
    def _get(self, key, default=None):
        v = self.store.setting_get(self.SETTING_PREFIX + key)
        return default if v is None else v

    def config(self, redact=True):
        secret = self._get('access_secret_enc')
        plain = ''
        if secret:
            try: plain = self.secret.decrypt(secret)
            except Exception: plain = ''
        cfg = {
            'enabled': self._get('enabled') == '1',
            'access_id': self._get('access_id', ''),
            'access_secret_set': bool(plain),
            'region': self._get('region', 'auto'),
            'biz_code': self._get('biz_code', ''),
            'virtual_device_count': len(self.virtual_map()),
            'sync_interval_s': SYNC_INTERVAL_DEFAULT,
            'connected': self.connected,
            'last_error': self.last_error,
            'last_sync_at': self.last_sync_at,
            'webhook_enabled': self._get('webhook_enabled') == '1',
            'webhook_token_set': bool(self._get('webhook_token')),
        }
        if not redact: cfg['access_secret'] = plain
        return cfg

    def save(self, body):
        old = self.config(redact=False)
        enabled = bool(body['enabled']) if 'enabled' in body else old['enabled']
        access_id = str(body.get('access_id', old['access_id'] or '')).strip()
        region = str(body.get('region', old['region'] or 'auto')).strip().lower()
        biz_code = str(body.get('biz_code', old['biz_code'] or '')).strip()
        if region not in ENDPOINTS['data']: region = 'auto'
        if enabled:
            if not access_id: raise ValueError('Tuya Access ID (client id)가 필요합니다')
            if not (body.get('access_secret') or old['access_secret']): raise ValueError('Tuya Access Secret이 필요합니다')
        persisted = {}; deletes = []
        persisted[self.SETTING_PREFIX + 'enabled'] = '1' if enabled else '0'
        persisted[self.SETTING_PREFIX + 'access_id'] = access_id
        persisted[self.SETTING_PREFIX + 'region'] = region
        persisted[self.SETTING_PREFIX + 'biz_code'] = biz_code
        if body.get('clear_secret'): deletes.append(self.SETTING_PREFIX + 'access_secret_enc')
        elif body.get('access_secret') not in (None, ''):
            persisted[self.SETTING_PREFIX + 'access_secret_enc'] = self.secret.encrypt(str(body['access_secret']))
        webhook_enabled = bool(body['webhook_enabled']) if 'webhook_enabled' in body else (self._get('webhook_enabled') == '1')
        persisted[self.SETTING_PREFIX + 'webhook_enabled'] = '1' if webhook_enabled else '0'
        if webhook_enabled and not self._get('webhook_token'):
            persisted[self.SETTING_PREFIX + 'webhook_token'] = os.urandom(24).hex()
        self.store.setting_apply(persisted, deletes)
        with self.lock:
            self.client = None; self.connected = False; self._last_pushed.clear(); self._virtual_ids_cache = None
        if enabled and self.loop is None:
            raise RuntimeError('backend event loop not ready')
        if enabled:
            self.reconnect()
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run_forever, name='tuya-cloud-sync', daemon=True)
                self._thread.start()
        else:
            self.stop()
        return self.public_config()

    def public_config(self): return self.config(True)

    def bootstrap_from_env(self):
        if self._get('bootstrap_done') == '1': return False
        if any(self._get(k) is not None for k in ('enabled', 'access_id', 'access_secret_enc')):
            self.store.setting_set(self.SETTING_PREFIX + 'bootstrap_done', '1'); return False
        aid = (os.getenv('TUYA_ACCESS_ID') or '').strip()
        sec = (os.getenv('TUYA_ACCESS_SECRET') or '').strip()
        if not aid or not sec: return False
        vals = {self.SETTING_PREFIX + 'enabled': '1' if _env_bool('TUYA_ENABLED', True) else '0',
                self.SETTING_PREFIX + 'access_id': aid,
                self.SETTING_PREFIX + 'access_secret_enc': self.secret.encrypt(sec),
                self.SETTING_PREFIX + 'region': (os.getenv('TUYA_REGION') or 'auto').strip().lower(),
                self.SETTING_PREFIX + 'biz_code': (os.getenv('TUYA_BIZ_CODE') or '').strip()}
        self.store.setting_apply(vals)
        log.info('Tuya cloud settings bootstrapped from environment')
        return True

    # ---- virtual device map ------------------------------------------------
    def virtual_map(self):
        raw = self._get('virtual_ids')
        if raw is None: return {}
        try: m = json.loads(raw)
        except Exception: return {}
        return m if isinstance(m, dict) else {}

    def _set_virtual_map(self, m):
        self.store.setting_set(self.SETTING_PREFIX + 'virtual_ids', json.dumps(m, separators=(',', ':')))

    def _device_dps(self, mac):
        """Return {switch_1..switch_4: True/False} desired switch states for one strip."""
        d = self.store.device(mac)
        if not d: return {}
        chans = {int(c.get('selector', 0)): c for c in d.get('channels') or []
                 if str(c.get('selector', '')).isdigit() and int(c.get('selector', 0)) in range(1, 5)}
        out = {}
        for n in range(1, 5):
            st = chans[n].get('on_state') if n in chans else None
            out[f'switch_{n}'] = bool(st) if st is not None else False
        return out

    # ---- lifecycle ----------------------------------------------------------
    def start(self, loop):
        self.loop = loop
        if self._get('enabled') == '1':
            self.save({'enabled': True})

    def reconnect(self):
        cfg = self.config(redact=False)
        if not cfg['enabled']: return False
        if not cfg['access_id'] or not cfg['access_secret']:
            self.last_error = 'missing credentials'; return False
        self.client = TuyaClient(cfg['access_id'], cfg['access_secret'], cfg['region'])
        try:
            self.client.refresh_token()
            self.connected = True; self.last_error = None
            log.info('Tuya cloud connected region=%s', cfg['region'])
            return True
        except Exception as e:
            self.connected = False; self.last_error = str(e)
            log.warning('Tuya cloud connect failed: %s', e)
            return False

    def stop(self):
        self._stop.set()
        t = self._thread
        self._thread = None
        if t and t.is_alive() and t is not threading.current_thread():
            try: t.join(2.0)
            except Exception: pass
        with self.lock:
            self.client = None; self.connected = False

    def _run_forever(self):
        while not self._stop.wait(SYNC_INTERVAL_DEFAULT):
            try: self.sync_once()
            except Exception as e:
                self.last_error = str(e); log.warning('Tuya sync cycle failed: %s', e)

    # ---- outbound sync (local -> cloud) -------------------------------------
    def sync_once(self):
        cfg = self.config(redact=False)
        if not cfg['enabled']: return
        if self.client is None:
            if not self.reconnect(): return
        ids = self.virtual_map()
        changed = False
        for d in self.store.devices():
            if d.get('removed'): continue
            mac = d['mac']
            vid = ids.get(mac)
            if not vid:
                vid = self.create_virtual_device(mac)
                if vid: ids[mac] = vid; changed = True
            if not vid: continue
            dps = self._device_dps(mac)
            prev = self._last_pushed.get(mac)
            if prev != dps:
                try:
                    with self._client_lock:
                        self.client.put(DEVICE_STATUS % vid, {'commands': [{'dp_id': k, 'code': k, 'value': v} for k, v in dps.items()]})
                    self._last_pushed[mac] = dps
                    log.info('Tuya state pushed device=%s switches=%s', vid, ''.join('1' if dps[f'switch_{n}'] else '0' for n in range(1, 5)))
                except TuyaAPIError as e:
                    if e.code in (2001, '2001'):  # device not exist -> recreate mapping
                        self.remove_mapping(mac); changed = True
                    else:
                        self.last_error = str(e)
                except Exception as e:
                    self.last_error = str(e)
        if changed: self._set_virtual_map(ids)
        self._prune_pending()
        self.connected = True; self.last_sync_at = time.time()

    def remove_mapping(self, mac):
        ids = self.virtual_map(); ids.pop(mac, None)
        self._set_virtual_map(ids)
        self._last_pushed.pop(mac, None)

    # ---- provisioning (local -> cloud virtual device) ------------------------
    def create_virtual_device(self, mac):
        ids = self.virtual_map()
        if mac in ids: return ids[mac]
        d = self.store.device(mac)
        if not d: return None
        name = str(d.get('alias') or f'MTTL-W01 {mac[-6:].upper()}')
        payload = {
            'name': name[:64],
            'category': 'smart_plug',
            'mode': 3,               # cloud-driven virtual device
            'dev_config': [
                {'dp_id': i + 1, 'dp_name': f'switch_{i + 1}', 'dp_type': 'Boolean', 'access_mode': 'rw'}
                for i in range(4)],
            'dev_strategy': '',
        }
        try:
            with self._client_lock:
                res = self.client.post(VIRTUAL_CREATE, payload)
        except TuyaAPIError as e:
            self.last_error = str(e); log.warning('Tuya virtual create failed mac=%s: %s', mac, e); return None
        vid = None
        if isinstance(res, dict): vid = res.get('dev_id') or res.get('device_id') or res.get('id')
        elif isinstance(res, str): vid = res
        if not vid:
            self.last_error = 'unexpected create response'; log.warning('Tuya virtual create returned no dev_id mac=%s', mac); return None
        ids[mac] = str(vid); self._set_virtual_map(ids)
        log.info('Tuya virtual device created mac=%s dev_id=%s', mac, vid)
        return str(vid)

    def delete_virtual_device(self, mac):
        ids = self.virtual_map(); vid = ids.get(mac)
        ok = True
        if vid and self.client:
            try: self.client.delete('/v1.0/devices/%s' % vid)
            except Exception as e: ok = False; self.last_error = str(e)
        self.remove_mapping(mac)
        return ok

    # ---- inbound control (cloud -> local) --------------------------------------
    def send_command(self, mac, selector, on):
        """Queue a local control action originating from Tuya cloud/app/Alexa."""
        if self.loop is None: raise RuntimeError('backend loop not ready')
        fut = asyncio.run_coroutine_threadsafe(
            self.manager.power(mac, selector, bool(on), source='tuya-cloud'), self.loop)
        key = (mac, selector)
        if selector == 0:
            for n in range(1, 5): self._pending[(mac, n)] = (bool(on), time.monotonic() + 25.0)
        else:
            self._pending[key] = (bool(on), time.monotonic() + 25.0)
        now = time.monotonic()
        self._rate_window[:] = [t for t in self._rate_window if now - t < 60.0]
        if len(self._rate_window) >= 30:
            return {'ok': False, 'state': 'busy', 'error': 'Tuya cloud control rate limit exceeded (30 commands/min); retry shortly'}
        self._rate_window.append(now)
        try:
            r = fut.result(30)
        except concurrent.futures.TimeoutError:
            r = {'ok': False, 'state': 'indeterminate', 'error': 'backend control deadline exceeded'}
        # Reflect the outcome in the cloud mirror immediately so the Tuya app,
        # Alexa and Google Home see consistent state without waiting for the
        # next periodic sync cycle.
        try: self.push_state_now(mac)
        except Exception as e: log.debug('Tuya immediate push skipped mac=%s: %s', mac, e)
        return r

    def push_state_now(self, mac):
        vid = self.virtual_map().get(mac)
        if not vid or self.client is None: return False
        dps = self._device_dps(mac)
        with self._client_lock:
            self.client.put(DEVICE_STATUS % vid, {'commands': [{'dp_id': k, 'code': k, 'value': v} for k, v in dps.items()]})
        self._last_pushed[mac] = dps
        return True

    def _prune_pending(self):
        t = time.monotonic()
        for k in [k for k, (_, dl) in self._pending.items() if dl <= t]:
            self._pending.pop(k, None)

    def consume_pending(self, mac, selector):
        p = self._pending.pop((mac, selector), None)
        return p[0] if p else None

    # ---- inbound webhook (Tuya cloud -> local) ---------------------------------
    def webhook_secret(self):
        cfg = self.config(redact=False)
        return cfg['access_secret']

    def webhook_verify(self, headers, body_bytes):
        tok = (headers.get('X-Mttl-Token') or '').strip()
        if tok and self._get('webhook_token') and hmac.compare_digest(tok, self._get('webhook_token')):
            return True
        auth = TuyaWebhookAuth(self.webhook_secret())
        return auth.verify(headers.get('t', ''), headers.get('nonce', ''), headers.get('sign', ''), body_bytes)

    def handle_webhook(self, payload):
        """Process a Tuya device status callback; returns list of control results."""
        dev_id = str(payload.get('devId') or '')
        uid = str(payload.get('uid') or '')
        dp_list = payload.get('status') or payload.get('dps') or {}
        if isinstance(dp_list, dict): items = [{'code': k, 'value': v} for k, v in dp_list.items()]
        elif isinstance(dp_list, list): items = dp_list
        else: items = []
        ids = self.virtual_map()
        mac = next((m for m, v in ids.items() if v == dev_id), None)
        if not mac and uid:
            # Virtual devices sometimes echo their own id in uid; fall back to exact map.
            mac = next((m for m, v in ids.items() if v == uid), None)
        if not mac: return {'handled': False, 'reason': 'unknown device'}
        results = []
        for it in items:
            code = str(it.get('code') or '')
            m = re.fullmatch(r'switch_([1-4])', code)
            if not m: continue
            selector = int(m.group(1)); on = bool(it.get('value'))
            pending = self.consume_pending(mac, selector)
            if pending is not None and pending == on:
                results.append({'selector': selector, 'on': on, 'skipped': 'echo'}); continue
            r = self.send_command(mac, selector, on)
            results.append({'selector': selector, 'on': on, 'result': r})
        return {'handled': True, 'mac': mac, 'results': results}

    # ---- helpers -------------------------------------------------------------
    def test_connection(self, timeout=8.0):
        cfg = self.config(redact=False)
        if not cfg['access_id'] or not cfg['access_secret']:
            return {'ok': False, 'error': 'Access ID와 Access Secret을 먼저 입력하세요'}
        try:
            c = TuyaClient(cfg['access_id'], cfg['access_secret'], cfg['region'])
            c.refresh_token()
            res = c.get(DEVICES_GET + '?page_no=1&page_size=1')
            total = (res or {}).get('total', 0) if isinstance(res, dict) else len(res or [])
            return {'ok': True, 'region': cfg['region'], 'devices_in_cloud': total}
        except TuyaAPIError as e:
            return {'ok': False, 'error': str(e), 'code': e.code}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
