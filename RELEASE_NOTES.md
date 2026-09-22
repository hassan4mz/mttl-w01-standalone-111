## 2026-09-17 — Distribution/tooling resync (Backend remains 0.2.11)

- Resynchronized the deployable package to the actual current components: Backend 0.2.11, canonical firmware 1.0.111, Android Setup 1.5.8 / versionCode 16, and the 2026-09-15 PC provisioning scripts.
- Rebuilt the PC provisioning ZIP from the current runtime files only; tests, caches, logs and all `.fwr` binaries remain excluded. PC provisioning regression: **15/15 PASS**.
- Replaced the stale bundled Android 1.5.0 APK with the verified 1.5.8 APK and updated Backend download metadata/UI labels accordingly.
- Added the 2026-09-17 FW 1.0.66 overnight boundary evidence and WLAN/LPS patch-feasibility analysis. The experimental 48-byte APP candidate remains analysis-only and is **not** included in distribution artifacts.
- Corrected stale current-version documentation, including the top-level README Backend version and the `VERSION.txt` release-bundle pointer. Historical release-note/audit entries remain unchanged.
- No firmware image or Backend control policy changed in this distribution refresh.
- Production `192.168.1.146` was rebuilt/recreated from this exact release tree after the distribution refresh. Runtime Backend/source/static hashes match the production source tree, Docker health is `healthy`, `/api/health` reports Backend 0.2.11 with three powered devices online, and the served PC Provision ZIP / Android APK now match the active distribution artifacts.

## 2026-09-17 — Backend 0.2.11

- Fixed the administrator Settings regression introduced by the 0.2.10 heap cleanup: `haIntervalOptions()` and `applyHA()` were accidentally removed while `loadHA()`/`saveHA()` still referenced them, producing `applyHA is not defined`.
- Confirmed this was a static Web UI cleanup regression, not a SQLite/HA settings migration failure; existing broker credentials/topics and device/channel data remain preserved.
- Added HA settings UI contract tests and a synthetic Backend 0.2.0 → current SQLite compatibility test.
- Audited 0.2.0 compatibility: all 18 baseline fixed API routes remain, all 46 baseline settings DOM IDs remain, no baseline `HAController` method is removed, and existing DB schema/data are preserved.
- Full regression after the audit: **161 tests OK, 4 optional local-firmware-fixture skips**.
- Runtime control, transient-loss reconciliation, HA availability, Child Lock and OTA behavior are unchanged from 0.2.10.
- Detailed audit: `docs/BACKEND_0.2.11_REGRESSION_AUDIT_2026-09-17.md`.

## 2026-09-16 — Backend 0.2.10

- Removed completed-investigation heap telemetry end-to-end: UI, API, DB setting, RAM history, device `up:memory` request path and background poll task.
- Removed accumulated GETINFO RTT/timeout/recovery diagnostic counters that were used only during blackout investigation. TCP_INFO classification itself remains because it now drives production reconnect behavior.
- Removed `.pre-*` runtime/source debris. Voltage/RSSI, transport-loss events and operational log UI remain because they are active user/operations features.
- Production release package is trimmed to runtime files plus current operational/design documents; unit tests and historical diagnostic change notes are no longer shipped in the install ZIP.
- Full post-cleanup regression: 158 tests OK, 4 local-firmware-fixture skips; production reconnect/health and heap-free UI/runtime checks passed.

## 2026-09-16 — Backend 0.2.9

- Expanded transient-loss validation to cold boot/reconnect at 15 s, 30 s and 60 s, true device power-off, temporary AP/Wi-Fi loss, repeated failed reconnects, partial ALL execution, backend restart, Child Lock/refresh/OTA non-replay, and Home Assistant availability boundaries.
- Fixed a stale-session race: a superseded old TCP session that times out after a healthy replacement is already current can no longer open a false 45-second transport-loss episode.
- Permanent device deletion now clears any RAM-only deferred power state and transport-loss episode, preventing a later same-MAC re-registration from inheriting stale intent.
- Transport-loss start/end/expiry are persisted in the bounded 30-day events table with recovery duration and reason, providing low-overhead long-run observability.
- 45-second hard deadline, observed-only HA switch state, 30-second device availability grace and read-before-write reconnect reconciliation remain unchanged.
- Detailed scenario matrix and long-run acceptance criteria: `docs/BACKEND_0.2.9_RESILIENCE_SCENARIOS_2026-09-16.md`.
- Production stop/start test captured MQTT bridge availability `online → offline → online`; three currently powered devices reconnected and resynchronized within seconds, while the pre-existing offline device remained offline. SQLite integrity remained `ok`.

## 2026-09-16 — Backend 0.2.8

- Short MTTL-W01 Wi-Fi/netif outages are handled as a per-MAC **transport-loss episode** with a fixed 45-second hard deadline measured from the first transport loss, not from each user command. Repeated commands never extend that deadline.
- Outlet ON/OFF commands received during the active episode are stored only as absolute desired state. Same-direction repeats are coalesced; a later opposite request replaces the earlier desired state for that outlet.
- Home Assistant switch state remains strictly **observed device state**; no optimistic/pending state is published. The existing 30-second HA availability grace hides the normal ~16-second periodic blackout without falsifying relay state.
- On reconnect the backend performs GETINFO first. If observed state already equals the pending desired state, nothing is retransmitted; otherwise a single normal ON/OFF transaction is issued on the fresh TCP session and verified by GETINFO.
- Ambiguous ACK/verification timeout never causes mutation replay on the same stream. The stale stream is fenced first, then only the absolute desired state may be reconciled on a fresh session.
- If the transport-loss episode reaches 45 seconds, all pending outlet commands are discarded and will never be replayed after a later reconnect. A superseded old TCP session cannot start a false new loss episode.
- Full regression: 140 tests OK (4 environment-dependent skips); focused transport/deferred-control tests 21/21 and final deferred-control tests 7/7.
- Detailed policy: `docs/BACKEND_0.2.8_CHANGE_NOTE_2026-09-16.md`.

## 2026-09-16 — Backend 0.2.7

- Synchronized pcap/ICMP evidence proved the recurring ~6h11 event is a temporary device IP/Wi-Fi/netif blackout, not only a stuck backend TCP stream.
- `unacked_at_timeout` now releases the stale transport immediately at the 5-second periodic GETINFO deadline, so the returning device can reconnect from the same `:30888` source port without colliding with the predecessor TCP state.
- `acked_after_retrans` retains the conservative 16-second same-request grace; no duplicate application GETINFO is sent.
- Home Assistant main availability keeps its 30-second grace, and Child Lock no longer force-flaps offline during a short transport handoff.
- Mutation ACK, Child Lock SET, OTA safety, polling choices, DB schema and firmware are unchanged.
- Detailed evidence/policy: `docs/BACKEND_0.2.7_CHANGE_NOTE_2026-09-16.md`.

## 2026-09-16 — Backend 0.2.6

- Main GETINFO polling choices are normalized to 10 / 15 / 30 / 60 / 120 / 300 seconds; default returns to 15 seconds after the 13-second diagnostic experiment disproved polling cadence as the primary periodic trigger.
- Web administrator sessions are now persisted in SQLite under `/data`; only SHA-256 token hashes are stored, never browser session tokens themselves.
- Login cookies and stored sessions are valid for one year, so Docker restart/recreation no longer forces reauthentication while the persistent `/data` volume is retained.
- Logout deletes the corresponding persisted session; administrator password change invalidates every previous session.

## 2026-09-16 — Backend 0.2.5

- Periodic GETINFO keeps the existing 5-second fast deadline, but Linux TCP_INFO now distinguishes an actual transport stall from a device/parser response stall.
- If the request is still unacknowledged, or was ACKed only after retransmission, the backend waits up to 16 additional seconds for the **same outstanding GETINFO**. It never sends an application-level retry.
- A reply recovered inside that grace keeps the TCP session/HA availability intact and records a transport-recovery counter.
- If the request was ACKed normally with no retransmission, behavior remains fail-fast: the session is invalidated for clean resynchronization.
- Mutation ACK/Child Lock SET/OTA safety behavior is unchanged.

## 2026-09-16 — Backend 0.2.4

- Added read-only Linux TCP_INFO logging on GETINFO timeout to separate transport delivery failure from device parser/response stalls.
- Polling and timeout behavior are unchanged from 0.2.3.

## 2026-09-15 — Backend 0.2.3

- Main GETINFO polling choices: 5/10/13/20/40/80 seconds.
- Default polling changed from 15 seconds to 13 seconds to avoid the device TCP keepalive 15-second boundary during the current transport-stall investigation.
- Legacy/invalid stored polling values fall back to 13 seconds.

- Android `1.5.8` keeps SoftAP scan/auto-connect unchanged, clarifies that UP Backend accepts any valid private/public IPv4, and blocks 105 download/build while connected to an MTTL SoftAP when no validated 1.0.105 cache exists. Valid cache requires pinned SHA256 plus FWR structure validation.
- Android `1.5.7` hardens failure handling: manual-current-SoftAP provisioning path with cmd103 identity proof, duplicate-operation locks, permission/location/Wi-Fi recovery, wrong-device guard, ACK-stage ambiguity reporting, manual-only Device Info, final OTA route revalidation, CPU/Wi-Fi wake locks, rotation survival, strict 192.168.1.x OTA route, and conservative post-transfer success wording.
- Android `1.5.6` moves OTA runtime progress from the bottom status log to a non-cancelable toast-style floating dialog, blocks Back/background touches within the activity, keeps the screen awake and orientation locked during OTA, shows byte/percent progress, and keeps the UI lock for a 10-second post-transfer flash/reboot stabilization window before releasing it.
- Android `1.5.5` removes OTA-screen SoftAP auto-connect in favor of explicit Android Wi-Fi Settings selection and current-SSID/Network verification. UP provisioning also stops after `up:ip` + `up:connect` ACKs; post-provision home-Wi-Fi/Backend attach polling and Backend API/auth verification were removed.
- Android `1.5.4` (versionCode 12) fixes API 28 false-positive SoftAP success by requiring exact target SSID/networkId/COMPLETED stability and target-Network revalidation, then proving MTTL identity with cmd103 instead of a bare TCP30300 connect. OTA/provisioning/PC cmd103 parsers now accept the canonical MTTL-W01 response semantics: request `103`, response header code `20301` (`0x4F4D`), body 70, result 0. API 28 fresh-profile handling also avoids reusing a stale profile after successful removal. Canonical firmware inspection confirms `TONLY_TAP_<last7 MAC hex>` and `LGU_<same last7>` construction, so password derivation remains unchanged.

- Android `1.5.3` (versionCode 11) fixes API 28 legacy SoftAP reconnect: an already-connected `TONLY_TAP_*` is reused without deleting its profile; failed `removeNetwork()` reuses the existing networkId instead of duplicate `addNetwork()`; switching from another Wi-Fi uses disconnect + 500 ms settle + profile re-read; `addNetwork(-1)` gets one configured-profile recovery check; previous Wi-Fi restoration also runs when target setup fails early. API 29+ behavior from 1.5.2 is unchanged.
## 2026-09-15 — Backend 0.2.2

- Added opt-in backend-only device heap telemetry using the firmware's read-only `up:memory` command.
- Default OFF; administrator setting toggles collection globally. Sampling is every 5 minutes, low-priority, serialized with device transactions, and skipped near the main GETINFO slot.
- Records current free heap, minimum-ever free heap, TCP session age and GETINFO success/timeout counters. Latest state is shown only in the authenticated backend; bounded 288-sample RAM history is available at `/api/heap` and each sample is written to backend logs.
- Heap telemetry is intentionally not exposed through guest API, Home Assistant Discovery/state, or MQTT. A heap telemetry timeout does not invalidate the device TCP session.

## 2026-09-15 — Backend 0.2.1

- Backend version promoted from `0.2.0` to `0.2.1` after the Home Assistant MQTT retained-state lifecycle fix.
- Firmware `1.0.110` remains fully supported for UP/Web/HA/power-energy-temperature/diagnostics/OTA; Child Lock remains capability-gated to canonical `1.0.111`.
- Detailed 2026-09-02 initial-backend → 0.2.1 delta: `docs/BACKEND_0.2.1_CHANGE_NOTE_2026-09-15.md`.
- Historical `dist` 0.2.0 archives and SHA256 manifests remain immutable; old release artifacts are not renamed.

## 2026-09-15 — Home Assistant MQTT retained lifecycle hardening

- GitHub `af950833/mttl_w01`의 HA MQTT 구조를 재검토해 stable client ID, reconnect cache reset, force snapshot sync, availability self-healing 원칙을 UP backend에 반영했습니다.
- Device availability `online`은 suppression cache를 거치지 않고 valid snapshot마다 retained publish하여 30초 이상 offline 후 재연결 시 broker의 stale `offline`이 남지 않도록 했습니다.
- 장치 숨김/retained 삭제 및 Voltage/RSSI 비활성화 시 해당 `_last_state` cache도 함께 무효화하며, 복원/재활성화는 동일 값이어도 force publish합니다.
- MQTT `publish()` 반환 rc를 검사해 broker/client가 publish를 거부한 경우 성공으로 cache하지 않습니다.
- MQTT reconnect 때 `_last_state`를 초기화한 뒤 retained discovery/state 전체를 강제 동기화하고 마지막에 bridge availability를 `online`으로 엽니다.
- HA lifecycle 및 전체 canonical backend regression 113 tests PASS. FWR은 변경하지 않았습니다.

## 2026-09-12 — Home Assistant outlet icons

## 2026-09-13 Web offline power stale-state fix

- Web UI에서 오프라인 장치의 상단 현재 소비전력과 Outlet별 순간전력을 마지막 수신값으로 표시하지 않고 `— W`로 표시합니다.
- 누적 소비전력(lifetime energy)은 DB 보존값을 계속 표시합니다. 온도/릴레이 등 기타 값은 기존처럼 마지막 수신 상태이며 안내문에서 stale 상태임을 명시합니다.
- DB energy continuity 및 Home Assistant availability semantics는 변경하지 않았습니다.

## 2026-09-13 Backend GETINFO Wi-Fi stall hardening

- 주기적 GETINFO timeout만 3.5초에서 5.0초로 완화했습니다.
- 초기 연결/수동 refresh/제어 verification은 3.5초를 유지합니다.
- 1초/2초 이상 GETINFO RTT 및 timeout runtime counters/logging을 추가했습니다.
- 1.0.110과 1.0.111 장치가 동일 시간대에 지연된 운영 관측에 따른 Backend-only 변경이며 FWR은 변경하지 않습니다.


- Fixed invalid `mdi:power-socket-kr` on the HA `All` switch; MDI has no `power-socket-kr`.
- HA `All` now uses `mdi:power-plug`.
- HA Outlet 1..4 now use `mdi:power-socket-de`, keeping the same icon for ON/OFF and relying on HA state color.
- The bundled/installed MTTL-UP Lovelace card channel icon is also aligned to `mdi:power-socket-de`.

## 2026-09-12 — FWR-free distribution

- Distribution ZIPs, source ZIPs, Backend static downloads and Docker images now contain **zero FWR binaries**.
- PC and Backend generate 1.0.66/1.0.110/1.0.111 only in the user runtime environment.
- Source can be either pinned 1.0.105 (`f78aee...efa`) or exact LG U+ 1.0.66 (`d780b5...290c`).
- 1.0.105 vendor download is attempted automatically. If unavailable, PC accepts a user-obtained 105/66 FWR placed beside the tool; Backend/Docker accepts it under `/data/firmware-source/`.
- The old bundled-FWR fallback path was removed. A missing/invalid source now fails closed with the manual source location in the error.
- Docker no longer copies `firmware_assets`; canonical 111 is deterministically generated from the accepted source and must still match the pinned canonical SHA.

## 2026-09-12 — Release/store consistency audit and withdrawn-profile cleanup

- Byte-audited canonical Provision source against the PC distribution ZIP, clean source ZIP, release directory, `dist/`, Backend static download, production host copy, and running Docker copy. All current artifacts match.
- Production Backend source/static files were hash-compared across workspace, production host, and running container.
- Found and removed the last operational legacy residue: hidden `official-112-lg` / `official-113` DB profile rows and `official_1.0.112.fwr` / `official_1.0.113.fwr` in `data/firmware`. Startup now purges withdrawn 112/113/114 operational profile metadata/files while preserving historical OTA jobs.
- Removed the unused 1.0.113 builder and 113 code blobs from production `backend/firmware_patch.py` / `firmware_assets`. Full failure evidence remains under `re/release_1_0_113_up_childlock/` and related `re/` analysis.
- Active operational profiles are exactly 1.0.111 / 1.0.110 / 1.0.66. Generated runtime FWRs live under `/data/firmware`; user/vendor source 1.0.105 or LG U+ 1.0.66 lives separately under `/data/firmware-source`.
- Backend regression suite is now **106/106 PASS**; PC Provision/OTA suite is **15/15 PASS**, including exact 1.0.66-as-source generation.
- Added `docs/RELEASE_CONSISTENCY_AUDIT_2026-09-12.md`.
- Source ZIP remains Docker-buildable but is now strictly FWR-free: it includes Backend code/static assets, the FWR-free PC tool and Android APK; all `.fwr`, secrets and runtime state are excluded.

## 2026-09-12 — PC SoftAP guided recovery hardening

- SoftAP auto-detection/connection failure no longer exits after one manual check and no longer asks the user to type the `TONLY_TAP_*` SSID.
- New recovery flow: connect/check Wi-Fi → Enter recheck → second Enter recheck → final y/n choice to make one real operation attempt or stop. Windows rechecks also retry scan/saved-profile connection when possible.
- OTA and Provision now carry the selected SoftAP hint into the actual operation and recover once if Wi-Fi roams away immediately before communication.
- INSTALL no longer immediately returns provisioning-pending when SoftAP service is late after OTA; it enters the same guided recovery flow first.
- Interactive Backend-IP/home-Wi-Fi validation allows correction instead of immediate exit. Explicit CLI/environment values remain fail-fast for automation.
- Safety preserved: firmware/header/checksum, TCP/80 and device protocol ACK checks remain strict; ambiguous mutation stages are never automatically retransmitted. Provisioning tests expanded from 10 to 14.

## 2026-09-12 — Release bundle/documentation resync

- `VERSION.txt`, workspace `README.md`, `AGENTS.md`, Backend README, Provision README and active firmware/operations/design documents are resynchronized to canonical 1.0.111 / Backend 0.2.0 / Android 1.5.0.
- Added current `docs/FIRMWARE_ENDPOINT_PROFILES.md`; old 1.0.112 endpoint-profile documents are explicitly historical.
- Current docs now record RTL8711AF + real-hardware JEDEC `C2 20 14`, the 66→110→111 functional lineage, Child Lock flash persistence, dual 49217/80 Backend OTA behavior, and the current log viewer scroll/text-copy UX.
- Stale active guidance for 1.0.112 RC4, Android 1.4.0, SQLite `synchronous=NORMAL`, and Backend default 1.0.110 was removed/corrected.
- Latest release bundle is regenerated with the FWR-free PC provisioning ZIP, Android 1.5.0 APK/source, current Backend/project source snapshot, checksums and a complete aggregate ZIP. No `.fwr` binary is included.

## 2026-09-12 — Firmware selector descriptions now show version deltas

- Firmware selector help is now written as a version lineage: 1.0.66 baseline, 1.0.110 changes versus 66, and 1.0.111 changes versus 110.
- 1.0.110 explicitly documents UP TCP10086 local backend support, immediate `up:event:onoff` reporting after physical relay changes, and orange status LED OFF during normal Wi-Fi+UP connection.
- 1.0.111 explicitly documents Child Lock/flash persistence and OTA 49217→80 fallback while retaining the 1.0.110 event/LED behavior. README uses the same wording.

## 2026-09-12 — Log viewer scroll/copy UX

- 2-second auto refresh no longer forces the page to the bottom while the administrator is reading older logs. Bottom-follow remains active only when already near the bottom; compact view preserves the visible log-row anchor across refreshes.
- Added in-page `텍스트` view and `현재 로그 복사`; the current filtered rows are copied as one-line-per-entry plain text without opening a separate window. JSON view remains available.

## 2026-09-12 — Backend firmware profile UI cleanup

- Official profile labels are now concise and function-first: `UP + Child Lock · 1.0.111`, `UP · 1.0.110`, `LG U+ Stock · 1.0.66`.
- The existing firmware hint line now changes immediately with the selected profile and shows its capabilities, Child Lock availability, OTA port behavior, validation/rollback role, and current-operation status. The redundant static explanatory banner was removed.

## 2026-09-12 — MTTL-W01 hardware identity / JEDEC verification

- Public MTTL-W01 control-board teardown and Ameba-1 documentation identify the physical controller as **Realtek RTL8711AF** (QFN48 / ARM Cortex-M3), not RTL8195A. `RTL8195A` names observed in firmware are shared Ameba-1 SDK symbols.
- A temporary read-only diagnostic based on canonical 1.0.111 returned the stock boot-cached JEDEC RDID **`C2 20 14`** on real hardware. `C2` is Macronix and the device density is 8 Mbit = 1 MiB.
- Macronix documentation shows MX25L8005 and MX25L8006E share the same three-byte RDID, so the exact die revision is deliberately recorded as **MX25L8005/MX25L8006E-compatible**, not guessed.
- Both candidate revisions are 100k erase/program-cycle class parts. After the read, the test device was restored to canonical 1.0.111 SHA `7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb` and Backend verified OTA SUCCESS + GETINFO. Temporary diagnostic Backend/FWR components were removed.
- Evidence: `docs/HARDWARE_IDENTIFICATION.md`.

## 2026-09-12 — Backend 0.2.0 / Android 1.5.0 / PC provisioning refresh

- Backend UI now identifies itself as `0.2.0` and adds an authenticated Log page with INFO/DEBUG/WARNING/ERROR, functional categories, compact/JSON views, a 5,000-row RAM ring and rotating DEBUG file logs.
- Child Lock is runtime/session-only; firmware flash is authoritative and reconnect sync repopulates HA/Web state. The compatibility DB column is retained but nulled/not written. Related settings backups are 2-second debounced; energy persistence retains the existing 10Wh/5min bounded flush policy.
- Web Child Lock master name is permanently `All`. HA exposes `All` + Outlet 1..4 as Controls, Child Lock 5 switches as Configuration, sensors separately and RSSI/backend data as Diagnostics.
- Android `1.5.0` fixes UP provisioning false failures using cmd103 exact MAC before `up:connect`; non-six-character SoftAP suffixes are only fallback hints. Unknown firmware SHA is allowed only after structural/model/internal-checksum validation and three warnings. Rooted phones may opt into legacy Device SW 0.1.54 TCP80 OTA via a narrow temporary iptables redirect.
- PC OTA-only now performs the same SoftAP auto-connect + manual fallback as provisioning/install. New source builder obtains the pinned 1.0.105 file (including the vendor `firmware1.0.115` directory alias) and deterministically reproduces official 66/110/111.

## 2026-09-12 — 1.0.111 hardware pass + 66/110/111 distribution + Android 1.4.0

- Canonical `1.0.111` was installed on hardware and normal OTA/boot operation was confirmed. Release status is now `HARDWARE_OTA_BOOT_VERIFIED` (not a claim that every protection/reset edge case was re-run).
- Backend official choices are now `1.0.111`, hardware-verified `1.0.110`, and deterministic LG U+ `1.0.66`. 66 is rebuilt from pinned Voltra 1.0.105 and accepted only when SHA256 is `d780b578af69d52f3a05191a8e7d91a20e05085a912722327481cd5663682c04`. Backend regression: **100/100 PASS**.
- PC distribution keeps **TCP/80 only by design**. 66/110 use 80 natively; 111 falls back from 49217 to 80. Field package contains exactly 111/110/66 plus provisioning scripts. Provision regression: **6/6 PASS**; all three FWR HTTP wire selftests PASS.
- Android `1.4.0` (versionCode 7) adds first-screen firmware/OTA access, pinned 1.0.105 download, deterministic 66/110/111 generation, generated-version selection, arbitrary structurally-valid FWR import, and top Toast feedback for SoftAP scan/selection.
- Android generator was cross-checked in pure Java against the Python reference and reproduces exact official SHA256 for 66/110/111. Direct OTA is intentionally gated on the current device reporting Device SW `0.1.55`; after downgrading to 66/110, restoration to 111 requires Backend/PC TCP80.

## 2026-09-12 — canonical 1.0.111 + SoftAP 0.1.55 + OTA TCP/49217

- Rebuilt canonical `1.0.111` from exact hardware-verified `1.0.110`; quarantined legacy experimental 111 hashes are explicitly rejected. Status: **STATIC VERIFIED / HARDWARE VERIFICATION PENDING**.
- Child Lock implementation uses only the proven-unreachable QMS body; known-live computed tables, RESET_AP, stock board init and HTTPS/443 path remain byte-identical to 1.0.110.
- Raw HTTP OTA now tries **TCP/49217 first** and falls back to **TCP/80 only if connect returns -1**. HTTP/download/flash executes once on the successful connection.
- SoftAP/LGAPMODE Device SW generation advanced from `0.1.54` to **`0.1.55`** consistently in all three firmware copies.
- FWR SHA256: `7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb`; APP SHA256: `6b2d650a35728eb7fbcb16f4c69025f36dc49fa8da21cc4988710315158d37f6`.
- Backend active official targets are canonical 1.0.111 + hardware-verified 1.0.110 rollback. Docker publishes host `49217:10080` and keeps `80:10080` for stock/1.0.110 bootstrap/fallback. Backend regression: **98/98 PASS**.
- PC field bundle defaults to canonical 1.0.111 and retains 1.0.110 rollback; provision regression: **5/5 PASS**, OTA selftest exact wire body PASS.
- Android `1.3.2` (versionCode 6) adds capability-gated SoftAP direct OTA. It serves the bundled FWR on TCP/49217 only when cmd103 reports Device SW `0.1.55`; 0.1.54/1.0.110 is blocked because that firmware is hard-coded to TCP/80. APK v2 signature verification PASS; lint 0 errors / 14 SetTextI18n warnings.

## 2026-09-12 — 1.0.113 withdrawn after hardware failure; backend forced to 1.0.110 recovery

- Exact 1.0.113 OTA transport succeeded after router DNAT was disabled: `/ota.bin` 327944 bytes completed. Device then failed to return STA Wi-Fi/UP.
- Static re-audit proved all original 1.0.113 zero-cave injection regions were live computed dispatch tables. 1.0.113 is withdrawn and must not be flashed.
- Production firmware target reset to hardware-verified 1.0.110. Backend hides/rejects 1.0.113 for target, prepare, download and direct OTA queue. Full dev backend regression: 96/96 PASS.
- 1.0.114 is static-only and not registered in production. It is rebased from exact 1.0.110, uses only proven-unreachable QMS body storage, and preserves the two corrupted dispatch tables byte-identically.

## 2026-09-12 — Backend / Provision / Android cleanup + SoftAP protocol correction

- Active official firmware choices reduced to **1.0.113 + 1.0.110 rollback**. 1.0.112/1.0.66 remain historical only.
- Backend diagnostic defaults corrected to Voltage OFF / RSSI OFF.
- Child Lock wire support simplified to exact firmware 1.0.113 and one connect-time sync.
- PC provisioning package now bundles pinned 1.0.113 + 1.0.110 FWRs and no firmware builder.
- Corrected an earlier protocol assumption: `up:getinfo`, `up:onoff`, `up:power_report`, `up:query`, and `up:childlock` are **TCP10086 backend commands, not SoftAP TCP30300 commands**.
- TCP30300 text handler is limited to `up:ip`, `up:connect`, `up:ota`, `up:reboot`.
- TCP30300 also implements stock-compatible `LGAPMODE0010`; cmd103 is Device Info. Android 1.3.1 uses only cmd103 on its read-only SoftAP information screen.
- 1.0.110 and 1.0.113 SoftAP text/LGAPMODE handlers are byte-identical; the 1.0.113 Child Lock patch does not extend SoftAP control.
- No UP production listener is currently running on this development host; prior notes claiming `/root/lgu_mttl-w01` deployment are not current.

## 2026-09-12 — 1.0.113 UP-only Child Lock static candidate

- Rebased Child Lock from exact hardware-verified 1.0.110 instead of continuing unified 1.0.112.
- Removed firmware-MQTT Child Lock, STATUS_REPORT extension, QMS-body cave reuse and custom Child Lock RAM.
- Corrected prior RAM analysis: `0x100546BC` is a live computed-offset structure entry, not free RAM.
- Runtime lock state now uses existing physical short-callback pointers (NULL=locked); long/reset state machine remains stock.
- Persistence remains bank1:`0x900`; SET uses write + strict readback verification. Read/write uncertainty fails open and returns `255`.
- Backend supports Child Lock wire access only for exact 1.0.113 and no longer auto-writes SET0 on `255`; 1.0.112 is legacy and receives no Child Lock wire commands.
- Active official profiles are 1.0.113 target + 1.0.110 rollback. Selecting a profile never starts OTA automatically.
- Static firmware verification PASS. Current cleaned Backend regression is **94/94 PASS**.
- FWR SHA256 `bf49274076263ac2651bb187612ae7fbfb1c4799c15b06c3b46b7faaf74ec8ee`.

# RC4 safety hardening — 1.0.112 / 2026-09-11

- Child Lock capability is exposed only for exact unified firmware 1.0.112; 66/68/110 and unknown future versions do not get HA lock entities.
- HA now separates firmware capability from current-session synchronization. Five lock entities exist for 112 but remain unavailable until a GET/SET is confirmed in the current connection; stale DB masks are never published as current state after reconnect.
- Connection synchronization retries at 8s, +10s, +30s and +60s, then leaves only the five Child Lock entities unavailable. No periodic Child Lock polling was added.
- Firmware RC4 hardening: runtime mask >31 fails open for physical short buttons; UP and MQTT GET reload the validated flash record before reporting.
- RC3 -> RC4 changes only 42 bytes inside the already-disabled QMS sender body plus two checksum bytes. External hooks, relay/protection/recovery and telemetry remain byte-identical.
- Production FWR SHA256: `3a5b7a94eabfb423848a9c624c093d1ac8e1a14762ce8bb8968a945d127ba554`.
- Status remains **STATIC VERIFIED / HARDWARE VERIFICATION PENDING**. Cold power-cycle persistence and full button/recovery regression are still required.

# RC3 hotfix — 1.0.112 / 2026-09-11

- RC2 was successfully installed on hardware and normal refresh/GETINFO worked, but explicit UP Child Lock still timed out with no reply.
- RC3 removes custom response formatting from the hook. The handler now only reads/sets the mask and returns it in `r3`; the original stock formatter/sender path at `0x10016046..0x10016050` is restored byte-for-byte.
- SET parsing accepts NUL/CR/LF terminators for `up:childlock:<0..31>`.
- FWR SHA256: `1c29de9ce9651db0cbfd7c0042dae7f8f8b61591a0f951836d68fe23cdecd844`.
- Backend now distinguishes a real device response timeout from a backend deadline and records Child Lock TX/RX at INFO for hardware verification.
- Status remains **STATIC VERIFIED / HARDWARE VERIFICATION PENDING** until RC3 Child Lock GET/SET and button behavior are tested.

---

# RC2 hotfix — 1.0.112 / 2026-09-11

- First RC1 hardware OTA booted successfully, but the UP Child Lock response reused a 16-byte diagnostic string slot without room for CRLF.
- RC2 changes only that response slot to `up:cl:%d\r\n`; request remains `up:childlock[:mask]`.
- Production RC2 FWR SHA256: `3d5997c8a37887512c61a47737f71e6cd0a1a87a730db017deeae89bbcf19e0b`.
- Backend OTA acceptance now treats either `ota_ok` or authenticated `/ota.bin` GET as acceptance, eliminating the observed false 5-second timeout.
- Automatic Child Lock probing on reconnect is disabled; exact RC2 install evidence gates manual probing.
- Status remains **STATIC VERIFIED / HARDWARE VERIFICATION PENDING**.

---

# Release notes — 1.0.112 RC1 / 2026-09-11

Status: **STATIC VERIFIED / HARDWARE VERIFICATION PENDING**.

- Unified 1.0.112 merges the hardware-verified 1.0.110 UP behavior with Direct-local MEF/MQTT support.
- Persistent physical-button Child Lock: 5-bit mask (Master + Outlet 1–4). UP request uses `up:childlock[:mask]`, response uses `up:cl:<mask>`; MQTT uses `CONFIGURATION` `434C00MM`, GET `434C00FF`.
- Persistence: bank1 offset `0x900`, record `43 4C MM (MM XOR FF)`. Invalid/uninitialized data boots unlocked.
- Factory reset clears the lock record; RESET_AP preserves it.
- HA exposes five Button Lock switches only for 1.0.112+ devices. Bit changes are GET → RMW → SET → confirmed response under one device transaction lock.
- Telemetry policy is `p/e/v/t`; current is intentionally omitted.
- `official-112` is fail-closed and the production vector is pinned to `192.168.1.146`.
- Production FWR SHA256: `3d5997c8a37887512c61a47737f71e6cd0a1a87a730db017deeae89bbcf19e0b`.
- PC provision package defaults to 1.0.112 RC; 1.0.110 generation remains available for rollback.
- Production UP backend on `192.168.1.146` was upgraded without automatic OTA. Existing devices remain on 1.0.110 pending one-device physical validation.
- Automated gates: UP backend **78/78 PASS**, unified MQTT backend **7/7 PASS**, Docker/Python/JS build and syntax gates PASS.

Hardware promotion gate: normal UP/MQTT control; masks 0/1/2/4/8/16/31 and combined masks; reboot persistence; UP↔MQTT cross-consistency; Factory clear; RESET_AP preserve; protection regression; OTA/recovery; p/e/v/t telemetry.

---

# Release notes — 1.0.110 / 2026-09-03

- Firmware version은 `1.0.110`으로 유지한다.
- 기존 검증된 UP 기능 유지: QMS HTTPS sender 차단, relay event push, TCP10086 UP 운용, 순정 protection/meter 로직 보존.
- GPIO35 주황 연결 상태 LED 처리 최종 확정.
  - dispatcher index 3 패턴: `0x10AFF`=0.5초 토글, `0x11EFF`=1.5초 토글, `0x13CFF`=3초 토글, low byte `FF`=무한 반복.
  - 순정 네트워크/재시도/LG oneM2M task에서 `0x11EFF`를 설정하는 실제 LED 호출 3개를 모두 순정 OFF로 치환.
  - `0x10AFF` 빠른 점멸과 `0x13CFF` 3초 점멸은 유지.
  - 정상 Wi-Fi + UP TCP10086 연결 및 backend 제어 상태에서 1.5초 재점멸이 사라지고 LED가 꺼지는 것을 실기 검증.
- 관련 펌웨어 동작과 적용 결과를 확인했다.
- `install` 안전성 개선: 생성된 `comMTTL-W01_1.0.110_UP.fwr`를 자동 선택하며, `MTTL-W01_V1.0.105.fwr`는 patch source로만 취급한다. 소스 105가 있어도 직접 OTA하지 않고 110을 생성한 뒤 설치한다.
- 저수준 `ota --fwr <file>`는 명시적으로 지정한 FWR wire bytes를 그대로 전송하는 기존 동작 유지.
- vendor FWR는 배포 ZIP에 포함하지 않는다. 공개 Voltra 1.0.105를 사용자 PC에서 받아 `105 -> LG U+ 66 baseline -> UP 1.0.110`으로 로컬 생성한다.
- 정식 UP 1.0.110 FWR SHA256: `318f44567cd90613a3a485a66bb398c80bc60c891699772d438c9cfd6cb33059`
- decoded app SHA256: `a72d17347be654fe3f5b655fc8a6e5caec6ce0d37ef21805a77130672119c654`
- OTA 후 SoftAP/30300이 자동 복귀하지 않으면 전원 버튼으로 SoftAP 재진입 후 `PROVISION_WINDOWS.bat`만 실행할 수 있다.


## Runtime / DB / Home Assistant 전송 개선

- GETINFO 기본 15초 polling을 `phase + N×interval` absolute monotonic grid로 운용해 요청 처리시간/지연이 다음 주기를 밀지 않도록 함.
- 장치별 MAC 기반 0~2초 고정 phase로 동시 요청을 분산하되, reconnect/MAC identify 직후 첫 GETINFO는 phase를 기다리지 않고 즉시 수행.
- 최근 성공 GETINFO가 5초 미만이면 해당 periodic slot을 skip. control verification/manual refresh도 같은 freshness에 포함. 단, 기존 5초 polling 설정은 자기 자신을 격회 skip하지 않도록 freshness suppression을 적용하지 않음.
- periodic slot에서 device transaction이 진행 중이면 기다리지 않고 해당 회차를 drop하며, event-loop가 한 cadence 이상 늦은 stale slot도 catch-up하지 않음.
- Voltage/RSSI 진단 polling은 첫 GETINFO 동기화 이후 시작하며 다음 정규 GETINFO slot 1초 이내에는 새 진단 transaction을 시작하지 않음.
- ON/OFF, 순간전력, 온도, 최신 통신 상태는 RAM에서 유지하고 polling마다 SQLite에 다시 저장하지 않음.
- 누적에너지는 4개 Outlet 미저장 변화량 합계 10Wh, dirty 5분, counter reset, disconnect 조건에서만 저장. 변화가 없으면 주기 DB write 없음.
- Home Assistant Power 보고: 기본 0.1W / 30초. 변화량 0.1/0.2/0.3/0.5/1W 및 15초~30분 보고 주기 선택 가능.
- Home Assistant Energy 보고: 기본 1Wh / 1분. 변화량 1/2/5/10/20Wh 및 15초~30분 보고 주기 선택 가능.
- ON/OFF는 변화 즉시, Voltage는 0.1V 변화, RSSI는 10dB 변화 또는 최대 5분 간격으로 갱신.
- 기존 DB/설정과 호환되는 상태에서 동작을 확인했다.

## Web 인증 / 설정 UI / 선택 진단 polling

- Web UI 관리 기능 인증 추가. 별도 사용자명 없이 최초 관리자 암호는 `admin`; 우상단 설정에서 변경 가능.
- 관리자 암호는 PBKDF2-SHA256 hash로 SQLite settings에 저장하고 평문은 저장하지 않음. 로그인 세션은 HttpOnly + SameSite=Strict cookie 사용.
- read-only `/api/devices`, `/api/health`, `/api/auth/status`는 인증 없이 유지하고 제어/설정/HA/백업/상세 관리 API는 관리자 로그인 필요.
- 기존 화면의 Home Assistant MQTT 설정을 우상단 **설정** dialog로 이동. 일반 GETINFO polling과 관리자 암호 변경도 같은 설정 화면에서 관리.
- 각 멀티탭별 Voltage / Wi-Fi RSSI 측정 활성화 추가. 둘을 개별 ON/OFF 가능.
- Voltage/RSSI polling은 GETINFO와 분리하고 기본 60초, 30/60/120/300/600/1800초 선택 가능.
- Voltage는 `up:power_report:1:vol`, RSSI는 `up:query:wifirssi` read-only request 사용. device transaction lock으로 기존 control/GETINFO와 직렬화.
- read timeout 시 late response 혼입을 막기 위해 해당 UP session을 invalidate/reconnect.
- 최신 Voltage/RSSI sample은 RAM에만 유지하고 DB에는 장치별 enabled/interval 설정만 저장하여 periodic DB write 증가를 피함.
- 활성화된 Voltage/RSSI만 HA Discovery에 추가; 비활성화 시 retained diagnostic state 정리.
- Web UX: device command pending 중 버튼 잠금, confirmed/error 문구, offline 안내, backend 연결 실패 banner, 중복 background load 방지.
- 권한 강화: `.env` 0600, `/data`/backup dirs 0700, runtime/backup files 0600, `umask 077`.
- Docker hardening: `cap_drop: ALL`, `no-new-privileges:true`.

## Backend TCP concurrency hardening

- 장치별 Web/HA/query/refresh/poll request를 single transaction lock으로 유지.
- `up:onoff` ACK를 최종 상태로 쓰지 않고, 모든 제어 후 fresh `up:getinfo:all` snapshot으로 observed state를 확인.
- snapshot mismatch는 150 ms 후 GETINFO만 1회 retry; mutation은 재전송하지 않음.
- ACK 또는 GETINFO timeout 시 wire transaction ID가 없는 stream을 재사용하지 않고 TCP session을 폐기하여 reconnect/resync.
- periodic GETINFO timeout 정책을 2회 누적에서 첫 transaction timeout 즉시 reconnect로 강화.
- remote mutation이 transaction slot에서 5초 이상 대기하면 `busy`로 중단하며 wire 전송하지 않음.
- periodic poll은 transaction active 시 해당 회차 skip.
- same-MAC 새 session 이후 old buffered frame이 DB/HA를 덮지 못하도록 session fencing 추가.
- GETINFO는 physical selector 1..4가 모두 있는 complete snapshot만 인정.
- `power_report`는 Voltage 선택 polling을 위해 bounded queue(max 16)로 다시 사용하며 device transaction lock으로 직렬화.
- MQTT actuator retained command replay 차단; HA birth/status retained 메시지는 유지.
- Web JSON `on`은 boolean만 허용. ALL 제어를 포함해 HTTP control deadline을 30초로 조정하고 `indeterminate`는 HTTP 504로 반환.
- control log에 backend-local transaction ID와 `source=web|ha` 추가; 물리 relay event도 INFO 로그에 기록.
- `acked_outlets`와 snapshot 기반 `confirmed_outlets` 의미를 분리.
- fake device concurrency/timeout/HA safety regression 12개 PASS.
- 운영 장치 2대 동시 refresh 검증 및 동일상태 Web 제어 `ACK -> GETINFO -> confirmed` 실기 PASS.

## Backend lifecycle / long-term operation hardening

- 장치 사용자 별명(`alias`) 추가. Web UI와 HA 기본 device name에 반영하되 MAC 기반 identity/unique_id는 변경하지 않는다.
- Web UI에 **장치 제거 / 제거된 장치 복원** 추가.
  - 일반 제거는 destructive delete가 아닌 soft-remove다.
  - alias, channel state, `energy_lifetime_wh`, highwater, energy epoch를 보존한다.
  - HA MQTT Discovery는 empty retained payload로 제거하고 device availability retained payload도 clear한다.
  - 복원 시 동일 MAC/unique_id로 Discovery를 다시 발행하고 backend 누적에너지를 이어 사용한다.
- removed 상태의 장치는 Web/HA 제어 대상에서 제외한다. TCP가 계속 연결돼 있다면 backend의 native energy counter 추적은 유지해 복원 시 continuity를 최대한 보존한다.
- 장치 TCP disconnect와 HA 표시 availability를 분리.
  - backend DB online/control 가능 여부는 TCP 종료 즉시 offline.
  - HA per-device offline은 30초 debounce 후 발행.
  - 30초 내 reconnect하면 unavailable을 발행하지 않고 마지막 known state를 유지한다.
  - 30초 이상 offline이면 HA에서 unavailable, reconnect 후 valid snapshot으로 online 복구.
  - backend/MQTT bridge 자체 장애는 별도 bridge availability 때문에 즉시 unavailable 가능.
- 운영 실기에서 backend 재시작 후 온라인 2대는 약 5초 내 reconnect하여 HA offline debounce가 취소되고, 계속 offline인 1대만 정확히 30초 후 unavailable 발행되는 것을 확인.
- 주기 GETINFO마다 MQTT Discovery 전체를 재발행하던 동작 제거. Discovery는 identify, HA sync/reconnect, alias 변경, restore 시에만 발행하고 polling에서는 state만 갱신한다.
- SQLite 장기운영 보강:
  - WAL + `synchronous=NORMAL`
  - `busy_timeout=5000`
  - `wal_autocheckpoint=256 pages`
  - `journal_size_limit=1 MiB`
  - 시작 시 WAL checkpoint/truncate
  - 6시간 주기 maintenance / `PRAGMA optimize`
  - connect/disconnect event 30일 및 최대 10,000행 보존
- 운영 DB 사본 migration 검증: 기존 3 devices / 12 channels, 누적에너지와 HA settings가 변경 없이 보존됨.
- 실제 운영 WAL은 기존 약 4 MiB에서 재배포 후 약 0.1~0.2 MiB 수준으로 감소 확인.
- session response queue와 pre-identification event buffer를 각각 최대 16개로 제한해 malformed/unsolicited traffic에 의한 무한 메모리 증가 방지.
- Docker `json-file` log rotation: 10 MiB × 3 files.
- Docker resource guard: memory 256 MiB, pids 128. 검증 시 정상 메모리 사용은 약 23 MiB 수준.
- `GET /api/storage` 저장소 진단 API 추가.
- lifecycle/storage/HA availability 및 DB recovery fault-injection까지 포함하여 전체 29 tests PASS.
- 사용자 README는 설치/HA/장치관리/복구 중심으로 간소화하고 다음 상세 문서를 분리:
  - `docs/OPERATIONS.md`
  - `docs/TECHNICAL_DESIGN.md`
  - `docs/FIRMWARE_AND_PROVISIONING.md`
  - `backend/CONTROL_CONCURRENCY.md`


## Verified DB backup / recovery hardening

- SQLite Online Backup API 기반 자동 snapshot 추가. 실행 중 `mttl_up.db` 파일을 raw copy하지 않는다.
- 각 snapshot은 `PRAGMA integrity_check=ok`와 SHA256을 통과해야 rotation에 진입한다.
- backup bundle은 `mttl_up.db`, `ha_secret.key`, `config.json`(존재 시), `manifest.json`으로 구성한다.
- 보존 정책: 최근 snapshot 12 + 시간별 24 + 일별 14 + 주별 8 + 월별 6.
- Backend 정상 시작 시 즉시 snapshot, 이후 1시간 주기. alias/remove/restore/HA 설정/poll 설정 변경 시 즉시 snapshot도 생성한다.
- 시작 전에 live DB `integrity_check` 수행. 손상 시 newest valid backup부터 검증해 자동 복구한다.
- 손상 DB/WAL/SHM은 `/data/backups/corrupt/`에 격리하고 최대 10세대 보존한다. 정상 backup이 없으면 빈 DB를 조용히 만들지 않고 fail-closed한다.
- 복구 자체가 전원 장애로 중단돼도 `.restore_pending.json` journal + staged bundle로 다음 시작 시 idempotent resume한다. DB는 sidecar state 뒤 마지막에 교체하고 완료 검증 후 journal을 제거한다.
- newest backup 자체가 손상된 fault-injection에서 이전 정상 generation으로 fallback하는 테스트 PASS.
- live DB 손상, restore 직전 재다운, stage 유실, DB 설치 후 journal 삭제 전 재다운을 모사한 fault-injection PASS.
- 운영 backend에서 startup/manual verified backup 생성, API 상태, rotation directory, manifest/SHA/integrity 검증 확인.
- 백업 DB 검증은 immutable read-only로 수행해 backup directory에 불필요한 WAL/SHM을 만들지 않는다.
- 운영용 `backend/backup_restore.sh` 추가: `status`, `backup`, `list`, `restore`.
- `GET /api/backup`, `POST /api/backup`, `GET /api/storage`로 상태/수동백업 확인 가능.
- 현재 DB가 수십 KiB 수준이므로 전체 rotation 용량도 수 MiB 규모다. 물리 디스크 자체 고장 대비는 `/data` 또는 backup directory의 외부/NAS 백업을 별도로 권장한다.

## Guest status / password-only administrator refinement

- 관리자 사용자명을 제거하고 **암호만 입력하는 관리자 로그인**으로 단순화.
- 최초 관리자 암호는 `admin`; 기존 PBKDF2 password hash와 변경된 암호는 그대로 호환.
- 로그인하지 않아도 `/api/devices` 및 Web UI에서 read-only 상태를 조회 가능.
  - online/offline, relay state, power, lifetime energy, temperature, last seen
  - 해당 장치에서 활성화된 경우 Voltage/RSSI 최신값
- guest `/api/devices`에서는 peer IP, session id, bootinfo, 내부 protection/config/raw counter 등 관리/프로토콜 세부정보를 제거.
- 제어, manual refresh, alias/remove/restore, diagnostic enable/interval, GETINFO poll 설정, HA MQTT 설정/test/sync, backup/storage/detail API는 관리자 로그인 필요.
- guest 화면에서는 제어/설정 버튼을 렌더링하지 않고 우상단 `관리자 로그인`만 표시.
- Voltage/RSSI는 신규/미설정 장치에서 둘 다 기본 OFF이며 요청 자체를 보내지 않음. 활성화한 센서만 HA Discovery 및 state topic으로 publish.

- Diagnostic 설정 변경을 device asyncio loop로 전달하고 per-session wake event로 첫 측정을 즉시 시작하도록 보강.
- 실기/MQTT 검증: 1.0.110 장치에서 `219.716 V`, `-55 dBm` 수신. HA broker에서 Voltage/RSSI Discovery와 retained state publish 확인 후 둘 다 OFF/60초로 복귀.
- 당시 unit/fault regression 35 tests PASS.


## UI/장치 lifecycle UX 정리

- 관리자 로그인 성공 후 설정 dialog로 강제 이동하지 않고 홈 화면을 그대로 유지. 기본 암호는 상단 경고만 표시.
- `관리자 / 로그인`, `관리자 / 로그아웃` 두 줄 버튼으로 변경.
- iOS Safari/WebView의 input focus 자동 확대를 피하도록 input/select 16px 이상 적용하고 설정 dialog 자동 focus 제거.
- 5초 상태 갱신 시 장치 카드의 `전압 / RSSI 측정` 펼침 상태, 체크박스/주기 선택, 숨긴 장치 펼침 상태와 스크롤 위치를 보존. 저장 후에만 서버값으로 명시적 재동기화.
- 화면 테마 기본값을 OS `prefers-color-scheme` 기반 **시스템**으로 변경. `시스템 / 라이트 / 다크` 사용자 선택은 `mttl_theme` cookie에 1년 저장.
- `장치 제거/복원` 용어를 `장치 숨기기/다시 표시`로 변경. soft-hide는 재연결/재프로비저닝만으로 자동 해제하지 않음.
- 숨긴 장치에 관리자 전용 `기록 완전 삭제` 추가. energy/history/alias/diagnostic settings/HA retained discovery 및 state를 제거하며, 다음 연결은 새 장치 등록으로 처리.
- UI/lifecycle 후속 회귀 포함 최종 **37 tests PASS**.

## Official release promotion

- 현재 `1.0.110` 펌웨어 + Standalone backend/UI/provisioning 구성을 정식 버전으로 확정.
- 최종 FWR SHA256: `318f44567cd90613a3a485a66bb398c80bc60c891699772d438c9cfd6cb33059`.
- LED 1.5초 legacy blink 제거 포함 실제 장치 검증 완료.
- 중간 패처/Toolkit/work copy/구 build 산출물은 정리하고 canonical project + dist + 최종 RE evidence만 유지.
## Provision-only SoftAP 자동 검색 개선

- `PROVISION_WINDOWS.bat` / `mttl_up_setup.py provision`도 install과 동일한 SoftAP 자동 연결 경로 사용.
- 현재 `TONLY_TAP_*` 연결은 그대로 재사용.
- 한 개 발견 시 자동 선택, 여러 개 발견 시 신호 세기와 SSID 목록을 표시하고 번호로 선택.
- 자동 연결 실패 시 수동 SSID 입력 fallback 유지.
- OTA 후 재부팅 때문에 SoftAP가 끊겨 별도로 Provision만 다시 실행하는 경우에도 적용.


## Backend-managed firmware OTA

- 관리자 설정에 목표 펌웨어 profile 추가: `UP 1.0.110`(기본), `LG U+ 1.0.66`, 검증된 사용자 지정 FWR.
- 공식 1.0.110/1.0.66은 published Voltra 1.0.105를 pinned SHA256으로 확인한 뒤 로컬 생성한다. 배포 ZIP에는 FWR을 포함하지 않는다.
- 1.0.66 생성 FWR SHA256: `d780b578af69d52f3a05191a8e7d91a20e05085a912722327481cd5663682c04`.
- 1.0.110 생성 FWR SHA256: `318f44567cd90613a3a485a66bb398c80bc60c891699772d438c9cfd6cb33059`.
- Dashboard는 현재 FW와 목표 FW가 다를 때 관리자에게 업데이트/다운그레이드 버튼을 표시한다. 자동 OTA는 없다.
- 모든 OTA는 global single-worker queue에서 한 대씩 순차 실행한다.
- 장치 TCP10086의 `up:ota:<backend IPv4>` / `up:ota:ota_ok` 경로를 사용한다.
- Device OTA worker가 고정 TCP/80 `GET /ota.bin`을 사용하므로 host `80` → container `10080`으로 매핑한다. container는 `cap_drop: ALL`을 유지한다.
- `/ota.bin`은 평소 404이며 active OTA job 중에만 현재 대상 장치의 기존 TCP10086 peer IP에 한해 제공한다.
- 성공 조건은 command ACK가 아니라 전체 FWR 전송 + 같은 MAC 재접속 + 목표 firmware version + fresh GETINFO 확인이다.
- ACK timeout/전송 모호성에서 mutation을 자동 재시도하지 않는다. `RESULT_UNKNOWN`은 늦은 재접속 후 목표 버전과 GETINFO를 모두 확인하면 `DELAYED_SUCCESS`가 된다.
- OTA queue/progress/history를 상단 `업데이트 N`, persistent progress toast, 작업 dialog에 표시한다.
- 사용자 지정 FWR은 size, XOR marker/decode, header/base, model, checksum, version, SHA256을 검증한 뒤에만 profile로 등록한다.
- firmware lifecycle/재시작/전송 모호성 회귀테스트 포함 현재 전체 **48 tests PASS**.
- Backend OTA host TCP80 publish는 기본 활성화다. 이미 TCP80을 사용하는 설치에서는 Compose의 `${OTA_PORT:-80}:10080` 매핑만 제거할 수 있으며 이 경우 Backend OTA만 비활성화되고 나머지 기능 및 PC 직접 OTA는 유지된다.
- Backend 재시작 시 persisted OTA 상태를 정합화한다: 명령 전 `QUEUED/PREPARING`은 `CANCELLED_RESTART`, 명령 전송 이후 미완료 상태는 `RESULT_UNKNOWN`으로 종료하고 자동 재전송하지 않는다.
- `up:ota` socket send 자체가 예외로 끝나도 일부 bytes 전송 여부를 알 수 없으므로 `FAILED_INTERNAL`이 아니라 `RESULT_UNKNOWN`으로 처리하고 session을 폐기한다.
- OTA reconnect wait event와 worker shutdown task를 항상 정리해 timeout/restart 후 RAM 상태가 남지 않게 했다.
- 공식 firmware 준비를 `prepare_source()`와 실제 build로 분리해 1.0.66 준비 시 불필요한 1.0.110 생성/폐기를 제거했다. Backend/PC patcher는 byte-identical 상태를 유지한다.
- guest `/api/devices`는 관리자용 firmware decoration을 거치지 않아 5초 상태 조회 때 불필요한 FWR SHA/readiness 검증을 수행하지 않는다.
- dead code/import와 중복 firmware host API, 이전 iOS local-OTA 실험 소스/생성물, 구 dist archive를 제거하고 과거 audit 문서는 RE historical 영역으로 이동했다.
- Verified backup format 2는 `/data/firmware/*.fwr`의 생성된 runtime/custom FWR을 DB/profile metadata와 같은 generation으로 SHA 검증 백업·복원한다. `/data/firmware-source/`의 사용자/vendor source는 별도 입력 자산으로 취급하며 snapshot에는 포함하지 않는다. 기존 format-1 backup restore 호환은 유지한다.
- `firmware_profiles`는 `kind=official|custom`을 유일한 분류 기준으로 사용하고 중복 `official` INTEGER 컬럼을 제거했다. 공식 profile filename도 고정 저장한다.
- Web mutation API는 DB 변경 후 verified backup을 background worker로 넘겨 숨기기/설정 저장 UI 응답을 즉시 반환한다. 수동 백업만 동기 완료를 기다린다.
- 장치 숨기기/다시 표시/완전 삭제는 API 응답의 device 상태를 즉시 local UI에 반영하며, 완전 삭제 확인은 장치명 재입력 대신 `YES` 또는 `네`를 사용한다.
- HA 연동 OFF 또는 broker/topic 변경 시 기존 연결이 살아 있으면 old broker의 discovery/state retained를 먼저 정리하고 마지막 QoS1 publish 완료 후 재구성한다. `enabled=false` 저장 회귀테스트를 추가했다.
- OTA 문서 구조를 정리해 정상 운영은 Backend/TCP10086 OTA, SoftAP/30300 OTA는 PC 직접 설치·복구 경로임을 명확히 구분했다.
- Home Assistant device `sw_version`은 SoftAP/SDK version(예: 0.1.54)이 아니라 bootinfo의 실제 application firmware version(예: 1.0.110/1.0.66)을 우선 사용한다.


## Web UI / Channel naming / Lovelace card (2026-09-09)

- GitHub `af950833/mttl_w01`의 카드형 UI/UX를 참고하되 현재 Standalone의 OTA/진단/lifecycle 구조는 유지하고 자체 구현으로 통합.
- `channels.name TEXT` additive migration 추가. 기존 운영 DB 사본에서 6 devices, 누적에너지, alias를 보존한 채 migration 및 `integrity_check=ok` 검증.
- 장치명과 Outlet 1~4 이름에 ✎ 편집 UI 추가. Web은 desktop 4열 / mobile 2×2 outlet tile, 현재 W / 누적 kWh / 온도 / 단일 ON-OFF toggle 표시.
- guest/read-only API에도 channel `name`을 포함해 로그아웃 상태에서도 사용자 이름 유지.
- channel rename API `PUT /api/devices/<mac>/outlets/<1-4>` 추가. 이름 변경 직후 HA Discovery 재발행.
- HA device/MQTT topic/entity `unique_id`는 MAC + selector 기반으로 고정하고 사용자 이름과 완전히 분리. switch, Power, Energy, Temperature의 integration-provided name만 변경.
- `static/mttl-up-card.js` 추가. 전체 12자리 MAC을 설정에 사용하고 HA Entity Registry의 `unique_id`로 실제 entity_id를 resolve하므로 HA에서 entity_id를 바꿔도 매핑 유지.
- Lovelace 카드: Total/Meter/ALL + 4채널, short press toggle, 0.6초 long press Power more-info, mobile 2×2/compact 4열 지원. selector 0 entity는 만들지 않고 ALL은 4개 physical switch service call로 구현.
- Backend Web에서 `/mttl-up-card.js` 다운로드 및 설치 안내 제공.
- regression **66 tests PASS**, Python/JS syntax check, Docker build PASS.
- 운영 `192.168.1.146`에 배포: container healthy, UP 장치 재연결 1 online, MQTT retained discovery/availability online 확인.


## Web row layout / HA device deep link / Lovelace control lock (2026-09-09)

- Web Outlet 4열 tile을 GitHub `af950833/mttl_w01` 스타일의 수평 row로 재구성: `✎ | 이름+누적/온도 | 현재 W | ON/OFF`. 긴 이름은 고정폭 ellipsis 대신 가변폭+wrap 허용.
- 장치 card에 `id=device-<mac>`/`data-mac`을 부여하고 `#device=<12자리 MAC>` deep link 지원. 최초 렌더 시 해당 카드로 scroll + 3.2초 강조하며 주기 refresh에서는 재스크롤하지 않음.
- `MTTL_PUBLIC_WEB_URL` 환경변수 추가. 설정 시 HA MQTT Discovery `device.configuration_url=<root>/#device=<mac>` 발행. 운영 URL은 `http://192.168.1.146:18282`.
- `mttl-up-card.js` 헤더에서 Online 왼쪽에 local lock toggle 추가. 기본 locked이며 잠금 해제 중에만 Outlet 1~4 short-press와 ALL service call 허용. long-press Power more-info는 잠금 중에도 유지.
- 카드 편집 UI에 `ALL ON/OFF 제어 표시` 추가. `all_control:false`면 ALL 영역을 렌더하지 않음. 미지정 기존 카드는 `true`로 호환.
- Python regression **67 tests PASS**, JS syntax check PASS, card contract mock test에서 locked service call 0회 / unlocked 1회 및 ALL show/hide 검증, Docker build PASS.


## RC3 Child Lock hardware follow-up — 2026-09-11

Hardware wire evidence on `d8aa59d24868` established that RC3 UP response transport is working. The initial read returned `up:cl:255`, which the backend previously discarded as out-of-range and therefore surfaced as a device response timeout. The backend now treats `255` as an uninitialized runtime mask and normalizes it once with `SET 0`.

Observed live round-trip after normalization:
- `GET -> up:cl:0`
- `SET 2 -> up:cl:2`
- `GET -> up:cl:2`
- `SET 0 -> up:cl:0`
- `GET -> up:cl:0`

This hardware-verifies RC3 UP Child Lock GET/SET response framing and runtime mask mutation. A later backend restart/device TCP reconnect again returned `up:cl:0`. An actual device reboot/power-cycle persistence check is still pending, so full Child Lock persistence/boot-load is not yet marked hardware verified.

Backend hardening:
- wire parser accepts `up:cl:0..255` so the sentinel can be diagnosed rather than misreported as timeout; application state still only accepts masks `0..31`;
- `255` is normalized to `0` before normal GET/HA bit control;
- connect-time one-shot Child Lock read keeps backend/HA state synchronized;
- target-specific diagnostic `0->2->0` code was removed after the successful test.

## HA Child Lock naming / card UI refinement — 2026-09-11
- Child Lock discovery names now follow Web UI names without changing stable unique IDs: master = `<device name> Button Lock`; outlet locks = `<outlet name> Button Lock`.
- `mttl-up-card.js`: local accidental-tap UI Lock changed from padlock to shield (`mdi:shield-lock` / `mdi:shield-outline`).
- Physical Child Lock is shown only while actually ON; the padlock opens that entity's Home Assistant More Info instead of toggling directly.
- The per-channel red/green relay dot was removed. The existing `mdi:power-socket-eu` icon is gray/off and green/on.
- Power and temperature are grouped with a 2px internal gap for consistent vertical rhythm.
- Master Child Lock is represented the same way on the ALL control.
