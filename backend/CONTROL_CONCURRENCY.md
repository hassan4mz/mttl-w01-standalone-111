# MTTL-W01 TCP 제어 동시성 / 일관성 설계

## 전제

MTTL-W01의 TCP 10086 프로토콜은 줄 단위 request/response를 사용하지만 wire transaction ID가 없다. TCP는 바이트 순서와 전달을 보장할 뿐, 장치가 명령을 실행했는지와 응답을 backend가 수신했는지를 하나의 exactly-once transaction으로 만들어 주지는 않는다.

따라서 다음 상황은 원리상 구분할 수 없다.

- 장치가 `ON`을 실행했고 ACK만 유실됨
- 장치가 `ON`을 실행하지 못했고 ACK도 없음

이 상태에서 같은 TCP stream에 mutation을 자동 재전송하면 중복 실행과 늦은 ACK 오인 가능성이 생긴다. 또한 오래된 remote command가 나중의 물리 버튼 조작을 덮을 수 있다.

## 현재 불변조건

1. **장치별 single writer**
   - Web/API, Home Assistant, explicit refresh, query, periodic poll은 한 `Session.lock`을 통해 request transaction을 직렬화한다.
   - 서로 다른 장치는 독립적으로 병렬 처리한다.

2. **mutation은 절대 자동 재전송하지 않음**
   - `up:onoff:N:on|off`는 absolute desired-state 명령이다.
   - ACK timeout이면 `indeterminate`로 끝내고 해당 TCP session을 폐기한다.
   - 장치가 새 TCP session으로 reconnect하면 즉시 `GETINFO`로 실제 상태를 다시 동기화한다.

3. **ACK != 최종 물리 상태**
   - `up:onoff:*` 응답은 command-path ACK로만 큐에 넣는다.
   - DB/HA의 최종 상태 확정은 명령 뒤 `up:getinfo:all` snapshot으로 수행한다.
   - 따라서 정상 제어 transaction은 `MUTATION -> ACK -> GETINFO -> OBSERVED CONFIRM`이다.

4. **받은 snapshot이 불일치할 때만 read-only retry**
   - ACK 후 snapshot이 요청 상태와 다르면 150 ms 후 `GETINFO`만 한 번 더 요청한다.
   - 두 번째도 다르면 `observed_mismatch`이며 재제어하지 않는다.
   - GETINFO 자체가 timeout이면 같은 stream에서 재조회하지 않고 session을 폐기한다.

5. **timeout 난 uncorrelated stream은 재사용하지 않음**
   - GETINFO/verification timeout 후 늦게 도착한 응답이 다음 GETINFO를 만족시키는 것을 막기 위해 첫 timeout에 reconnect한다.
   - 이는 wire transaction ID가 없는 프로토콜에서 가장 보수적인 resynchronization boundary다.

6. **완전한 snapshot만 인정**
   - GETINFO는 physical outlet selector 1,2,3,4가 정확히 한 번씩 모두 파싱되어야 유효하다.
   - 부분/깨진 snapshot은 DB나 transaction 결과에 적용하지 않는다. 결국 request timeout -> reconnect로 수렴한다.

7. **오래 기다린 mutation은 실행하지 않음**
   - remote control이 transaction lock을 5초 안에 잡지 못하면 `busy`로 실패한다.
   - 이 경우 command는 wire에 전혀 전송되지 않는다.
   - 긴 ALL 제어나 다른 transaction 뒤에 오래된 명령이 남았다가 뒤늦게 물리 조작을 덮는 문제를 줄인다.

8. **periodic telemetry는 low priority + fixed cadence**
   - reconnect/MAC identify 직후에는 MAC phase를 기다리지 않고 `GETINFO`를 즉시 1회 수행한다.
   - 이후 GETINFO는 `phase + N×interval` absolute monotonic grid를 사용한다. 기본 interval은 15초, MAC별 phase는 0~2초다.
   - 최근 성공 GETINFO가 5초 미만이면 해당 periodic slot은 건너뛴다. control verification/manual refresh도 같은 성공 시각을 갱신한다.
   - 기존 5초 poll 설정에서는 periodic 자기 자신이 다음 slot을 억제하지 않도록 freshness skip을 사용하지 않는다.
   - periodic poll 시점에 transaction lock이 사용 중이면 기다리지 않고 그 회차를 drop한다.
   - event loop가 한 cadence 이상 늦게 깨어난 stale slot도 실행하지 않으며 missed poll을 queue/catch-up하지 않는다.
   - Voltage/RSSI 진단은 최초 GETINFO 성공 이후 시작하고 다음 정규 GETINFO slot 1초 이내에는 새 transaction을 시작하지 않는다.
   - **주기적(background) GETINFO만 5.0초 timeout**을 사용한다. connect sync/manual refresh/control verification은 기존 3.5초를 유지한다. 2026-09-13 운영 로그에서 정상 응답이 2.4~2.75초까지 지연되고, 1.0.110/1.0.111 장치가 같은 수초 구간에 함께 지연/timeout된 사례가 확인되어 Wi-Fi 순간 stall에 대한 여유만 확대했다.
   - GETINFO 성공 RTT가 1초 이상이면 slow log를 남기고, 장치별 runtime counter에 성공/timeout/1초 이상/2초 이상/마지막 RTT/최대 RTT를 누적한다. 이 카운터는 진단용 RAM 상태이며 Backend 재시작 시 초기화된다.

9. **same-MAC session fencing**
   - 같은 MAC의 새 session이 identify되면 old session을 close한다.
   - old reader가 이미 가지고 있던 buffered line도 `sessions[mac] is s` 검사를 통과하지 못하면 DB/HA에 적용하지 않는다.

10. **물리 버튼은 observed-state event**
    - `up:event:onoff:*`는 transaction ACK queue에 섞지 않는다.
    - 즉시 DB/HA observed state에 반영한다.
    - remote command와 동시에 일어나면 장치가 보내는 최종 event/snapshot 순서가 backend의 최종 observed state가 된다.

11. **HA는 non-optimistic state**
    - HA switch는 `state_topic`을 사용한다.
    - command publish만으로 상태를 성공 처리하지 않고 장치 event/snapshot이 상태를 결정한다.
    - actuator command topic의 retained message는 backend/MQTT 재연결 시 과거 명령이 재실행되지 않도록 무시한다.
    - HA birth/status topic은 retained 여부와 관계없이 정상 처리한다.

12. **ALL은 비원자적임을 명시**
    - selector 0은 물리 master relay transaction이 아니라 outlet 1..4 absolute command를 순서대로 실행한다.
    - 중간 실패 시 rollback/retry하지 않는다.
    - `acked_outlets`와 최종 snapshot의 `confirmed_outlets`를 구분한다.

## 제어 상태 머신

```text
QUEUED
  |  transaction slot <= 5 s
  +-- timeout --------------------------> BUSY (wire 전송 없음)
  v
MUTATION_SENT
  | ACK <= 3 s
  +-- timeout --------------------------> INDETERMINATE -> CLOSE TCP -> RECONNECT -> GETINFO
  v
ACKED
  | GETINFO <= 3.5 s
  +-- timeout --------------------------> INDETERMINATE -> CLOSE TCP -> RECONNECT -> GETINFO
  v
OBSERVED
  +-- desired와 일치 ------------------> CONFIRMED
  +-- 불일치 -> 150 ms -> GETINFO 1회 --> CONFIRMED 또는 OBSERVED_MISMATCH
```

## 동시 actor별 처리

| actor | ingress | serialization | 최종 상태 근거 |
|---|---|---|---|
| 물리 버튼 | `up:event:onoff` | inbound event, command lock과 독립 | event + 이후 snapshot |
| Web/API | `Manager.power(source=web)` | 장치별 transaction lock | post-command GETINFO |
| Home Assistant | MQTT -> `Manager.power(source=ha)` | Web과 동일 lock | event/post-command GETINFO |
| periodic poll | `up:getinfo:all` | 동일 lock, busy면 poll skip | complete snapshot |
| explicit refresh | `up:getinfo:all` | 동일 lock | complete snapshot |
| version query | `up:query:*` | 동일 lock, 별도 response queue | query response |

## 실패 결과 의미

- `confirmed`: ACK를 받고 fresh snapshot에서 requested state를 확인함.
- `observed_mismatch`: ACK는 받았지만 두 번의 fresh snapshot에서 desired state로 수렴하지 않음. 자동 재제어하지 않음.
- `indeterminate / ack_timeout`: 장치 실행 여부를 알 수 없음. mutation 재전송 금지, TCP reset 후 snapshot 재동기화.
- `indeterminate / verify_timeout`: ACK는 받았지만 최종 상태 확인 실패. TCP reset 후 snapshot 재동기화.
- `busy`: 오래된 queued mutation 방지. wire에 명령을 보내지 않음.

`acked_outlets`는 프로토콜 ACK를 받은 채널이고 `confirmed_outlets`는 snapshot으로 최종 확인된 채널이다.

## Short transport-loss desired-state reconciliation (0.2.8)

약 6시간 11분 주기의 RTL8195A Wi-Fi/netif blackout처럼 짧은 transport loss는 MAC별 episode로 처리한다. episode 시작 시각은 최초 transport loss에서 한 번만 고정되며 hard deadline은 45초다. 같은 episode 동안 들어오는 추가 명령은 deadline을 연장하지 않는다.

- HA `state_topic`은 항상 마지막으로 장치에서 확인된 observed state만 publish한다. pending desired state를 optimistic state로 publish하지 않는다.
- main HA availability는 기존 30초 grace를 유지하므로 정상적인 약 16초 blackout에서는 entity가 unavailable로 흔들리지 않는다.
- outlet 1..4 / All ON/OFF만 absolute desired state로 defer할 수 있다. 같은 채널의 같은 방향 반복은 coalesce하고, 반대 방향의 새 요청은 latest-wins로 desired state만 교체한다.
- reconnect 후 첫 작업은 GETINFO다. observed==desired면 mutation을 보내지 않고 pending을 제거한다. observed!=desired일 때만 fresh session에서 한 번 제어하고 ACK+GETINFO로 확인한다.
- ACK timeout/verification timeout처럼 실행 여부가 모호한 경우 같은 stream에 mutation을 재전송하지 않는다. stream을 폐기한 뒤 fresh session에서 observed state를 먼저 확인한다.
- episode가 45초를 넘으면 pending을 모두 폐기한다. 이후 장치가 살아나도 오래된 명령은 replay하지 않는다.
- 새 세션이 이미 same-MAC current session이 된 뒤 old session이 늦게 종료되는 경우 old finally는 새 transport-loss episode를 만들지 않는다.

## 현재 프로토콜의 남는 한계

현 firmware protocol 자체에 transaction ID / state revision이 없으므로 backend만으로 true exactly-once 제어를 만들 수는 없다. 현재 설계는 **at-most-once mutation attempt + read-after-write verification + reconnect-on-ambiguity + eventual state convergence**를 목표로 한다.

향후 firmware protocol v2를 만들 수 있다면 다음이 최선이다.

- request `command_id` / response 동일 `command_id`
- boot/session epoch
- monotonic state revision
- `desired`와 `observed`를 함께 포함한 command response
- 4채널 atomic ALL command 또는 batch transaction

이 기능들이 있으면 timeout 뒤에도 동일 command ID를 이용한 안전한 idempotent retry와 정확한 stale-response 제거가 가능하다.

## 2026-09-02 검증

오프라인 regression:

- 단일 제어 `ACK -> snapshot -> confirmed`
- mutation ACK timeout: 1회만 송신, retransmit 없음, session invalidate
- verification timeout: 같은 stream에서 재조회하지 않고 invalidate
- received mismatch: mutation은 재전송하지 않고 GETINFO만 1회 retry
- mismatch 후 convergence
- ALL 4채널 순차 command 후 1회 snapshot
- 동시 remote writer transaction serialization
- transaction slot busy 시 wire 송신 없음
- periodic GETINFO `unacked_at_timeout`은 stale transport 즉시 종료 후 reconnect/resync; `acked_after_retrans`만 동일 outstanding request의 제한된 grace 허용 (application retry 없음)
- HA retained actuator command 무시
- HA retained birth message 정상 처리
- HA command가 공통 `Manager.power(source=ha)` 경로 사용

총 12개 regression test PASS.

운영 실기:

- 온라인 2대에 동시 explicit refresh 12회 및 재배포 후 8회 추가: 모두 selector 1..4 complete snapshot, timeout/partial response 없음.
- 현재 ON + 0 W 채널에 동일상태 ON 제어: `queued -> start -> ACK -> GETINFO -> confirmed`, 약 0.24 s, 실제 상태 변화 없음.
- backend health 정상, TCP10086 정상, HA MQTT connected.

## ALL ON/OFF relay sequencing

Backend selector `0`은 wire selector가 아니다. 확인된 UP wire command가 `up:onoff:1..4`이므로 ALL은 1→4 absolute mutation을 순차 전송한다.

펌웨어 `onoff` parser는 각 요청마다 relay set/reset dispatcher를 실행하고 response를 만든 뒤 약 100 ms delay를 거쳐 다음 input을 parse한다. Relay driver 자체도 50 ms tick × 2의 약 100 ms latching pulse window를 사용한다. 따라서 다음 TCP frame이 이미 도착해도 네 relay drive가 동시에 겹치는 방식으로 처리되지 않는다.

물리 master button은 별도의 aggregate callback/BSP sequencer 경로를 사용한다. 둘의 내부 진입점은 다르지만 현재 backend 방식 역시 하드웨어 관점에서는 순차 구동이다.


## 0.2.9 resilience boundary hardening

추가 cold-boot/AP/backend/reconnect 시나리오 회귀에서 다음 두 race를 수정했다.

- current same-MAC session이 이미 교체된 뒤 old session에서 늦은 timeout/invalidate가 발생해도 새 transport-loss episode를 만들지 않는다.
- permanent delete는 RAM `pending_power`와 `transport_loss`까지 함께 폐기한다. 이후 같은 MAC이 새 identity로 등록되어도 이전 desired state를 상속하지 않는다.

Transport-loss 이벤트는 30일 bounded events table에 start/end/expired 형태로 기록한다. 계측 저장 실패는 제어 경로를 중단하지 않는 fail-open telemetry다. 장기 검증 기준은 `../docs/BACKEND_0.2.9_RESILIENCE_SCENARIOS_2026-09-16.md`를 따른다.
