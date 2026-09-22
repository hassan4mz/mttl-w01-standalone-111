#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE"

compose_json() { docker compose config --format json; }
DATA_PATH=$(compose_json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]["mttl-up"]; print(next(v["source"] for v in s["volumes"] if v.get("target")=="/data"))')
WEB_PORT=$(compose_json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]["mttl-up"]; print(next(str(p["published"]) for p in s["ports"] if int(p.get("target",0))==8080))')
BACKUPS="$DATA_PATH/backups"

list_backups() {
  PYTHONPATH="$HERE" python3 - "$DATA_PATH" <<'PY'
import json,sys
from pathlib import Path
from backup_manager import BackupManager
b=BackupManager(Path(sys.argv[1])); rows=[];seen=set()
for created,p,m in b._candidates():
    # Show one row per logical generation; duplicate GFS copies are hidden in the list.
    key=(created,((m.get('files') or {}).get('mttl_up.db') or {}).get('sha256'))
    if key in seen:continue
    seen.add(key);ok,msg=b._bundle_valid(p)
    rows.append((created,p,m,ok,msg))
for i,(created,p,m,ok,msg) in enumerate(rows,1):
    import datetime
    t=datetime.datetime.fromtimestamp(created).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
    try:rel=p.relative_to(Path(sys.argv[1]))
    except Exception:rel=p
    print(f'{i:2d}. {t}  {m.get("reason","?"):<16}  {"OK" if ok else "BAD"}  {rel}')
if not rows:print('(백업 없음)')
PY
}

case "${1:-status}" in
  status)
    # Backup API is administrator-authenticated. The host CLI reads backup metadata directly.
    PYTHONPATH="$HERE" python3 - "$DATA_PATH" <<'PYSTATUS'
import json,sys
from pathlib import Path
from backup_manager import BackupManager
print(json.dumps(BackupManager(Path(sys.argv[1])).status(),ensure_ascii=False,indent=2))
PYSTATUS
    ;;
  backup)
    # Use SQLite Online Backup directly; no Web administrator credential is required.
    PYTHONPATH="$HERE" python3 - "$DATA_PATH" <<'PYBACKUP'
import json,sqlite3,sys,threading
from pathlib import Path
from backup_manager import BackupManager
root=Path(sys.argv[1])
class StoreView:
    def __init__(self,path):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(path,timeout=5)
s=StoreView(root/'mttl_up.db')
try:
    print(json.dumps(BackupManager(root).create(s,'manual-cli'),ensure_ascii=False,indent=2))
finally:
    s.db.close()
PYBACKUP
    ;;
  list)
    list_backups
    ;;
  restore)
    SNAP=${2:-}
    if [ -z "$SNAP" ]; then
      echo "Usage: $0 restore <backup-directory>" >&2
      echo "먼저 '$0 list'로 경로를 확인하세요." >&2
      exit 2
    fi
    case "$SNAP" in
      /*) BUNDLE="$SNAP" ;;
      *) BUNDLE="$DATA_PATH/$SNAP" ;;
    esac
    [ -d "$BUNDLE" ] || { echo "[ERROR] backup directory not found: $BUNDLE" >&2; exit 2; }
    echo "[1/4] Stop backend"
    docker compose stop mttl-up
    echo "[2/4] Validate + preserve current DB + restore: $BUNDLE"
    if ! PYTHONPATH="$HERE" python3 - "$DATA_PATH" "$BUNDLE" <<'PY'
import json,sys
from pathlib import Path
from backup_manager import BackupManager
b=BackupManager(Path(sys.argv[1]));r=b.restore_bundle(Path(sys.argv[2]),reason='manual restore via backup_restore.sh')
print(json.dumps(r,ensure_ascii=False,indent=2))
PY
    then
      echo "[ERROR] restore failed. Backend remains stopped; current data was not silently discarded." >&2
      exit 1
    fi
    echo "[3/4] Start backend"
    docker compose up -d mttl-up
    echo "[4/4] Wait for health"
    i=0
    while [ "$i" -lt 60 ]; do
      if curl -fsS "http://127.0.0.1:$WEB_PORT/api/health" >/tmp/mttl_restore_health.$$ 2>/dev/null; then
        cat /tmp/mttl_restore_health.$$; echo;rm -f /tmp/mttl_restore_health.$$
        echo "[OK] restore complete"
        exit 0
      fi
      i=$((i+1));sleep 1
    done
    rm -f /tmp/mttl_restore_health.$$ 2>/dev/null || true
    echo "[ERROR] backend did not become healthy after restore" >&2
    docker compose logs --tail=100 mttl-up >&2 || true
    exit 1
    ;;
  *)
    echo "Usage: $0 {status|backup|list|restore <backup-directory>}" >&2
    exit 2
    ;;
esac
