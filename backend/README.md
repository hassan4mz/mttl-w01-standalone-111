# Backend

MTTL-W01 UP Standalone의 Docker backend입니다. Current backend version: **0.2.11**.

## 실행

```bash
cp .env.example .env
./deploy.sh
```

기본 container/service 포트:

- TCP `10086` — 장치 UP 연결
- HTTP `8080` — Web UI/API (production host는 현재 18282 publish)
- TCP `10080` — container 내부 OTA HTTP

정식 Compose OTA publish:

- host `49217 -> 10080` — canonical 1.0.111 primary
- host `80 -> 10080` — 1.0.110/1.0.66 및 1.0.111 fallback

영속 데이터는 `.env`의 `DATA_PATH`에 저장됩니다. Web 관리 기능은 사용자명 없이 관리자 암호를 사용하며 최초 암호는 `admin`입니다. `/api/devices`, `/api/health`는 read-only 공개이고 제어·설정·HA·백업·로그 등 관리 API는 관리자 세션이 필요합니다.

## 현재 firmware profiles

- `UP + Child Lock · 1.0.111` — 현재 운영판 / hardware OTA+boot verified
- `UP · 1.0.110` — hardware-verified rollback
- `LG U+ Stock · 1.0.66` — stock-compatible rollback

운영 DB에는 이 세 official profile만 유지합니다. 실제 FWR은 배포/Docker image에 포함하지 않고, 사용자의 `/data/firmware-source/` source에서 로컬 생성된 결과만 `/data/firmware/`에 저장합니다. 시작 시 과거 1.0.112/1.0.113/1.0.114 profile metadata 및 operational FWR residue를 제거하지만, 기존 OTA job 이력은 삭제하지 않습니다. Production `firmware_patch.py`는 withdrawn 1.0.113을 생성하지 않습니다.

자동 1.0.105 다운로드가 실패하면 host의 `${DATA_PATH}/firmware-source/` (container에서는 `/data/firmware-source/`)에 사용자가 별도 확보한 exact 1.0.105 또는 LG U+ 1.0.66 FWR을 넣고 **선택 펌웨어 준비/검증**을 다시 누릅니다. 1.0.105 SHA는 `f78aee...efa`, LG U+ 1.0.66 SHA는 `d780b5...290c`이며, SHA가 다르면 사용하지 않습니다.

Profile 선택/준비만으로 OTA를 자동 시작하지 않습니다. 관리자 명시 동작만 single sequential OTA queue에 들어갑니다. `/ota.bin`은 idle에서 404이며 active job 동안 선택된 장치에만 제공합니다. 성공 판정은 full transfer + same-MAC reconnect + target version + fresh GETINFO입니다.

## Web log

관리자 Log 페이지는 5,000행 RAM ring + rotating DEBUG file(5MB, current+2 backup)을 사용합니다. Compact/Text/JSON 보기를 제공하며 현재 필터 결과를 plain text로 복사할 수 있습니다. 2초 자동 갱신은 사용자가 과거 로그를 읽는 동안 스크롤을 강제로 아래로 내리지 않습니다.

## 백업 / 복구

```bash
./backup_restore.sh status
./backup_restore.sh backup
./backup_restore.sh list
```

SQLite는 WAL + `synchronous=FULL`을 사용합니다. 상세는 `../docs/OPERATIONS.md`를 참고하세요.

## 문서

- `../README.md` — 사용자/현재 상태
- `../docs/FIRMWARE_AND_PROVISIONING.md` — firmware/OTA/provisioning
- `../docs/OPERATIONS.md` — Docker/DB/log/backup
- `../docs/TECHNICAL_DESIGN.md` — HA/energy/device lifecycle/API
- `CONTROL_CONCURRENCY.md` — TCP 제어 동시성

## GETINFO timeout / RTT 진단

- 주기 GETINFO: 5.0초 timeout
- 초기 동기화/수동 조회/제어 검증: 3.5초 timeout 유지
- `unacked_at_timeout`: 운영 pcap에서 ICMP까지 함께 끊기는 Wi-Fi/netif blackout으로 확인되어 stale TCP transport를 즉시 종료하고 reconnect 공간을 비웁니다. application GETINFO 재전송은 하지 않습니다.
- `acked_after_retrans`: TCP delivery가 확인된 경우에만 같은 outstanding GETINFO 응답을 최대 16초 더 기다립니다. duplicate GETINFO는 보내지 않습니다.
- HA device availability는 30초 grace로 짧은 network handoff를 숨기며 마지막 정상 상태를 유지합니다.
- 현재 장애/재접속 정책 및 장기 검증: `../docs/BACKEND_0.2.9_RESILIENCE_SCENARIOS_2026-09-16.md`
- 1초 이상 RTT slow log 및 runtime `getinfo_*` 카운터 제공
