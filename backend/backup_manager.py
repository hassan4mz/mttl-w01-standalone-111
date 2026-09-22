from __future__ import annotations

import hashlib,json,logging,os,shutil,sqlite3,time,uuid,threading
from datetime import datetime,timezone
from pathlib import Path

log=logging.getLogger('mttl-up.backup')

class BackupError(RuntimeError):pass

class BackupManager:
    """Verified SQLite snapshot rotation and startup recovery.

    A backup is a small directory bundle containing the SQLite online-backup image,
    optional HA secret/config files, all firmware FWR files, and a manifest with checksums.  Buckets are
    GFS-style: recent backups are dense while older backups are sparse.
    """
    INTERVAL_SECONDS=3600
    KEEP={'recent':12,'hourly':24,'daily':14,'weekly':8,'monthly':6}
    CORRUPT_MAX=10

    def __init__(self,data_dir:Path):
        self.data_dir=Path(data_dir)
        self.root=self.data_dir/'backups'
        self.root.mkdir(parents=True,exist_ok=True)
        for k in (*self.KEEP.keys(),'corrupt'):(self.root/k).mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.last_backup=None
        self.last_error=None
        self.last_recovery=None

    @staticmethod
    def sha256(path:Path)->str:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def verify_db(path:Path)->tuple[bool,str]:
        path=Path(path)
        if not path.exists():return False,'missing'
        try:
            # Open read-only so a validation attempt cannot create/modify a damaged DB.
            uri=f'file:{path.as_posix()}?mode=ro&immutable=1'
            con=sqlite3.connect(uri,uri=True,timeout=3)
            try:
                row=con.execute('PRAGMA integrity_check').fetchone()
                msg=str(row[0] if row else 'no result')
                return msg.lower()=='ok',msg
            finally:con.close()
        except Exception as e:return False,f'{type(e).__name__}: {e}'

    def _bucket_names(self,ts:float)->dict[str,str]:
        # Bucket names are UTC so host/container timezone cannot create two naming schemes.
        d=datetime.fromtimestamp(ts,timezone.utc)
        iso=d.isocalendar()
        return {
            'recent':d.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6],
            'hourly':d.strftime('%Y%m%d-%H'),
            'daily':d.strftime('%Y%m%d'),
            'weekly':f'{iso.year}-W{iso.week:02d}',
            'monthly':d.strftime('%Y-%m'),
        }

    def _manifest(self,bundle:Path)->dict:
        try:return json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
        except Exception:return {}

    def _bundle_valid(self,bundle:Path)->tuple[bool,str]:
        m=self._manifest(bundle)
        db=bundle/'mttl_up.db'
        if not m:return False,'manifest missing/invalid'
        ok,msg=self.verify_db(db)
        if not ok:return False,'DB integrity_check: '+msg
        expected=((m.get('files') or {}).get('mttl_up.db') or {}).get('sha256')
        if expected and self.sha256(db)!=expected:return False,'DB sha256 mismatch'
        for name,meta in (m.get('files') or {}).items():
            p=bundle/name
            if not p.exists():return False,f'{name} missing'
            if meta.get('sha256') and self.sha256(p)!=meta['sha256']:return False,f'{name} sha256 mismatch'
        return True,'ok'

    def _atomic_replace_dir(self,src:Path,dst:Path)->None:
        tmp=dst.parent/(dst.name+'.new-'+uuid.uuid4().hex[:8])
        old=dst.parent/(dst.name+'.old-'+uuid.uuid4().hex[:8])
        shutil.copytree(src,tmp)
        if dst.exists():
            os.replace(dst,old)
        os.replace(tmp,dst)
        if old.exists():shutil.rmtree(old,ignore_errors=True)

    def _prune(self,kind:str)->None:
        root=self.root/kind
        rows=[]
        for p in root.iterdir():
            if not p.is_dir() or '.new-' in p.name or '.old-' in p.name:continue
            m=self._manifest(p);rows.append((float(m.get('created_at') or p.stat().st_mtime),p))
        rows.sort(reverse=True)
        for _,p in rows[self.KEEP[kind]:]:shutil.rmtree(p,ignore_errors=True)

    def create(self,store,reason='scheduled',ts:float|None=None)->dict:
        with self.lock:return self._create_locked(store,reason,ts)

    def _create_locked(self,store,reason='scheduled',ts:float|None=None)->dict:
        ts=float(time.time() if ts is None else ts)
        work=self.root/('.snapshot-'+uuid.uuid4().hex)
        work.mkdir(parents=True)
        try:
            db_out=work/'mttl_up.db'
            dst=sqlite3.connect(db_out)
            try:
                # Store.lock prevents a logical write from spanning the backup boundary.
                with store.lock:store.db.backup(dst)
                dst.commit()
            finally:dst.close()
            ok,msg=self.verify_db(db_out)
            if not ok:raise BackupError('backup integrity_check failed: '+msg)

            files={}
            files['mttl_up.db']={'sha256':self.sha256(db_out),'bytes':db_out.stat().st_size}
            # Keep crypto/config and all prepared FWR files in the same generation as the DB.
            # The firmware set is small; keep all already-prepared FWR files with the DB generation.
            # Active official profiles are canonical 1.0.111 and rollback 1.0.110; legacy files may remain for recovery history.
            for name in ('ha_secret.key','config.json'):
                src=self.data_dir/name
                if src.exists():
                    out=work/name;shutil.copy2(src,out)
                    files[name]={'sha256':self.sha256(out),'bytes':out.stat().st_size}
            fw_src=self.data_dir/'firmware'
            if fw_src.exists():
                for src in sorted(x for x in fw_src.glob('*.fwr') if x.is_file() and not x.is_symlink()):
                    rel=src.relative_to(self.data_dir).as_posix()
                    out=work/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
                    files[rel]={'sha256':self.sha256(out),'bytes':out.stat().st_size}
            manifest={
                'format':2,'firmware_snapshot':True,'created_at':ts,
                'created_utc':datetime.fromtimestamp(ts,timezone.utc).isoformat(),
                'reason':str(reason),'integrity_check':'ok','files':files,
            }
            (work/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

            buckets=self._bucket_names(ts);written={}
            for kind,name in buckets.items():
                target=self.root/kind/name
                self._atomic_replace_dir(work,target)
                written[kind]=str(target)
                self._prune(kind)
            self.last_backup={'created_at':ts,'reason':reason,'buckets':written,'db_sha256':files['mttl_up.db']['sha256']}
            self.last_error=None
            log.info('verified DB backup reason=%s db_sha=%s buckets=%s',reason,files['mttl_up.db']['sha256'][:12],','.join(buckets.values()))
            return dict(self.last_backup)
        except Exception as e:
            self.last_error=str(e);log.error('DB backup failed: %s',e);raise
        finally:shutil.rmtree(work,ignore_errors=True)

    def _candidates(self):
        rows=[]
        for kind in ('recent','hourly','daily','weekly','monthly'):
            for p in (self.root/kind).iterdir():
                if not p.is_dir() or '.new-' in p.name or '.old-' in p.name:continue
                m=self._manifest(p);rows.append((float(m.get('created_at') or 0),p,m))
        # Keep duplicate bucket copies: they are useful redundancy if one directory/file is damaged.
        rows.sort(reverse=True,key=lambda x:x[0])
        return rows

    @property
    def restore_marker(self)->Path:
        return self.data_dir/'.restore_pending.json'

    def _write_restore_marker(self,payload:dict)->None:
        tmp=self.data_dir/('.restore_pending-'+uuid.uuid4().hex[:8]+'.tmp')
        data=(json.dumps(payload,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        with tmp.open('wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.restore_marker)
        try:
            fd=os.open(self.data_dir,os.O_RDONLY);os.fsync(fd);os.close(fd)
        except OSError:pass

    def _stage_restore(self,bundle:Path,reason:str,snapshot_at:float|None=None)->dict:
        bundle=Path(bundle)
        ok,msg=self._bundle_valid(bundle)
        if not ok:raise BackupError(f'backup validation failed: {msg}')
        stage=self.data_dir/('.restore-stage-'+uuid.uuid4().hex)
        shutil.copytree(bundle,stage)
        ok,msg=self._bundle_valid(stage)
        if not ok:
            shutil.rmtree(stage,ignore_errors=True)
            raise BackupError(f'staged backup validation failed: {msg}')
        payload={
            'format':1,'bundle':str(bundle.resolve()),'stage':str(stage.resolve()),
            'reason':str(reason),'snapshot_at':snapshot_at,'prepared_at':time.time(),
        }
        # Marker is the commit journal.  Once it exists, startup recovery must finish
        # this generation before the Store is allowed to open the live database.
        self._write_restore_marker(payload)
        return payload

    def _target_matches_stage(self,stage:Path)->bool:
        m=self._manifest(stage);files=m.get('files') or {}
        for name in ('ha_secret.key','config.json','mttl_up.db'):
            dst=self.data_dir/name;meta=files.get(name)
            if meta is None:
                if dst.exists():return False
                continue
            if not dst.exists() or self.sha256(dst)!=meta.get('sha256'):return False
        if m.get('firmware_snapshot'):
            expected={name for name in files if name.startswith('firmware/') and name.endswith('.fwr')}
            live=set()
            fw=self.data_dir/'firmware'
            if fw.exists():live={x.relative_to(self.data_dir).as_posix() for x in fw.glob('*.fwr') if x.is_file() and not x.is_symlink()}
            if live!=expected:return False
            for name in expected:
                dst=self.data_dir/name
                if not dst.exists() or self.sha256(dst)!=files[name].get('sha256'):return False
        ok,_=self.verify_db(self.data_dir/'mttl_up.db')
        return ok

    def _install_stage(self,payload:dict,db_path:Path)->dict:
        stage=Path(payload['stage']);bundle=Path(payload.get('bundle') or stage)
        if not stage.exists():
            # If power failed after writing the journal but the temporary stage was
            # lost, rebuild it from the still-verified rotation bundle.
            ok,msg=self._bundle_valid(bundle)
            if not ok:raise BackupError(f'pending restore stage missing and source invalid: {msg}')
            stage=self.data_dir/('.restore-stage-'+uuid.uuid4().hex)
            shutil.copytree(bundle,stage);payload['stage']=str(stage.resolve());self._write_restore_marker(payload)
        ok,msg=self._bundle_valid(stage)
        if not ok:raise BackupError(f'pending restore stage invalid: {msg}')

        if self._target_matches_stage(stage):
            quarantine=None
        else:
            quarantine=self._quarantine_live(db_path,payload.get('reason','restore')) if db_path.exists() else None
            for suffix in ('-wal','-shm'):
                stale=Path(str(db_path)+suffix)
                if stale.exists():stale.unlink()
            manifest=self._manifest(stage);files=(manifest.get('files') or {})
            # Restore the complete FWR snapshot (official + custom) before installing the DB.
            # Install sidecar state first and the DB last; the journal makes a crash resumable.
            if manifest.get('firmware_snapshot'):
                dst_fw=self.data_dir/'firmware';dst_fw.mkdir(parents=True,exist_ok=True)
                for old in dst_fw.glob('*.fwr'):old.unlink()
                src_fw=stage/'firmware'
                if src_fw.exists():
                    for src in src_fw.glob('*.fwr'):
                        shutil.copy2(src,dst_fw/src.name)
            for name in ('ha_secret.key','config.json','mttl_up.db'):
                src=stage/name;dst=self.data_dir/name
                if name not in files:
                    if dst.exists():dst.unlink()
                    continue
                tmp=self.data_dir/(name+'.restore-'+uuid.uuid4().hex[:8])
                with src.open('rb') as fi,tmp.open('wb') as fo:
                    shutil.copyfileobj(fi,fo);fo.flush();os.fsync(fo.fileno())
                try:shutil.copystat(src,tmp)
                except OSError:pass
                os.replace(tmp,dst)
            try:
                fd=os.open(self.data_dir,os.O_RDONLY);os.fsync(fd);os.close(fd)
            except OSError:pass

        ok2,msg2=self.verify_db(db_path)
        if not ok2:raise BackupError('restored DB failed integrity_check: '+msg2)
        if not self._target_matches_stage(stage):raise BackupError('restored generation checksum mismatch')
        result={
            'at':time.time(),'from':str(bundle),'snapshot_at':payload.get('snapshot_at'),
            'quarantine':str(quarantine) if quarantine else None,'reason':payload.get('reason'),
        }
        # Commit is complete only after validation.  Removing the marker is last.
        try:self.restore_marker.unlink()
        except FileNotFoundError:pass
        shutil.rmtree(stage,ignore_errors=True)
        self.last_recovery=result
        return result

    def _resume_pending_restore(self,db_path:Path)->dict|None:
        if not self.restore_marker.exists():return None
        try:payload=json.loads(self.restore_marker.read_text(encoding='utf-8'))
        except Exception as e:raise BackupError(f'invalid pending restore journal: {e}') from e
        log.critical('resuming interrupted DB restore from=%s',payload.get('bundle'))
        result=self._install_stage(payload,db_path)
        log.critical('interrupted DB restore completed from=%s',result.get('from'))
        return result

    def _quarantine_live(self,db_path:Path,why:str)->Path:
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        q=self.root/'corrupt'/stamp;q.mkdir(parents=True)
        for suffix in ('','-wal','-shm'):
            src=Path(str(db_path)+suffix)
            if src.exists():shutil.move(str(src),q/src.name)
        (q/'reason.txt').write_text(str(why)+'\n',encoding='utf-8')
        rows=sorted((x for x in (self.root/'corrupt').iterdir() if x.is_dir()),key=lambda x:x.stat().st_mtime,reverse=True)
        for old in rows[self.CORRUPT_MAX:]:shutil.rmtree(old,ignore_errors=True)
        return q

    def recover_if_needed(self,db_path:Path)->dict:
        """Validate current DB before Store opens it. Restore newest valid bundle if corrupt."""
        with self.lock:return self._recover_locked(db_path)

    def _recover_locked(self,db_path:Path)->dict:
        db_path=Path(db_path)
        resumed=self._resume_pending_restore(db_path)
        if resumed is not None:return {'action':'resumed_restore',**resumed}
        if not db_path.exists():return {'action':'new','reason':'database does not exist'}
        ok,msg=self.verify_db(db_path)
        if ok:return {'action':'ok','integrity_check':'ok'}

        log.critical('live DB failed integrity_check/open: %s; searching verified backups',msg)
        valid=None
        for created,p,m in self._candidates():
            bok,bmsg=self._bundle_valid(p)
            if bok:
                valid=(created,p,m);break
            log.warning('skip invalid backup %s: %s',p,bmsg)
        if valid is None:
            # Never silently replace user data with an empty DB. Preserve evidence and fail.
            raise BackupError(f'live DB corrupt ({msg}) and no valid automatic backup exists')

        created,bundle,manifest=valid
        payload=self._stage_restore(bundle,'automatic recovery: '+msg,created)
        result=self._install_stage(payload,db_path)
        log.critical('automatic DB recovery succeeded from=%s corrupt_saved=%s',bundle,result.get('quarantine'))
        return {'action':'restored',**result}

    def restore_bundle(self,bundle:Path,db_path:Path|None=None,reason='manual restore')->dict:
        with self.lock:
            db_path=Path(db_path or (self.data_dir/'mttl_up.db'));bundle=Path(bundle)
            m=self._manifest(bundle);created=float(m.get('created_at') or 0) if m else None
            payload=self._stage_restore(bundle,reason,created)
            return self._install_stage(payload,db_path)

    def status(self)->dict:
        with self.lock:return self._status_locked()

    def _status_locked(self)->dict:
        counts={}
        for kind in self.KEEP:
            counts[kind]=sum(1 for p in (self.root/kind).iterdir() if p.is_dir() and '.new-' not in p.name and '.old-' not in p.name)
        candidates=self._candidates()
        latest=None
        if candidates:
            created,p,m=candidates[0];ok,msg=self._bundle_valid(p)
            latest={'created_at':created,'path':str(p),'valid':ok,'check':msg,'reason':m.get('reason')}
        return {'enabled':True,'interval_seconds':self.INTERVAL_SECONDS,'keep':dict(self.KEEP),'counts':counts,'latest':latest,'last_backup':self.last_backup,'last_error':self.last_error,'last_recovery':self.last_recovery}
