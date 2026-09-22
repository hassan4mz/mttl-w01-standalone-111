## CURRENT STATUS — canonical 1.0.111 / 2026-09-17

- Current production target: **canonical 1.0.111** (`HARDWARE_OTA_BOOT_VERIFIED`). User hardware test confirmed OTA installation and normal boot/operation.
- Official Backend choices: **1.0.111**, hardware-verified **1.0.110 rollback**, and LG U+ **1.0.66 stock-compatible rollback**. 1.0.112/113/114 are historical/withdrawn and hidden from normal selection.
- 1.0.111 FWR SHA256: `7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb`; Device SW `0.1.55`; raw HTTP OTA `49217 -> connect failure -> 80`.
- PC provisioning/OTA intentionally serves **TCP/80 only**; 66/110 use 80 directly and 111 falls back to 80.
- Android 1.5.8 / versionCode 16 is the current setup app. It retains exact 66/110/111 generation, structural custom-FWR validation with three confirmations and cmd103 identity checks, and adds the hardened SoftAP/OTA failure handling documented in Release Notes.
- Automatic OTA is never started merely by selecting/preparing a profile; an explicit administrator queue action is required.

> Historical 1.0.112/113/114 evidence is retained under `re/` and Release Notes. Any legacy references remaining later in this document are historical context, not current target guidance.

# 운영 / 저장소 관리

이 문서는 MTTL-W01 UP Standalone을 Docker에서 장기간 운영할 때 필요한 관리 항목을 정리한다.

## 영속 데이터

기본 Compose volume:

```text
${DATA_PATH:-./data}:/data
```

주요 파일:

- `/data/mttl_up.db` — SQLite DB
- `/data/mttl_up.db-wal`, `/data/mttl_up.db-shm` — SQLite WAL 런타임 파일
- `/data/ha_secret.key` — MQTT password 암호화 키
- `/data/config.json` — polling 설정
- Web 관리자 password hash / 장치별 Voltage·RSSI polling 설정 — SQLite `settings` table

컨테이너를 재생성해도 `/data`가 유지되면 장치 이름과 누적에너지 기록은 유지된다.

## SQLite 장기운영 정책

SQLite는 WAL mode를 사용한다.

현재 설정:

- `journal_mode=WAL`
- `synchronous=FULL`
- `busy_timeout=5000 ms`
- `wal_autocheckpoint=256 pages` — 4 KiB page 기준 약 1 MiB
- `journal_size_limit=1 MiB`

Backend 시작 시 기존 WAL을 checkpoint/truncate하고, 이후 약 6시간마다 maintenance를 수행한다.

maintenance 내용:

- connection event 중 30일 초과 자료 삭제
- event table 최대 10,000행 유지
- WAL passive checkpoint
- `PRAGMA optimize`

`channels`는 시간별 history table이 아니다. 실시간 ON/OFF·순간전력·온도·last_seen은 RAM에서 유지하고, DB에는 누적에너지 연속성에 필요한 값만 조건부로 갱신한다. 미저장 누적 변화량 합계 10Wh 또는 dirty 5분에 도달할 때 저장하며 변화가 없으면 주기 write가 발생하지 않는다.

DB 파일은 삭제된 page 때문에 바로 작아지지 않을 수 있지만 해당 page는 SQLite가 재사용한다. 자동 `VACUUM`은 DB를 장시간 잠글 수 있어 운영 중 주기적으로 실행하지 않는다.

### 저장소 상태 확인

```text
GET /api/storage
```

예시 항목:

- `db_bytes`
- `wal_bytes`
- `events`
- `active_devices`
- `removed_devices`
- retention 설정

## 메모리 사용 정책

TCP session당 response queue는 최대 16개로 제한한다.

- ON/OFF ACK queue
- GETINFO response queue
- query response queue

식별 전 physical event buffer도 최대 16개다. 비정상/unsolicited packet이 들어와도 Python queue가 무한히 증가하지 않는다.

Voltage 선택 측정을 위해 `power_report` response queue를 최대 16개로 제한해 사용한다. RSSI는 기존 query queue를 사용한다. 모든 read transaction은 device별 lock으로 직렬화하고 timeout이 나면 transaction ID가 없는 stream을 재사용하지 않는다.

Voltage/RSSI의 **최신 측정값은 RAM에만 유지**한다. DB에는 장치별 활성화 여부와 polling 주기만 저장하므로 60초 진단 polling을 켜도 주기적인 SQLite write는 추가되지 않는다.

### GETINFO scheduler

기본 GETINFO는 15초이며 매 회차 완료시각에서 다시 15초를 세지 않는다. Backend monotonic clock의 `MAC phase(0~2초) + N×15초` absolute grid를 사용한다.

- reconnect/MAC identify 직후 첫 GETINFO는 즉시 수행
- 이후 정규 slot에서 최근 성공 GETINFO가 5초 미만이면 중복 poll 생략
- control/diagnostic 등 transaction lock이 사용 중이면 periodic 회차를 기다리지 않고 drop
- 한 cadence 이상 늦은 stale slot과 밀린 회차는 catch-up하지 않음
- 기존 5초 GETINFO 설정에서는 freshness suppression을 비활성화해 실질 주기가 10초로 늘어나는 것을 방지
- Voltage/RSSI 진단은 최초 GETINFO 성공 후에만 시작하며 정규 GETINFO slot 1초 전에는 새 진단 transaction을 시작하지 않음

Periodic GETINFO timeout은 TCP_INFO 분류를 따른다. `unacked_at_timeout`은 운영 pcap에서 Wi-Fi/netif blackout으로 확인되어 stale transport를 즉시 abort하고 reconnect 공간을 비운다. `acked_after_retrans`만 동일 outstanding GETINFO 응답을 최대 16초 더 기다리며 application-level 재전송은 하지 않는다. 그 외 response stall은 late reply 혼입 방지를 위해 session invalidate/reconnect로 재동기화한다.

## Web 관리자 인증 / 파일 권한

- 관리자 사용자명: 없음
- 최초 암호: `admin`
- 첫 로그인 후 Web 우상단 설정에서 변경 권장
- 새 암호: 8자 이상
- password: PBKDF2-SHA256 hash로 DB 저장, 평문 저장 안 함
- 로그인: HttpOnly + SameSite=Strict session cookie
- `/api/health`만 Docker healthcheck를 위해 인증 없이 허용
- `.env`: `0600`
- `/data`, backup directories: `0700`
- DB/key/config/backup files: `0600`
- process umask: `077`
- container: `cap_drop: ALL`, `no-new-privileges:true`

## 컨테이너 자원 상한

정상 운영 메모리는 수십 MiB 수준이다. Compose에는 비정상 runaway가 호스트 전체에 영향을 주지 않도록 다음 상한을 둔다.

- memory: 256 MiB
- pids: 128

정상 트래픽에서는 이 상한에 근접하지 않는다. 메모리 제한으로 프로세스가 종료되는 비정상 상황에서도 `restart: unless-stopped` 정책으로 컨테이너가 다시 시작된다. 원인은 Docker 로그와 `/api/storage`를 함께 확인한다.

## Docker 로그

Compose의 Docker `json-file` log는 다음과 같이 rotation한다.

- 파일당 최대 10 MiB
- 최대 3개

따라서 Docker daemon의 container log가 무한히 증가하지 않는다.

애플리케이션 로그 레벨:

- `INFO` — 일반 운영 권장
- `DEBUG` — UP RX/TX 문제 확인 시에만 사용

## 백업

가장 안전한 방법은 backend를 잠깐 중지하고 `/data`를 함께 백업하는 것이다.

```bash
docker compose stop mttl-up
cp -a /path/to/data /backup/mttl-up-data
# 또는 tar/rsync 사용
docker compose start mttl-up
```

최소한 다음 두 파일은 함께 백업한다.

```text
mttl_up.db
ha_secret.key
```

`ha_secret.key`를 잃으면 DB에 저장된 MQTT password 암호문을 복호화할 수 없으므로 Web UI에서 password를 다시 입력해야 한다.

## 복구

1. backend 중지
2. 기존 `/data` 별도 보관
3. 백업된 data directory 복원
4. 권한 확인
5. backend 시작
6. `/api/health`와 Web UI 확인

장치 MAC과 DB가 같으면 Home Assistant MQTT unique_id도 동일하게 유지된다.

## 장애 시 확인 순서

```bash
docker compose ps
docker compose logs --tail=200 mttl-up
curl http://127.0.0.1:8080/api/health
# /api/health는 인증 없이 확인 가능
curl http://127.0.0.1:8080/api/health
# /api/storage 등 관리 API는 Web 로그인 세션 필요
```

장치 TCP10086 연결 장애는 `CONTROL_CONCURRENCY.md`의 reconnect/resync 정책을 참고한다.

## 자동 DB 백업 / 자동 복구

SQLite WAL은 갑작스러운 전원 차단에 강하지만 디스크/파일 손상이나 운영 실수까지 대신 복구해 주지는 않는다. Backend는 별도의 검증된 snapshot rotation을 사용한다.

### 자동 백업

Backend가 정상 시작되면 즉시 한 번 snapshot을 만들고 이후 **1시간마다** 갱신한다. 장치 이름/숨기기·다시 표시·완전 삭제, HA MQTT 설정, polling 설정처럼 사용자 설정이 바뀐 경우에는 다음 정기시간을 기다리지 않고 즉시 snapshot을 갱신한다.

보존 세대:

- 최근 snapshot 12개 (설정 변경/수동백업 포함)
- 시간별 24개
- 일별 14개
- 주별 8개
- 월별 6개

DB가 수십 KiB 수준이라 모든 세대를 유지해도 용량 부담은 작다.

각 backup bundle:

```text
mttl_up.db
ha_secret.key      # 존재하는 경우
config.json        # 존재하는 경우
manifest.json
```

`mttl_up.db`는 실행 중 파일을 단순 `cp`하지 않고 **SQLite Online Backup API**로 만든다. snapshot이 rotation에 들어가기 전에 다음을 확인한다.

- `PRAGMA integrity_check = ok`
- 각 파일 SHA256
- manifest 기록

같은 시점 snapshot은 recent/hourly/daily/weekly/monthly bucket에 복제되어 한 bucket 파일이 손상되어도 다른 복사본을 recovery 후보로 사용할 수 있다. 현재 DB 크기 기준 전체 rotation은 대략 수 MiB 규모다.

### 시작 시 자동 복구

Backend는 기존 DB를 열기 전에 `PRAGMA integrity_check`를 수행한다.

```text
DB 정상
  -> 그대로 시작

DB 손상
  -> newest backup부터 integrity/SHA 검증
  -> 가장 최근의 정상 bundle 선택
  -> 손상 DB/WAL/SHM을 backups/corrupt/ 아래에 격리
  -> DB + HA key + config를 같은 generation에서 복원
  -> 복원 DB integrity_check 재검증
  -> 정상 시작
```

정상 backup이 하나도 없으면 빈 DB를 새로 만들어 조용히 데이터가 사라지는 방식은 사용하지 않는다. Backend가 fail-closed되어 로그에 원인을 남긴다.

복원 전에 verified bundle 전체를 staging하고 `.restore_pending.json` journal을 먼저 기록한다. 복구 도중 다시 전원이 끊겨 live DB가 이미 이동됐거나 stage 일부만 남은 경우에도 다음 시작에서 journal을 읽어 동일 generation 복구를 이어간다. 복구 완료 후 DB integrity/SHA를 다시 확인한 뒤에만 journal을 삭제한다.

손상되었거나 수동 복원 직전 보관된 live DB는 `backups/corrupt/`에 최대 10세대까지 남긴다.

운영 중 정기 backup 또는 6시간 maintenance 과정에서 live DB 자체의 integrity failure가 확인되면 process를 종료한다. Docker `restart: unless-stopped`가 다시 시작하고 위 startup recovery를 수행한다.

### 수동 관리

```bash
cd backend

./backup_restore.sh status
./backup_restore.sh backup
./backup_restore.sh list
./backup_restore.sh restore backups/recent/20260902-001511-b9c482
```

수동 restore도 snapshot integrity/SHA를 먼저 확인하고 현재 DB를 `backups/corrupt/`에 보관한 다음 복원한다.

API:

```text
GET  /api/backup
POST /api/backup
GET  /api/storage
```

### 같은 디스크의 한계

이 rotation은 DB 파일 손상, 비정상 종료 후 구조 손상, 운영 실수에는 유효하지만 **스토리지 장치 자체가 고장나는 경우**에는 같은 디스크의 backup도 함께 유실될 수 있다. 중요한 설치에서는 `/data` 또는 `backups/`를 NAS/호스트 백업 시스템으로 추가 백업하는 것이 필요하다.

## Firmware OTA 운영

### Active firmware-store invariant

Operational `firmware_profiles` and `/data/firmware` are kept consistent with the selectable release set: official 1.0.111 / 1.0.110 / 1.0.66, generated official targets; user-provided/downloaded source is kept separately under `/data/firmware-source/` when present. Startup removes legacy 1.0.112/1.0.113/1.0.114 profile metadata and operational FWR residue. Historical OTA job rows remain for audit, and withdrawn firmware analysis remains under the workspace `re/` tree rather than the Backend data store.


설정 → **펌웨어 관리**에서 목표 profile과 장치가 접근할 Backend IPv4를 지정한다. 기본 목표는 canonical 1.0.111이다. 공식 선택지는 1.0.111 / 1.0.110 / 1.0.66이며 profile 선택만으로 OTA는 시작되지 않는다. 공식 profile 파일이 아직 없으면 OTA job의 PREPARING 단계에서 자동 생성/검증된다.

Dashboard에서 현재 버전과 목표 버전이 다를 때만 관리자 OTA 버튼이 표시된다. 더 낮은 목표를 선택하면 `다운그레이드`로 명확히 표시된다. 여러 장치를 선택해도 Backend가 한 대씩 순차 처리한다.

정상 idle 상태에서는 `/ota.bin`이 404여야 한다. Docker host TCP49217은 1.0.111 primary, TCP80은 1.0.110/1.0.66 및 1.0.111 fallback OTA 용도이며 외부 인터넷에 노출하지 않는 것을 권장한다. 가능하면 router/firewall에서 IoT LAN만 접근시키며 Web 8080/TCP10086과 동일한 로컬 관리 영역으로 제한한다.

실제 OTA 성공은 Backend 작업 기록의 `SUCCESS`/`DELAYED_SUCCESS`와 최종 firmware version으로 확인한다. `RESULT_UNKNOWN`이면 자동 재시도를 하지 말고 장치 재접속 상태를 먼저 확인한다.

### Host TCP 80이 이미 사용 중인 설치

정식 Compose는 `49217:10080`과 `80:10080`을 함께 publish한다. 설치 대상 서버에서 TCP 80이 이미 nginx, reverse proxy 등 다른 서비스에 의해 사용 중이면 `docker-compose.yaml`의 `${OTA_PORT:-80}:10080` 항목만 제거/주석 처리한다.

TCP80 publish를 제거해도 canonical 1.0.111은 host TCP49217 primary OTA를 사용할 수 있다. 다만 1.0.110/1.0.66 OTA와 1.0.111의 fallback 경로는 사용할 수 없다. TCP49217 publish까지 제거하면 Backend-managed OTA 전체가 사용할 수 없다. PC SoftAP 직접 OTA는 별도 PC TCP80을 사용한다.

Firmware profile을 사용하는 현재 버전의 verified backup(format 2)은 `/data/firmware/*.fwr`의 **사용자 환경에서 생성된 runtime FWR**과 custom FWR을 DB/profile metadata와 같은 generation으로 보관합니다. Source 1.0.105/LG U+ 1.0.66은 `/data/firmware-source/`에 별도 보관하며 firmware snapshot에는 포함하지 않습니다. 복원 후 source가 다시 필요하면 vendor 1.0.105를 재다운로드하거나 사용자가 exact 1.0.105/1.0.66을 `/data/firmware-source/`에 다시 넣을 수 있습니다.
### OTA 중 Backend가 재시작된 경우

재시작 시 자동으로 같은 OTA 명령을 다시 보내지 않습니다.

- `QUEUED/PREPARING`이던 작업: `CANCELLED_RESTART`로 종료. 장치에 OTA 명령을 보내기 전이므로 관리자 재시작이 안전합니다.
- `COMMAND_SENT` 이후 작업: `RESULT_UNKNOWN`으로 종료. 실제 장치 결과를 먼저 확인해야 합니다.
- 나중에 같은 MAC이 목표 firmware로 재접속하고 GETINFO까지 정상 확인되면 `DELAYED_SUCCESS`로 자동 정정될 수 있습니다.



## Backend 0.2.9 장기 안정성 확인

RTL8195A periodic blackout 자체는 firmware 현상으로 남아 있으므로 backend에서 `transport_loss_start/end/expired`를 30일간 기록한다. 정상 short blackout은 30초 HA availability grace 안에서 회복하고, 45초 command hard deadline을 넘기지 않아야 한다.

운영 판단은 단순 disconnect 횟수보다 다음을 우선한다.

- `transport_loss_expired` 0건
- recovery duration 대부분 < 30초
- backend container unexpected restart 0건
- deferred control 후 duplicate mutation 0건 / observed mismatch 증가 없음
- SQLite integrity/backup 정상

최소 72시간, 권장 7일 동안 확인한다. 상세 SQL 및 scenario matrix는 `BACKEND_0.2.9_RESILIENCE_SCENARIOS_2026-09-16.md` 참조.
