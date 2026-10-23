# Alexa / Google Home 직접 연결 (허브 없이, 무료)

이 문서는 MTTL-W01 멀티탭을 **제조사 Cloud·유료 허브 없이** Alexa와 Google Home에
직접 등록하는 방법을 다룹니다. 근거는 현재 코드(`backend/backend.py` 0.2.11)입니다.

## 왜 "허브 없이" 가능한가

- 펌웨어 1.0.110/1.0.111은 제조사 Cloud를 전혀 사용하지 않고, 오직 로컬 Backend의
  TCP/10086(UP 프로토콜)에만 연결됩니다 (`docs/TECHNICAL_DESIGN.md`).
- 따라서 이미 Docker로 돌아가는 **Backend 자체가 허브 역할**을 합니다.
- Voice Assistant Bridge는 Backend에 추가된 경량 HTTP 엔드포인트로,
  **Home Assistant나 SmartThings Hub 없이** Alexa/Google Home 앱에서 기기를
  직접 발견·제어할 수 있게 합니다.
- 비용: **완전 무료** (외부 클라우드 의존 없음, 로컬 네트워크만 사용).

## 전제 조건

1. Backend가 Docker(또는 `backend/deploy.sh`)로 실행 중이어야 합니다.
   - Web UI: `http://서버IP:8080` (`.env`의 `WEB_PORT`로 변경 가능)
2. 장치가 Backend에 1대 이상 온라인으로 붙어 있어야 합니다 (`/api/health` 확인).
3. **Alexa**: Alexa App → Settings → Music & Services? ❌ 아닙니다.
   **Skills**에서 `MTTL Local` 커스텀 스킬을 "Self Test" 모드로 활성화합니다.
   - 커스텀 스킬은 무료이며, Lambda 등 AWS 리소스가 필요 없습니다.
   - Skill의 Account Linking은 **Disabled**로 둡니다.
4. **Google Home**: Google Home App → ＋ → Set up device → Works with My account?
   ❌ 그 경로에는 무료 로컬 옵션이 없습니다. 대신 **Local Home SDK(Device TR)**를
   사용합니다. TR(Local Home Actions)는 Google 프로젝트(Firebase)에 배포하며,
   Firebase의 무료 Spark 플랜으로 충분합니다. 기기 제어 자체는 LAN 안에서만
   일어나고 Google 서버를 거치지 않습니다.

## Bridge 활성화

### A. 환경 변수 (docker-compose / `.env`)

```env
VOICE_BRIDGE_ENABLED=1
VOICE_BRIDGE_TOKEN=<32바이트 이상 랜덤 문자열>   # openssl rand -hex 32
```

docker-compose.yaml의 `services.backend.environment`에 두 줄을 추가하고 재시작합니다.

### B. 또는 Web UI 없이 curl 로깅인 경우

동일한 값은 SQLite `settings` 테이블의 `voice.enabled`, `voice.token`에도 저장됩니다.
환경 변수가 있으면 항상 환경 변수가 우선합니다.

## Bridge API 명세

Base URL: `http://<서버IP>:8080/voice`

인증: **Bearer 토큰 헤더** (관리자 세션 쿠키와 별개이며, Alexa 스킬 / Google TR이 사용)

```
Authorization: Bearer <VOICE_BRIDGE_TOKEN>
```

| Method | Path | 설명 |
|---|---|---|
| GET  | `/voice/devices` | 디바이스 목록. 각 Outlet과 All을 개별 on/off 스위치 + power(W) 센서로 펼친다 |
| GET  | `/voice/devices/<id>` | 단일 디바이스 상태 (`id` = MAC 12자리 또는 `mttl_<mac>_switch_<n>`) |
| PUT  | `/voice/devices/<id>` | `{ "on": true/false }` — 내부적으로 기존 `/api/devices/<mac>/outlets/<0-4>/power`와 같은 `manager.power()` 경로를 호출한다 |

디바이스 ID 규칙 (HA discovery payload의 `unique_id`와 동일해서 이름이 겹치지 않음):

```
mttl_<mac>_switch_all        # 전체 ON/OFF (selector 0)
mttl_<mac>_switch_1..4       # Outlet 1~4
mttl_<mac>_power_total/_1..4 # 소비전력 센서 (읽기 전용)
```

## Alexa 등록 절차 (무료)

1. [developer.amazon.com](https://developer.amazon.com/alexa/console/ask) → Create Skill
   - Language: English(또는 Korean), Model: **Custom**, Other: Smart Home
2. Interaction Model → Devices tab → Add Device Type: **Plug** (×5: All+Outlet1~4당 1개)
   - 또는 스마트홈 스킬 템플릿("Smart Home Skill Template")을 적용하면 Discovery만 구현해도 됨
3. Endpoint: **HTTPS endpoint**에 아래 URL 입력 (LAN 사설 IP는 Alexa 클라우드는 못 감;
   단, Self-test 모드에서는 테스트 화면에서 직접 JSON을 붙여넣어 검증 가능)
   - 실제 가정 사용 시에는 **역방향 프록시 + Let's Encrypt 무료 인증서**로
     HTTPS 443을 터널링해야 합니다. 무료 방법 예시:
     - Cloudflare Tunnel(free tier) → `https://tunnel 도메인` → Backend 8080
     - 또는 Caddy 자동 HTTPS + 포트포워드(공인 IP 필요)
4. SSL 인증서에 위 도메인을 Skill Information → SSL Certificate에 등록
5. Test(Develop) 탭 → **Test enabled** → Alexa App에서 해당 Amazon 계정으로
   "Discover devices" → 기기 등장 → "Alexa, turn on Outlet 1"

Skill 코드는 `tools/alexa-smart-home-skill.js`(Node 18, 단일 파일) 제공:
- `AddDiscoveryRequest` → Backend `/voice/devices`를 호출해 번역 응답
- `TurnOn/TurnOff` → `/voice/devices/<id>` PUT
- `QueryPowerStateRequest` → 전력 센서 값 보고
- Environment variable: `MTTL_BASE_URL`, `MTTL_TOKEN`

## Google Home 등록 절차 (무료)

1. Google Home App → Profile → **Developer tools → Device TAM/TR project** 생성
   (Firebase 프로젝트 연동, Spark 무료 플랜)
2. TR(Local Home Actions) 프로젝트 폴더에 `tools/googlehome-tr/index.ts` 적용:
   - `identification` hint: mDNS `_http._tcp.local`에서 Backend 서버 검색
   - `onExecute`: Backend `/vibe/devices/<id>` PUT 호출 (LAN 내 직접 HTTP, 8080)
   - `onQuery` / `onSync`: `/vibe/devices` 매핑
3. `npm run deploy` (Firebase Hosting free quota로 충분)
4. Google Home App → 개발자 모드 ON → 기기가 자동 동기화됨
   - "Hey Google, turn on the coffee maker" (별명 지정)

TR은 로컬 네트워크에서만 동작하므로 Google 서버에 명령이 가지 않고,
Backend가 꺼져 있으면 기기는 "unavailable"로 표시됩니다.

## 보안 주의

- Bridge는 관리자 권한과 동일한 제어 능력을 갖습니다. **토큰 유출 = 전원 제어 노출.**
- 반드시 `.env`/Web에서 관리자 암호를 기본값 `admin`에서 변경한 뒤 활성화하세요.
  코드상 `AuthManager.verify()`가 기본 암호면 Bridge 활성화를 거부합니다.
- Bridge 토큰은 `store.setting('voice.token')`에 저장되며, Web UI에서 재발급 시 즉시 무효화.
- `/vibe/*` 요청은 기존 Web 로그 버퍼(category `control`)에 기록되어
  `/logs.html`에서 누가 언제 껐다/켰다 추적 가능합니다.

## 제한 사항 (솔직한 트레이드오프)

| 항목 | 상태 |
|---|---|
| 허브 하드웨어 불필요 | ✅ Backend PC/NAS가 허브 |
| 월 구독/클라우드 비용 | ✅ 없음 (Firebase/AWS/Let's Encrypt 무료 티어) |
| 외출 중 제어 | ⚠️ Cloudflare Tunnel 등으로 HTTPS 진입로가 있어야 함 |
| 음성 named scenes ("취침 준비") | ✅ HA 없이도 Alexa/Google 앱 자체 루틴으로 가능 |
| Child Lock 노출 | ❌ 안전상 Bridge에서는 미노출 (config entity) |
| 에너지 통계 음성 질문 | ✅ QueryPowerState로 실시간 W 응답 |

## 관련 문서

- `TECHNICAL_DESIGN.md` — UP 백엔드/HA 브리지 구조
- `OPERATIONS.md` — Docker 운영, `.env` 항목
- `backend/ha_bridge.py` — MQTT 경로(HA 쓰는 사람용 대안)
