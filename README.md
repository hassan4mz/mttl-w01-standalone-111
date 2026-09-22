## CURRENT STATUS — 2026-09-17

- 운영판: **canonical 1.0.111** (`HARDWARE_OTA_BOOT_VERIFIED`), exact 1.0.110 기준 재구성. 실기 OTA/부팅 정상 확인.
- 안정 rollback: **1.0.110 UP** (hardware verified).
- canonical 1.0.111의 Device SW/SoftAP generation은 **0.1.55**다.
- 1.0.111 HTTP OTA는 **TCP/49217 우선 → connect 실패 시 TCP/80 fallback**이다. HTTP/download/flash는 성공한 연결에서 한 번만 수행한다.
- stock/1.0.110은 OTA 포트가 TCP/80 고정이므로 110→111 최초 업데이트 호환을 위해 Backend/PC의 TCP/80 경로를 유지한다.
- 1.0.112/1.0.113/1.0.114 실험판은 active target에서 제외한 history 상태다.
- Child Lock은 firmware version이 정확히 1.0.111일 때만 Backend/HA에서 노출한다.
- Android SoftAP의 runtime UP 제어는 허용하지 않는다. Device Info는 LGAPMODE cmd103, direct OTA는 지원 세대에서만 별도 OTA 경로를 사용한다.

# MTTL-W01 UP Standalone

- Backend software: **0.2.11**
- Android setup: **1.5.8 / versionCode 16**
- Canonical device firmware: **1.0.111** (`HARDWARE_OTA_BOOT_VERIFIED`)

MTTL-W01 멀티탭을 제조사 Cloud 없이 로컬에서 사용하기 위한 패키지입니다.

### FWR 미포함 배포

배포 ZIP과 Docker image에는 **어떤 `.fwr` 바이너리도 포함하지 않습니다.** PC/Backend는 사용자의 환경에서 1.0.105를 vendor URL에서 직접 받거나, 사용자가 별도 확보한 exact 1.0.105 또는 LG U+ 1.0.66을 source로 사용해 66/110/111을 로컬 생성합니다.

- PC 수동 source 위치: PC 툴과 같은 폴더
- Backend/Docker 수동 source 위치: `/data/firmware-source/`
- source는 SHA256으로 식별하므로 파일명이 달라도 exact LG U+ 1.0.66이면 사용할 수 있습니다.
- 생성 결과는 사용자 환경의 runtime 영역에만 저장되며 배포물에는 들어가지 않습니다.

`MTTL-W01_UP_Standalone_0.2.0_source.zip`은 2026-09-12 배포 당시의 보존된 source artifact이며 Docker build에 필요한 코드/정적 자산을 포함하지만 FWR은 포함하지 않습니다. Web에서 받는 PC Provision ZIP과 Android APK도 FWR을 내장하지 않습니다.

### PC SoftAP 복구 UX

PC OTA/Provision 도구는 SoftAP 자동 감지 실패를 즉시 종료 조건으로 보지 않습니다. 자동 연결이 안 되면 `TONLY_TAP_*`에 연결하도록 안내하고 **Enter 재확인 2회**를 수행한 뒤, 마지막에 `y/n`으로 실제 통신 단계까지 진행할지 선택합니다. SoftAP SSID를 직접 입력할 필요는 없습니다. OTA 후 SoftAP 서비스 복귀가 늦어도 같은 복구 흐름을 사용합니다. 단, OTA/Provision 명령이 이미 장치에 수락됐을 수 있는 모호한 단계는 자동 재전송하지 않습니다.

### Backend 펌웨어 프로필 표시

Web UI의 공식 펌웨어 이름은 기능 중심으로 짧게 표시하고 버전은 뒤에 둡니다. 드롭다운에서 항목을 선택하면 같은 설명 영역에 **기준 버전 대비 변경점**이 즉시 표시됩니다.

- **LG U+ Stock · 1.0.66** — 기준판. LG U+ Stock 호환, TCP/80 OTA. UP 로컬 백엔드, 물리 버튼 상태 즉시 이벤트 보고, Child Lock은 없습니다. 정상 연결 LED 동작도 Stock 그대로이며 공장계열 원복용입니다.
- **UP · 1.0.110** — **1.0.66 대비** UP TCP10086 로컬 백엔드 지원, 물리 버튼으로 Outlet 상태가 바뀌면 `up:event:onoff`를 즉시 보고, 정상 Wi-Fi+UP 연결 상태에서는 주황 상태 LED를 끕니다. Child Lock은 없고 OTA는 TCP/80입니다. Hardware-verified rollback입니다.
- **UP + Child Lock · 1.0.111** — **1.0.110 대비** Master+Outlet 1~4 물리 짧은 누름 Child Lock과 Flash 영속 저장을 추가합니다. 1.0.110의 UP 이벤트 보고와 정상 연결 LED 소등 동작은 유지합니다. SoftAP Device SW는 0.1.55이며 OTA는 TCP/49217 우선, connect 실패 시 TCP/80 fallback입니다. 현재 운영판이며 실기 OTA/부팅을 확인했습니다.

프로필 이름에는 핵심 기능만 표시하고, 버전별 변경점·검증 상태·OTA 방식은 선택 설명과 이 문서에서 확인합니다.

### 확인된 하드웨어

- 메인 SoC: **Realtek RTL8711AF** (Ameba-1 / ARM Cortex-M3)
- Flash: **Macronix 8 Mbit = 1 MiB SPI NOR**, 실기 JEDEC **`C2 20 14`**
- `C2 20 14`는 MX25L8005와 MX25L8006E가 공통으로 사용하므로 exact die revision은 RDID만으로 구분하지 않는다.
- 두 후보 모두 약 **100,000 erase/program cycle** 급이므로 Child Lock 수명 평가는 같은 결론이다.
- 상세 근거: `docs/HARDWARE_IDENTIFICATION.md`

펌웨어의 `Rtl8195A` 계열 함수명은 Realtek Ameba-1 공통 SDK 명칭이다. **MTTL-W01 실물 SoC를 RTL8195A로 표기하지 않는다.**

주요 기능:

- Web에서 콘센트 1~4 ON/OFF 및 전체 ON/OFF
- 소비전력, 누적 소비전력, 온도 확인
- Home Assistant MQTT 연동
- 장치와 Outlet 1~4 개별 이름 지정
- MTTL-UP Home Assistant Lovelace 카드
- 장치 숨기기 / 다시 표시 / 기록 완전 삭제
- Windows / macOS / Linux에서 펌웨어 설치 및 Wi-Fi/backend 설정

## 1. Backend 설치

Docker가 설치된 서버에서:

```bash
cd backend
cp .env.example .env
./deploy.sh
```

기본 Web 주소는:

```text
http://서버IP:8080
```

일반 상태 화면은 로그인 없이 볼 수 있습니다. 관리자 기능의 최초 암호는 `admin`이며, 우상단 **관리자 로그인**에서 암호만 입력합니다. 로그인해도 홈 화면을 그대로 유지하며 제어 기능만 활성화됩니다. 암호를 바꾸려면 **설정 → 관리자 암호**를 사용합니다.

포트를 바꾸려면 `backend/.env`의 `WEB_PORT`를 수정합니다.

## 2. Home Assistant 연결

Web 우상단 **설정 → Home Assistant MQTT**에서 다음 항목을 입력합니다.

- MQTT Broker 주소
- Port
- ID / Password
- 필요하면 TLS

**저장 / 연결**을 누르면 Home Assistant MQTT Discovery로 자동 등록됩니다.

Power/Energy 보고량은 설정에서 조절할 수 있습니다. 기본값은 Power **0.1 W / 30초**, Energy **1 Wh / 1분**입니다. Power 변화량은 0.1/0.2/0.3/0.5/1 W, Energy 변화량은 1/2/5/10/20 Wh를 선택할 수 있고, 각각 15초/30초/1분/2분/5분/10분/30분 보고 주기를 선택할 수 있습니다. 선택 주기마다 변화량을 확인하며 기준에 못 미치면 같은 값을 다시 보내지 않습니다.

등록되는 주요 항목:

- Outlet 1~4 스위치
- 각 Outlet 소비전력 / 누적에너지 / 온도
- 전체 소비전력 / 전체 누적에너지

Outlet 이름을 바꾸면 같은 `unique_id`를 유지한 채 Discovery 이름만 다시 발행합니다. 따라서 이름 변경 때문에 새 HA entity가 생성되거나 누적 history가 끊기지 않습니다. HA에서 entity 이름 또는 entity_id를 직접 사용자 지정한 경우 HA Registry 설정이 우선될 수 있습니다.

### MTTL-UP Lovelace 카드

Backend Web의 **설정 → Home Assistant MQTT → MTTL-UP Lovelace 카드**에서 `mttl-up-card.js`를 받을 수 있습니다. Home Assistant `/config/www/mttl-up-card.js`에 저장하고 대시보드 리소스에 `/local/mttl-up-card.js`를 **JavaScript Module**로 1회 등록합니다.

```yaml
type: custom:mttl-up-card
mac: 001122334455
```

카드는 전체 12자리 MAC을 설정값으로 사용하지만 실제 entity 탐색은 HA Entity Registry의 `unique_id`를 기준으로 합니다. 따라서 HA에서 `switch.xxx` 같은 entity_id를 직접 바꿔도 카드 매핑이 유지됩니다.

카드는 **기본 잠금 상태**로 열립니다. 헤더의 `Online` 왼쪽 자물쇠를 해제한 동안에만 Outlet 1~4와 ALL 제어가 동작하며, 페이지/카드를 새로 열면 다시 잠깁니다. 잠긴 상태에서도 약 0.6초 long-press Power more-info는 사용할 수 있습니다. 카드 편집 UI의 **ALL ON/OFF 제어 표시**를 끄면 ALL 영역 자체를 숨깁니다. 기존 카드 설정은 옵션이 없으면 `all_control: true`로 호환됩니다.

```yaml
type: custom:mttl-up-card
mac: 001122334455
all_control: true
```


### Home Assistant 장치에서 Web 제어 화면 열기

`backend/.env`에 브라우저에서 접근 가능한 Web URL을 지정하면 MQTT Discovery의 `configuration_url`에 장치별 deep link를 넣습니다.

```text
MTTL_PUBLIC_WEB_URL=http://192.168.1.146:18282
```

예를 들어 MAC `d8aa59d24868` 장치는 `http://192.168.1.146:18282/#device=d8aa59d24868`로 연결됩니다. Web UI는 해당 장치 카드로 자동 스크롤하고 잠시 강조합니다. 이 URL은 표시/관리 링크일 뿐 MAC, MQTT topic, HA `unique_id`에는 영향을 주지 않습니다.

## 전압 / Wi-Fi RSSI 선택 측정

전압과 RSSI는 기본 GETINFO와 별도의 UP 요청이 필요하므로 기본값은 **비활성화**입니다. 관리자 로그인 후 각 장치 카드의 **전압 / RSSI 측정**을 펼쳐 장치별로 켤 수 있습니다.

- 전압 ON/OFF 개별 선택
- RSSI ON/OFF 개별 선택
- 진단 polling 기본 60초
- 선택 가능한 주기: 30초 / 60초 / 2분 / 5분 / 10분 / 30분
- 소비전력/상태 GETINFO polling과 별도 주기
- 기본 GETINFO는 15초 **absolute cadence**를 유지하고 장치별 MAC 기반 0~2초 고정 phase로 시작 시점을 분산
- TCP reconnect/MAC identify 직후에는 phase를 기다리지 않고 GETINFO를 즉시 1회 실행해 실제 상태를 동기화
- 정규 slot에서 최근 성공 GETINFO가 5초 미만이면 중복 조회를 skip하고, transaction이 진행 중인 회차도 대기/누적하지 않고 drop
- 밀린 periodic poll은 catch-up하지 않으며 다음 원래 15초 slot로 복귀해 장기 drift를 만들지 않음
- 최신 Voltage/RSSI 값은 RAM에만 보관하여 주기적인 DB write를 추가하지 않음
- 활성화된 센서만 Home Assistant Discovery에 추가

## 3. 장치 / Outlet 이름 변경

관리자 로그인 후 장치명 또는 각 Outlet 이름 옆의 **✎** 버튼을 누르면 원하는 이름을 지정할 수 있습니다. 빈 문자열로 저장하면 기본 이름으로 돌아갑니다.

- 장치 이름: Web 화면과 Home Assistant 기본 device name에 사용
- Outlet 이름: Web의 4채널 카드와 HA switch / Power / Energy / Temperature 기본 이름에 사용
- MAC, MQTT topic, HA `unique_id`는 이름과 무관하게 고정

로그아웃한 read-only Web 화면에도 저장된 Outlet 이름을 그대로 표시합니다. Web 장치 화면의 Outlet은 `편집 | 이름+누적/온도 | 현재 W | ON/OFF` 가로 행으로 표시해 긴 이름에 우선 폭을 주며, 이름이 더 길면 한 줄에서 억지로 잘라내지 않고 줄바꿈합니다. Home Assistant에서 이미 이름을 직접 변경했다면 HA의 사용자 Registry 설정이 우선될 수 있습니다.

## 4. 장치 숨기기 / 다시 표시

**장치 숨기기**는 Web 목록과 Home Assistant에서만 장치를 숨깁니다. 별명, 누적에너지, 측정 설정은 보존됩니다. 숨긴 장치가 다시 연결되어도 자동으로 다시 표시되지는 않습니다.

관리자 화면 아래의 **숨긴 장치 → 다시 표시**를 누르면 기존 데이터와 누적에너지를 그대로 이어서 사용합니다.

더 이상 데이터를 보관할 필요가 없다면 숨긴 장치에서 **기록 완전 삭제**를 사용할 수 있습니다. 이 기능은 별명, 누적에너지, 기록과 장치별 설정을 삭제하며 되돌릴 수 없습니다. 이후 같은 MAC의 장치가 다시 연결되면 새 장치로 등록됩니다.

## 화면 테마

기본값은 **시스템**이며 휴대폰/PC의 라이트·다크 설정을 따릅니다. 상단에서 `시스템 / 라이트 / 다크`를 직접 선택하면 해당 브라우저 쿠키에 저장됩니다.

장치 상태는 자동으로 갱신되지만, 사용자가 펼쳐둔 **전압 / RSSI 측정** 영역과 입력 중인 값은 자동 갱신 때문에 닫히거나 초기화되지 않습니다.

## 5. 잠깐 연결이 끊겼을 때

장치 TCP 연결이 잠깐 끊겨도 Home Assistant가 바로 `사용 불가`로 깜빡이지 않도록 **30초 유예시간**을 둡니다.

- 30초 안에 다시 연결: Home Assistant는 마지막 정상 상태를 그대로 유지
- 30초 이상 연결되지 않음: Home Assistant에서 장치가 `사용 불가`
- 다시 연결: 최신 상태를 읽은 뒤 자동 복구

연결이 끊겨 있는 동안 누적에너지를 0으로 만들지 않습니다. 장치가 다시 연결되면 장치의 누적 카운터를 기준으로 이어서 계산합니다.

## 6. 초기 설치 / PC 직접 OTA

**정상 운영 중 펌웨어 업데이트는 SoftAP가 아니라 관리자 Dashboard의 Backend OTA가 기본 경로입니다.** 이미 프로비저닝되어 홈 Wi-Fi에서 Backend TCP10086에 연결된 장치는 SoftAP 모드로 전환할 필요가 없습니다.

아래 절차는 새 장치 초기 설치, Backend 미등록 장치, 복구 상황에서 사용하는 **PC 직접 SoftAP OTA / Provisioning 경로**입니다.

가장 간단한 PC 설치 명령:

```bash
cd provision
python3 mttl_up_setup.py install
```

Windows에서는:

```text
INSTALL_WINDOWS.bat
```

이 PC 직접 설치 경로에서만 장치를 `TONLY_TAP_*` SoftAP 모드로 만든 뒤 진행합니다.

PC 직접 OTA는 완료됐지만 Wi-Fi/backend 설정 단계로 자동 복귀하지 않는 경우에는 장치를 다시 SoftAP 모드로 만든 뒤 **Provision만 다시 실행**하면 됩니다. 펌웨어 OTA를 다시 할 필요는 없습니다.

## 7. 데이터 위치

Backend 데이터는 기본적으로 `backend/data/` 또는 `.env`의 `DATA_PATH`에 저장됩니다.

중요 파일:

- `mttl_up.db` — 장치/누적에너지/설정 데이터
- `ha_secret.key` — Home Assistant MQTT 비밀번호 복호화 키

백업할 때는 두 파일을 함께 보관하는 것을 권장합니다.

Backend는 SQLite 무결성을 검사한 자동 snapshot을 시간/일/주/월 단위로 로테이션하며, DB 손상이 확인되면 가장 최근의 정상 snapshot으로 자동 복구합니다. 상세 내용은 `docs/OPERATIONS.md`를 참고하세요.

## 자세한 문서

일반 사용에는 아래 문서를 읽지 않아도 됩니다.

- `docs/OPERATIONS.md` — Docker 장기운영, DB/WAL, 로그, 백업
- `docs/TECHNICAL_DESIGN.md` — 장치 lifecycle, HA availability, 에너지 연속성, API
- `docs/FIRMWARE_AND_PROVISIONING.md` — 펌웨어 생성/OTA/Provision 상세
- `backend/CONTROL_CONCURRENCY.md` — TCP 제어 동시성/timeout/race 설계
- `RELEASE_NOTES.md` — 변경 이력

## 1.0.66 대비 1.0.110 변경점

- LG QMS HTTPS 보고 비활성화
- 릴레이 상태 변경 event push 추가 — 물리 버튼 등으로 상태가 바뀌면 다음 GETINFO polling을 기다리지 않고 Backend에 즉시 전달
- 정상 Wi-Fi/Backend 연결 후 반복되던 1.5초 주황 LED 점멸 제거
- 펌웨어 버전 `1.0.110`

## 펌웨어 관리

초기 프로비저닝 이후에는 관리자 Dashboard에서 펌웨어를 관리합니다. 이 경로는 SoftAP/30300 제어 경로가 아니라 홈 Wi-Fi의 기존 Backend TCP10086 session에서 `up:ota:<backend IP>`를 전달하는 방식입니다. Active official profile은 **canonical 1.0.111**, **1.0.110 rollback**, **LG U+ 1.0.66 rollback**입니다. 1.0.112/113/114는 history/legacy로 숨깁니다. 자동 업데이트는 하지 않으며 관리자 확인 후 한 대씩 queue에서 처리합니다.

### OTA 포트

- **1.0.111:** TCP **49217**을 먼저 시도하고 TCP connect가 실패할 때만 **80**으로 fallback합니다.
- **1.0.110 및 이전 호환:** OTA 클라이언트가 TCP **80**으로 고정되어 있으므로 최초 110→111 업데이트에는 TCP/80 listener가 필요합니다.
- Docker 내부 OTA HTTP 서버는 비특권 포트 `10080` 하나이며 host에서 `49217:10080`과 `80:10080`을 동시에 publish합니다.
- Backend Web UI의 `8080`(운영 배포에서는 별도 host port 사용)은 OTA 포트와 무관합니다.

따라서 TCP/80을 즉시 제거하면 기존 1.0.110 장치의 부트스트랩 OTA가 막힙니다. 장치가 1.0.111로 전환된 뒤에는 49217이 정상 우선 경로이며 80은 호환 fallback으로만 남습니다.

PC Provision/OTA 도구의 직접 SoftAP OTA 기능은 복구·독립 설치용으로 제공되며 1.0.110의 고정 TCP/80 요구를 충족할 수 있습니다.
