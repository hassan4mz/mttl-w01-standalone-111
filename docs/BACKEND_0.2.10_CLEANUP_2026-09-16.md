# Backend 0.2.10 cleanup

Backend 0.2.9의 장애 대응 로직은 그대로 유지하면서 조사 완료 후 불필요해진 진단 기능을 제거했다.

## 제거

- Backend heap telemetry 전체: UI, `/api/heap`, 설정 API, RAM history, 5분 heap poll task
- 장치 `up:memory` 진단 경로: parser, queue, request helper
- heap 조사를 위해 붙였던 GETINFO 누적 RTT/timeout/recovery 통계와 admin diagnostics payload
- `heap.telemetry_enabled` SQLite setting
- runtime 폴더의 `.pre-*`/backup debris

## 유지

- Linux TCP_INFO timeout classification: 현재 Wi-Fi blackout 시 stale socket을 빠르게 abort할지 결정하는 실제 제어 로직이므로 유지
- transport-loss start/end/expired events: 장기 안정성 및 45초 deadline 검증에 직접 필요
- 전압/RSSI 측정: Home Assistant 센서와 Lovelace 카드가 사용하는 사용자 기능
- Backend Log UI/회전 로그: 운영 장애 확인 기능

정리 후 `heap_telemetry`, `up:memory`, `MEMORY_RE`, `memory_q`, `getinfo_stats`, `session_getinfo_*`는 production backend/static source에 존재하지 않는다.

## 검증

- 전체 회귀: **158 tests OK, 4 skipped** (배포에 포함하지 않는 로컬 1.0.105 fixture 의존)
- production health: Backend `0.2.10`, Docker health `healthy`
- 현재 online 3대 재접속 및 Child Lock sync 정상
- live UI에서 `heap`/`up:memory` 문자열 0건
- 배포 후 runtime log의 heap/MEMORY 요청 0건
- SQLite `heap.telemetry_enabled` 0건, `PRAGMA integrity_check=ok`
