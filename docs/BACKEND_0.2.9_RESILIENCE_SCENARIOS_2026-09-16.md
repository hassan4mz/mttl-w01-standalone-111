# Backend 0.2.9 — 장애/재접속 시나리오 검증

## 기준 정책

- Home Assistant switch state는 장치에서 실제 확인한 observed state만 사용한다.
- device availability offline 전환 grace는 30초다.
- absolute outlet ON/OFF만 transport-loss episode 동안 defer 가능하다.
- transport-loss hard deadline은 최초 loss 시각부터 45초이며 어떤 추가 입력/재접속 실패도 연장하지 않는다.
- fresh session에서는 반드시 GETINFO를 먼저 읽고 observed != desired인 outlet만 한 번 제어한다.
- 45초가 지나면 pending desired는 폐기하며 이후 reconnect에서 replay하지 않는다.
- Child Lock, explicit refresh, OTA는 blackout pending/replay 대상이 아니다.
- Backend restart는 RAM-only pending/episode를 폐기하는 fail-safe 경계다.

## 시나리오 결과

| 시나리오 | Backend | HA | 제어 결과 |
|---|---|---|---|
| 장치 cold boot 후 15초 내 재접속 | 동일 45초 episode 유지 | 30초 grace 안이므로 offline publish 없음 | pending이 있으면 fresh GETINFO 후 필요한 outlet만 1회 실행 |
| 30초 부근 재접속 | 45초 episode 안 | offline timer와 reconnect가 경계에서 경합할 수 있어 순간 unavailable 가능 | reconnect/GETINFO 후 online 복구, pending은 아직 유효 |
| 60초 후 재접속 | 45초 episode 이미 만료 | 30초에 unavailable, reconnect 후 fresh state로 online | 오래된 pending은 절대 replay하지 않음 |
| 실제 멀티탭 전원 OFF 직후 제어 | 처음 45초는 Wi-Fi blackout과 구분 불가 | 30초까지 기존 availability grace | 45초 안의 absolute ON/OFF만 임시 보관; 장치가 45초 안에 돌아오면 reconcile |
| 실제 멀티탭 전원 OFF 45초 초과 | episode 종료 | unavailable | 새 제어는 offline error, 과거 pending 폐기 |
| AP/Wi-Fi 잠깐 장애 | transport loss 시작, stale stream fence | 약 16초 수준이면 unavailable flap 없음 | 같은 명령 반복은 coalesce, reconnect read-before-write |
| AP/Wi-Fi 장애 중 ACK timeout | 같은 stream mutation retry 금지 | observed state 유지 | desired만 fresh session으로 넘김 |
| 여러 번 reconnect했지만 GETINFO 실패 | 최초 deadline 유지 | 30초 초과 시 unavailable 가능 | mutation replay 없음; 45초 만료 시 pending 폐기 |
| ALL 중 일부 outlet만 실제 적용된 뒤 장애 | outlet별 absolute desired 유지 | observed state만 표시 | reconnect GETINFO 후 이미 맞는 outlet은 skip, 나머지만 1회 실행 |
| reconnect는 됐지만 reconcile 직전 45초 만료 | episode 만료 | 연결 상태는 fresh snapshot 기준 | pending 폐기, 오래된 command 실행 금지 |
| Child Lock 요청 중 offline | defer하지 않음 | Child Lock availability는 current-session sync 필요 | 실패 반환, 자동 replay 없음 |
| explicit refresh 중 offline | defer하지 않음 | 기존 observed 유지 | 실패 반환, 자동 retry 없음 |
| OTA 요청 시 장치 offline | queue 거부 | 변화 없음 | 자동 defer/replay 없음 |
| OTA command 결과가 모호함 | RESULT_UNKNOWN | 상태 확인 전 성공 취급 안 함 | 자동 재전송 금지, 후속 reconnect에서 version+GETINFO 검증만 허용 |
| Backend restart | Store startup에서 모든 device online=0/session=NULL, RAM pending 소멸 | bridge availability가 offline/online gate 역할 | restart 전에 pending이던 power command는 replay하지 않음 |
| 새 same-MAC session 뒤 old session 늦은 timeout | old session은 current 아님을 확인 | 영향 없음 | false transport-loss episode를 만들지 않음 (0.2.9 fix) |
| permanent delete 후 same MAC 재등록 | pending/episode 즉시 폐기 | retained discovery/state 제거 후 새 등록 | 이전 identity의 desired state를 상속하지 않음 (0.2.9 fix) |

### 30초와 45초가 다른 이유

30초는 HA 표현의 availability grace이고 45초는 backend의 명령 안전창이다. 따라서 30~45초 구간에는 HA가 unavailable일 수 있지만, 이미 backend에 들어온 absolute desired state는 45초까지 살아 있을 수 있다. Stock HA는 unavailable entity에 새 toggle을 보통 보내지 않으므로 실사용 충돌은 작다. 이 분리는 짧은 Wi-Fi reset은 숨기면서도 장기 offline 뒤 오래된 command가 살아나는 것을 막는다.

## Backend / MQTT 장애

HA discovery는 `availability_mode=all`로 다음 두 availability를 동시에 사용한다.

1. `mttl/<MAC>/availability` — device transport 상태
2. `mttl/bridge/availability` — backend/MQTT bridge 상태

따라서 backend 자체가 죽으면 device retained `online`이 남아 있더라도 bridge availability가 offline이 되어 entity 제어 가능 상태로 보이지 않아야 한다. Backend가 다시 시작되면 persisted device row를 모두 offline으로 초기화하고, 장치가 다시 연결되어 GETINFO snapshot을 갱신한 뒤 retained state를 다시 publish한다. Retained actuator command는 수신 시 무시하므로 backend/MQTT reconnect가 과거 제어를 재실행하지 않는다.

## 장기 테스트 계측

0.2.9부터 기존 bounded `events` 테이블에 다음 이벤트를 기록한다.

- `transport_loss_start`: 최초 reason, 45초 deadline
- `transport_loss_end`: 실제 recovery duration, start/end reason
- `transport_loss_expired`: 45초 초과 duration, 폐기한 outlet 목록

이 기록은 기존 30일 / 최대 10,000 rows retention 정책을 그대로 사용한다. 기록 실패는 제어 경로를 중단시키지 않는다.

### 권장 기간

- 최소: **72시간** — 6시간 11분 주기 기준 장치당 약 11회, 4대면 약 40회 이상의 자연 blackout 표본
- 권장: **7일** — 장치당 약 27회, 4대면 약 100회 수준 표본

### 합격 기준

1. 정상적인 periodic blackout은 모두 `transport_loss_end`로 끝나고 `transport_loss_expired`는 0건이어야 한다.
2. periodic blackout recovery duration은 대부분 30초 미만이어야 하며 HA device availability가 offline으로 flap하지 않아야 한다.
3. blackout 중 제어한 경우 같은 mutation이 동일 stream에 재전송되지 않아야 한다.
4. reconnect 후 observed==desired이면 mutation 0회, 다르면 필요한 outlet만 1회여야 한다.
5. 45초 이후 pending replay는 0건이어야 한다.
6. backend container unexpected restart는 0회, health failure는 0회여야 한다.
7. DB `integrity_check=ok`, WAL/DB/event row 수가 기존 retention 한계 안에 있어야 한다.
8. MQTT reconnect/backend restart 뒤 retained actuator command 실행은 0건이어야 한다.
9. 6h11 주기가 지속되더라도 사용자 체감 실패는 `transport_loss_expired`, failed reconcile, observed mismatch의 증가로 판단한다.

### 조회 예시

```sql
SELECT datetime(ts,'unixepoch','localtime') AS local_time,
       mac, kind, detail
FROM events
WHERE kind LIKE 'transport_loss_%'
ORDER BY ts;
```

7일간 MAC별 횟수:

```sql
SELECT mac, kind, COUNT(*) AS n
FROM events
WHERE kind LIKE 'transport_loss_%'
  AND ts >= strftime('%s','now','-7 days')
GROUP BY mac, kind
ORDER BY mac, kind;
```

`transport_loss_end.detail` JSON의 `duration_sec`를 집계해 평균/최대 recovery를 확인한다. `transport_loss_expired`가 한 건이라도 생기면 해당 시각의 backend log, AP log, ping/pcap을 함께 확인한다.

## 검증 결과

- resilience + control focused regression: 39/39 PASS
- resilience + OTA safety focused regression: 27/27 PASS
- full regression: **161 tests OK, 4 skipped** (배포 ZIP에 포함하지 않는 local 1.0.105 firmware fixture 의존 테스트)
- 테스트 과정에서 stale old-session false episode와 permanent-delete stale pending 두 edge case를 재현했고 0.2.9에서 수정 후 회귀 통과했다.


## Production backend stop/start 실기 검증

0.2.9 production 배포 후 MQTT subscriber를 붙인 상태에서 backend container를 의도적으로 stop → 약 3초 후 start했다. Broker에서 retained bridge availability가 다음 순서로 실제 관측됐다.

```text
mttl/bridge/availability online
mttl/bridge/availability offline
mttl/bridge/availability online
```

재시작 후 backend health는 `version=0.2.9`, `devices_online=3`, Docker health `healthy`였다. 당시 실제 online 장치 3대는 약 1~2초 내 TCP10086 재접속 후 GETINFO/Child Lock sync가 완료되었고 retained device availability가 다시 `online`으로 확인됐다. 기존 장기 offline 장치 1대의 retained availability는 `offline`을 유지했다. DB `PRAGMA integrity_check`는 `ok`였다.

Backend restart 전 RAM-only pending을 의도적으로 만들지는 않았지만, 회귀 테스트에서 restart 시 Store가 persisted online을 0으로 초기화하고 새 Manager의 pending/episode가 비어 있으며 old pending을 replay하지 않는 것을 검증했다. Retained actuator command 무시 정책도 기존 HA command safety regression으로 유지된다.
