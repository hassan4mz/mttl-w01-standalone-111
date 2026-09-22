## CURRENT STATUS — canonical 1.0.111 / 2026-09-17

- Current production target: **canonical 1.0.111** (`HARDWARE_OTA_BOOT_VERIFIED`). User hardware test confirmed OTA installation and normal boot/operation.
- Official Backend choices: **1.0.111**, hardware-verified **1.0.110 rollback**, and LG U+ **1.0.66 stock-compatible rollback**. 1.0.112/113/114 are historical/withdrawn and hidden from normal selection.
- 1.0.111 FWR SHA256: `7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb`; Device SW `0.1.55`; raw HTTP OTA `49217 -> connect failure -> 80`.
- PC provisioning/OTA intentionally serves **TCP/80 only**; 66/110 use 80 directly and 111 falls back to 80.
- Android 1.5.8 / versionCode 16 is the current setup app. It retains exact 66/110/111 generation, structural custom-FWR validation with three confirmations and cmd103 identity checks, and adds the hardened SoftAP/OTA failure handling documented in Release Notes.
- Automatic OTA is never started merely by selecting/preparing a profile; an explicit administrator queue action is required.

> Historical 1.0.112/113/114 evidence is retained under `re/` and Release Notes. Any legacy references remaining later in this document are historical context, not current target guidance.

# Hardware baseline

- Physical controller: **Realtek RTL8711AF**, Ameba-1 / ARM Cortex-M3.
- Real-device flash RDID: **`C2 20 14`**, Macronix 8-Mbit / 1-MiB SPI NOR.
- Exact flash revision is not asserted: Macronix documentation shows **MX25L8005 and MX25L8006E use the same RDID**.
- Both candidate revisions specify about **100,000 erase/program cycles** and support 4-KiB sector erase.
- `SpicReadIDRtl8195A` and similar names in the binary are common Ameba-1 SDK symbols; they do not change the physical SoC identification.
- Full evidence and diagnostic provenance: `HARDWARE_IDENTIFICATION.md`.

# Backend 기술 설계

## 1. 데이터 모델

장치 식별자는 WLAN MAC이다.

`devices`에는 다음과 같은 lifecycle 정보가 저장된다.

- MAC
- firmware/software version
- peer IP / session ID
- online 상태
- first/last seen
- 사용자 alias
- soft-remove 시각 (`removed_at`)

`channels`는 Outlet 1~4의 사용자 `name`과 누적에너지 연속성에 필요한 기준값을 영속 보관한다. ON/OFF, 순간전력, 온도와 최신 통신 시각은 실행 중 RAM 상태를 사용하며 polling마다 DB에 다시 쓰지 않는다. 기존 DB는 시작 시 `channels.name TEXT`를 additive migration으로 추가한다.

## 2. 장치 / 채널 이름

사용자가 장치 alias 또는 Outlet 1~4의 이름을 지정해도 장치 identity와 Home Assistant `unique_id`는 MAC + selector 기반으로 유지한다. MQTT state/command topic도 이름과 무관하게 고정한다.

따라서 이름 변경은 entity identity, entity history, 누적에너지 continuity를 바꾸지 않는다.

- device alias → Home Assistant device name
- channel name → switch 이름, `<name> Power`, `<name> Energy`, `<name> Temperature`
- 빈 channel name → `Outlet N` fallback

이름 변경 직후 같은 device-level Discovery topic을 retained로 다시 발행한다. HA Entity Registry의 기존 `unique_id`가 같으므로 새 entity가 아니라 기존 entity의 integration-provided name이 갱신된다. HA에서 사용자가 별도 이름/entity_id를 지정한 경우 Registry override는 HA가 유지한다.

Web guest/read-only 응답에도 channel `name`을 포함하되 session/IP/config/raw counter 같은 내부 정보는 계속 제외한다. GETINFO runtime snapshot은 기존 channel 객체를 update하므로 `name`을 제거하지 않는다.

## 3. 장치 숨기기 / 다시 표시 / 완전 삭제

일반 UI의 **장치 숨기기**는 soft-remove다.

삭제하지 않는 정보:

- device row
- channel rows
- `energy_lifetime_wh`
- `energy_highwater_wh`
- energy epoch
- alias

HA에서는 device-level MQTT Discovery topic에 empty retained payload를 발행해 entity를 제거하고, device availability retained payload도 clear한다.

removed 상태의 장치가 TCP로 계속 연결되어 있다면 backend는 통신/누적 기준값을 유지할 수 있지만 Web/HA에는 노출하지 않는다.

**다시 표시** 시 `removed_at=NULL`로 바꾸고 같은 MAC 기반 Discovery를 다시 발행한다. `unique_id`가 바뀌지 않으므로 새로운 임의 ID의 센서를 만들지 않는다.

### Home Assistant recorder/statistics

soft-remove 동안 backend의 cumulative value 자체를 보존하므로 복원 후 energy state는 이전 값보다 작아지지 않는 것이 정상 경로다. 이는 `state_class=total_increasing` sensor의 continuity에 가장 안전하다.

HA에서 Discovery entity가 잠시 제거되어도 Recorder의 과거 DB 자체를 backend가 지우지는 않는다. 복원 시 동일한 unique_id/entity 체계를 사용한다. 단, 사용자가 HA에서 entity registry/statistics를 별도로 삭제하거나 entity_id를 수동 변경한 경우 그 부분은 Home Assistant 정책을 따른다.

## 4. 누적에너지

장치 native energy counter를 backend의 lifetime counter로 연속화한다.

일반 경우:

```text
new raw >= previous highwater
lifetime += new raw - previous highwater
```

여러 Outlet counter가 동시에 낮아지거나 전체가 0으로 초기화되는 패턴은 device counter reset epoch로 처리한다.

TCP가 끊겨 있어도 DB의 lifetime은 그대로 보존한다. 재연결 후 native raw counter가 증가해 있다면 그 증가분을 반영한다.

단, 장치가 backend와 단절된 사이 전력을 사용한 뒤 native counter 자체까지 리셋되어 그 중간값이 사라졌다면 backend가 알 수 없는 에너지는 복원할 수 없다. 이는 protocol/data-source 한계다.

## 5. HA availability

Backend 내부의 실제 TCP online 상태와 HA 표시 상태를 분리한다.

### 장치 TCP 단절

- TCP session 종료 즉시 backend DB `online=0`
- 원격 제어는 즉시 실패
- HA device availability의 `offline` publish는 30초 지연
- 30초 안에 reconnect하면 timer 취소
- reconnect 후 첫 valid snapshot이 들어오면 `online` publish
- 30초 이상 offline이면 HA entity는 unavailable

이 정책은 Wi-Fi roam, AP 순간 장애, TCP reconnect 때문에 HA UI가 불필요하게 unavailable/available로 깜빡이는 것을 줄인다.

유예시간 동안 HA에는 마지막 known state가 보인다. 이는 **실시간 연결됨을 뜻하는 것이 아니라 짧은 장애를 UI에서 debounce한 값**이다. Backend Web UI의 online 표시는 실제 TCP 상태를 따른다.

### Backend/MQTT 자체 장애

Discovery component에는 device availability와 backend bridge availability가 모두 있고 `availability_mode=all`이다.

따라서 backend process 또는 MQTT bridge 자체가 내려가면 HA에서는 즉시 unavailable이 될 수 있다. 이 경우 제어 경로 자체가 존재하지 않으므로 device TCP reconnect debounce와 다르게 취급한다.

## 6. HA 상태 발행

주기 GETINFO에서는 Discovery payload를 매번 다시 발행하지 않는다.

Discovery를 다시 발행하는 시점:

- device identify
- HA MQTT reconnect/birth sync
- alias 변경
- channel name 변경
- 장치 restore

주기 polling에서는 RAM 상태를 갱신하고, HA state topic은 각 센서의 변화량/보고 주기 조건을 만족할 때만 발행한다. ON/OFF는 상태 변화 event를 즉시 반영한다.


### HA device configuration URL / Web deep link

`MTTL_PUBLIC_WEB_URL` 환경변수가 유효한 `http://` 또는 `https://` URL이면 MQTT device Discovery의 `device.configuration_url`을 `<root>/#device=<mac>`으로 설정한다. URL이 비어 있거나 유효하지 않으면 필드를 생략한다.

Web UI는 `#device=<12자리 MAC>`을 읽어 렌더 완료 후 해당 `.device-card`로 한 번만 scroll/focus하고 약 3.2초 강조한다. 5초 상태 갱신 때마다 다시 스크롤하지 않도록 현재 적용한 hash를 RAM에서 기억한다.

이 메타데이터는 장치 표시 링크일 뿐 MQTT topic, `unique_id`, state continuity를 변경하지 않는다.

## 7. TCP 제어 일관성

상세 상태 머신과 race-condition 정책은 다음 문서에 분리했다.

```text
backend/CONTROL_CONCURRENCY.md
```

핵심 원칙:

- device별 single transaction writer
- mutation 자동 retry 금지
- ACK와 observed state 분리
- command 후 fresh GETINFO verification
- request timeout 시 TCP session reset/reconnect
- stale session fencing
- periodic poll은 control보다 낮은 우선순위
- reconnect/MAC identify 직후 GETINFO를 즉시 1회 수행해 observed state를 먼저 동기화
- 이후 periodic GETINFO는 `phase + N×interval` absolute monotonic grid를 사용하며 기본 interval은 15초
- 장치 MAC에서 결정되는 0~2초 고정 phase를 적용해 동시 접속 장치의 GETINFO 시작 시점을 분산
- 최근 성공 GETINFO가 5초 이내면 해당 periodic slot을 skip
- transaction이 진행 중인 periodic 회차와 한 cadence 이상 늦은 stale slot은 쌓거나 catch-up하지 않고 drop
- 선택 진단 polling은 최초 GETINFO 완료 전 시작하지 않고, 다음 정규 GETINFO slot 1초 이내에도 새 진단 transaction을 시작하지 않음

## 8. API 추가 항목

### 별명 변경

```http
PUT /api/devices/<mac>
Content-Type: application/json

{"alias":"거실 멀티탭"}
```

빈 문자열은 기본 이름으로 되돌린다.

### Outlet 이름 변경

```http
PUT /api/devices/<mac>/outlets/<1-4>
Content-Type: application/json

{"name":"NAS"}
```

이름은 64자 이하이며 공백을 정규화한다. 빈 문자열은 `Outlet N` 기본 표시로 되돌린다. 저장 후 HA Discovery를 즉시 다시 발행하되 `unique_id`와 MQTT topic은 변경하지 않는다.

### 장치 숨기기

```http
POST /api/devices/<mac>/remove
```

soft-remove이며 에너지 history는 보존한다.

### 장치 다시 표시

```http
POST /api/devices/<mac>/restore
```

### 저장소 진단

```http
GET /api/storage
```

`GET /api/devices`는 active `devices`와 soft-removed `removed` 배열을 함께 반환한다.

## 9. Runtime 상태 / Energy 영속화

GETINFO는 기본 15초로 유지한다. TCP reconnect 직후에는 즉시 한 번 동기화하고, 이후에는 MAC별 0~2초 고정 phase가 포함된 absolute monotonic grid(`phase + N×15초`)를 따른다. 따라서 GETINFO 처리시간이나 일시적인 event-loop 지연이 다음 poll의 기준시각을 이동시키지 않는다.

정규 slot 직전에 control verification 또는 manual refresh 등으로 성공한 GETINFO가 있으면 마지막 성공 시각 기준 5초 미만인 회차는 생략한다. transaction lock이 사용 중인 회차와 한 주기 이상 늦어진 stale slot도 대기/누적/catch-up하지 않고 버린다. 기존 5초 polling 선택지는 자기 자신이 격회 생략되는 것을 막기 위해 freshness suppression을 비활성화한다.

ON/OFF, 순간전력, 온도, 최신 통신 시각은 RAM에서 최신 상태를 유지하므로 값이 그대로인 polling 때문에 SQLite write가 반복되지 않는다.

누적에너지는 장치 native counter를 기준으로 연속성을 유지하며 다음 경우 DB에 저장한다.

- 4개 Outlet의 미저장 누적 변화량 합계가 10 Wh 이상
- 미저장 energy 변화가 있는 상태로 5분 경과
- counter reset 감지
- 장치 disconnect 시 남은 dirty energy flush

변화가 전혀 없으면 5분이 지나도 같은 energy 값을 다시 저장하지 않는다. 최초 상태 확인에서는 이후 연속 계산을 위한 energy baseline을 저장한다.

Home Assistant Power/Energy는 topic별 마지막 전송값과 시각을 RAM에서 관리한다. 기본값은 Power 0.1 W / 30초, Energy 1 Wh / 60초이며 Web 설정에서 다음 범위를 선택할 수 있다.

- Power 변화량: 0.1 / 0.2 / 0.3 / 0.5 / 1 W
- Energy 변화량: 1 / 2 / 5 / 10 / 20 Wh
- 각각의 보고 주기: 15초 / 30초 / 1분 / 2분 / 5분 / 10분 / 30분

선택 주기가 지났더라도 변화량 기준에 못 미치면 같은 값을 재전송하지 않는다. HA reconnect/birth sync에서는 현재값을 강제로 한 번 동기화한다.

## 10. ALL ON/OFF와 릴레이 하드웨어 제약

물리 전체전원 버튼과 UP TCP 제어는 진입 경로가 다르다.

### 물리 전체전원 버튼

확인 결과 `mttl_button_total_power_callback`가 aggregate/master 동작을 담당하고, BSP tap task 내부에서 relay index를 순차 처리한다.

### 현재 Web ALL ON/OFF

UP TCP parser에 확인된 개별 명령은:

```text
up:onoff:1:on|off
...
up:onoff:4:on|off
```

이며 확인된 `up:onoff:0` master wire command는 없다. 따라서 backend의 selector 0은 wire selector가 아니라 내부 aggregate 의미다.

Backend는 ALL 요청을 다음처럼 수행한다.

```text
Outlet 1 mutation + ACK
Outlet 2 mutation + ACK
Outlet 3 mutation + ACK
Outlet 4 mutation + ACK
GETINFO 1..4 verification
```

펌웨어 UP parser의 `onoff` branch는 dispatcher index 11/12를 사용한다.

- index 11 -> `0x1001A486` -> `mttl_relay_drive_set_path (0x1001A04E)`
- index 12 -> `0x1001A4AE` -> `mttl_relay_drive_reset_path (0x1001A088)`

각 relay drive path는 runtime counter를 2로 두며 50 ms tap timer 기준 약 **100 ms latching-relay drive window**다. UP parser도 on/off 응답 처리 후 `vTaskDelay(100)`을 수행하고 다음 command parse로 돌아간다.

즉 backend가 다음 명령을 빨리 보내더라도 TCP buffer에 대기할 뿐, firmware task가 이전 relay의 약 100 ms drive window를 지나기 전에 다음 `up:onoff`를 처리하지 않는다. 현재 ALL 제어는 4개 relay coil을 동시에 구동하지 않는 보수적인 순차 방식이다.

물리 master 버튼과 결과 의미는 같지만 동일한 one-shot firmware API를 호출하는 것은 아니다. 확인되지 않은 `up:onoff:0`을 추측해서 사용하는 것보다 현재 순차 경로를 유지하는 편이 안전하다.


## 11. Web 관리자 인증

Web 상태 화면은 인증 없이 read-only로 제공한다. 관리 기능은 별도 사용자명 없이 관리자 암호만 사용한다. 최초 암호는 `admin`이며 Web 우상단 **관리자 로그인**에서 암호를 입력하고 **설정 → 관리자 암호**에서 8자 이상의 새 비밀번호로 변경한다.

- password 원문은 저장하지 않는다.
- SQLite `settings`에는 PBKDF2-SHA256 salt/hash만 저장한다.
- 로그인 session token은 memory에만 저장하며 backend restart 시 모두 만료된다.
- cookie는 `HttpOnly; SameSite=Strict`로 발행한다.
- `GET /api/health`, `GET /api/devices`, login/status endpoint는 인증 없이 사용할 수 있다. 제어·설정·HA·백업·세부 관리 API는 인증이 필요하다.
- 기본 암호 사용 중에는 UI에서 변경 경고를 표시한다.

LAN HTTP를 전제로 하므로 cookie에 `Secure` flag는 사용하지 않는다. 인터넷 노출이 필요하면 reverse proxy HTTPS를 security boundary로 둔다.

## 12. Voltage / RSSI 선택 polling

Voltage와 Wi-Fi RSSI는 `up:getinfo:all` payload에 포함되지 않으므로 별도 read-only command로 조회한다.

```text
Voltage : up:power_report:1:vol  -> up:power_report:1:<raw_mV>
RSSI    : up:query:wifirssi     -> up:query:<dBm>
```

정책:

- 장치별 Voltage/RSSI를 각각 enable/disable한다.
- 기본값은 둘 다 OFF이며 OFF 상태에서는 해당 추가 UP request를 보내지 않는다.
- 별도 diagnostic poll 기본값은 60초.
- 허용값: 30 / 60 / 120 / 300 / 600 / 1800초.
- 설정 변경은 HTTP thread에서 device runtime을 직접 건드리지 않고 asyncio device loop로 전달하며 diagnostic poller를 즉시 wake한다.
- normal GETINFO polling과 같은 device transaction lock을 공유해 control/read가 wire에서 교차하지 않는다.
- transaction ID가 없는 protocol 특성상 read timeout은 stale reply를 다음 transaction에 혼입시키지 않도록 resync한다. Periodic GETINFO의 `unacked_at_timeout`은 stale transport 즉시 abort, `acked_after_retrans`는 duplicate request 없이 동일 outstanding response만 제한적으로 grace하며, 나머지는 session invalidate/reconnect한다.
- Voltage raw는 기존 실기 검증과 동일하게 `1 mV/count`로 해석한다.
- RSSI는 signed dBm 정수로 사용한다.

### 저장 정책

SQLite에는 다음 설정만 영속화한다.

```text
diag.<mac>.voltage_enabled
diag.<mac>.rssi_enabled
diag.<mac>.poll_interval
```

실제 `voltage_mv`, `rssi_dbm`, 측정 timestamp는 memory에만 유지한다. 따라서 diagnostic polling을 60초로 켜도 그 자체로 SQLite periodic write를 추가하지 않는다.

### Home Assistant

활성화한 diagnostic만 Discovery component를 생성한다.

- Voltage: `device_class=voltage`, measurement, V
- RSSI: `device_class=signal_strength`, measurement, dBm, diagnostic category

비활성화 시 해당 retained state를 clear하고 Discovery payload에서 component를 제거한다.

## Backend-managed Firmware OTA

정상 운영의 기본 OTA 경로는 SoftAP/PC가 아니라 이미 프로비저닝된 장치의 UP TCP10086 세션이다.

```text
Web / mobile
    -> authenticated Backend API
    -> single OTA queue
    -> device TCP10086: up:ota:<OTA_HOST_IP>
    <- up:ota:ota_ok
    -> device connects Backend host TCP/49217 (111 primary) or TCP/80 (fallback/110/66)
    <- GET /ota.bin
    -> full FWR transfer
    -> device reboot/reconnect
    -> same MAC + target firmware version + fresh GETINFO
```

Backend process는 container TCP10080에서 OTA HTTP를 듣고 Docker가 host TCP49217과 host TCP80을 같은 listener로 전달한다. 따라서 container는 privileged bind capability 없이 `cap_drop: ALL`을 유지한다.

`/ota.bin`은 static public file이 아니다. active job이 없으면 404이고, job 중에도 요청 source IP가 OTA 시작 직전 TCP10086 session의 `peer_ip`와 같아야 한다. global single-worker queue 때문에 동시에 다른 firmware profile을 제공하지 않는다.

Firmware binary는 `/data/firmware/`에 0600으로 저장하고 DB에는 profile metadata와 중요한 OTA state transition만 기록한다. 진행률 샘플을 지속적으로 DB에 쓰지 않는다.

공식 profile:

- `official-111`: UP + Child Lock 1.0.111, SHA256 `7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb`, hardware OTA/boot verified
- `official-110`: UP 1.0.110, SHA256 `318f44567cd90613a3a485a66bb398c80bc60c891699772d438c9cfd6cb33059`, hardware-verified rollback
- `official-66`: LG U+ 1.0.66, SHA256 `d780b578af69d52f3a05191a8e7d91a20e05085a912722327481cd5663682c04`, stock-compatible rollback

공식 1.0.111/1.0.110/1.0.66은 pinned 1.0.105 또는 exact LG U+ 1.0.66 source에서 사용자 환경 내 로컬 생성한다. Custom FWR은 decode/header/model/checksum/version/SHA 검증에 통과해야 등록된다.

OTA는 관리자 명시 동작만 queue에 넣는다. FW version 불일치는 UI 표시 조건일 뿐 자동 update trigger가 아니다. timeout/ACK ambiguity에서는 같은 mutation을 자동 재전송하지 않는다.

### Host OTA port publishing

Application-level OTA listener는 unprivileged container TCP10080이다. 정식 Compose는 host TCP49217(primary for 111)과 TCP80(110/66 + 111 fallback)을 10080으로 publish한다. TCP80 충돌 환경에서는 80 publish만 제거할 수 있지만 그 경우 110/66 OTA와 111 fallback을 잃는다. 두 OTA publish를 모두 제거하면 Backend-managed OTA를 사용할 수 없다. 이 선택은 TCP10086/Web/HA/provisioning과 독립적이다.

### Firmware backup consistency

Verified backup format 2는 SQLite, HA secret/config와 함께 `firmware/*.fwr` 전체를 같은 generation manifest에 넣고 각 SHA256을 검증한다. Restore는 FWR 집합 전체를 snapshot과 동일하게 맞춘 뒤 DB를 마지막에 설치한다. 공식 1.0.111/1.0.110/1.0.66, 사용자 지정 FWR, source는 `/data/firmware-source/`에 별도 보관하며, firmware snapshot에는 생성된 runtime FWR이 포함되므로 복원 직후 firmware profile과 실제 파일 세트가 일치한다. 기존 format-1 bundle은 firmware snapshot 항목이 없으므로 기존 방식대로 호환 복원한다.
### OTA process restart consistency

OTA queue는 RAM에서 직렬 실행되지만 job state는 SQLite에 남는다. Backend가 재시작되면 RAM queue/HTTP state가 사라지므로 startup에서 non-terminal job을 다음처럼 정합화한다.

- `QUEUED`, `PREPARING`: 아직 `up:ota`를 보내기 전이므로 `CANCELLED_RESTART`로 종료한다. 관리자가 다시 시작해도 mutation 중복 위험이 없다.
- `COMMAND_SENT` 이후의 미완료 상태: 명령/flash 결과를 확정할 수 없으므로 `RESULT_UNKNOWN`으로 종료한다. 자동 재전송하지 않는다.
- 이후 같은 MAC이 재접속하고 목표 firmware version + fresh GETINFO가 확인되면 기존 `RESULT_UNKNOWN` job을 `DELAYED_SUCCESS`로 판정할 수 있다.

`up:ota` socket send가 예외로 끝난 경우도 일부 전송 여부를 알 수 없으므로 같은 `RESULT_UNKNOWN` 규칙을 적용한다. reconnect event와 OTA worker task는 timeout/cancel/shutdown에서 항상 정리한다.



## Web Outlet row / Lovelace local control lock

Web UI Outlet은 GitHub `af950833/mttl_w01`의 가로 행 UX를 참고해 `편집 | 이름/누적/온도 | Power | ON/OFF` grid로 표시한다. 이름 column은 `minmax(0,1fr)`의 우선 가변폭이고, 긴 이름은 ellipsis 대신 wrap을 허용한다.

`mttl-up-card.js`의 lock은 Home Assistant entity나 backend 보호설정이 아니라 **카드 내부 accidental-tap guard**다.

- 카드 instance 생성/설정 시 `locked=true`
- lock 상태에서는 1~4 short-press와 ALL service call을 return 처리
- long-press Power more-info는 lock과 무관하게 유지
- unlock은 RAM에만 존재하고 재로드/재생성 시 다시 lock
- `all_control` 설정 기본값은 `true`; `false`면 ALL element를 렌더하지 않고 Total/Meter 2열로 재배치
- 기존 YAML에 `all_control`이 없는 경우 기존 동작 호환을 위해 true로 해석

### HA card lock semantics (2026-09-11)
- Shield icon = card-local UI accidental-tap guard; this is not device Child Lock.
- Padlock icon = physical Child Lock and is rendered only when the corresponding HA Child Lock switch is ON.
- Clicking a displayed Child Lock padlock opens HA More Info; it never directly toggles the lock from the main card.
- Child Lock entity unique IDs remain MAC/bit based; Web UI device/outlet rename only changes discovery display names.
