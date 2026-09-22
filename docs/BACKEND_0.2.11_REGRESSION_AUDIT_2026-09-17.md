# Backend 0.2.11 regression audit

## Scope

Backend 0.2.10 cleanup removed heap/MEMORY investigation code. During that cleanup, two adjacent Home Assistant settings UI helpers were accidentally removed together with the intended heap helper:

- `haIntervalOptions()`
- `applyHA()`

`loadHA()` and `saveHA()` still called `applyHA()`, so opening the administrator Settings dialog failed with `applyHA is not defined`.

## Fix

- Restored `haIntervalOptions()` and `applyHA()` from the pre-cleanup known-good source.
- No HA DB migration or stored configuration was changed.
- Existing encrypted MQTT password, broker/user/topic settings and all existing device/channel data remain intact.
- Added static UI contract tests so HA settings DOM IDs and required helper functions cannot be silently removed by a future cleanup.
- Added a synthetic Backend 0.2.0 SQLite upgrade test. It verifies preservation of device alias, channel name, observed relay state, lifetime energy, polling setting and HA MQTT settings while allowing the current backend to add its `auth_sessions` table and clear stale transport-online state at startup.

## 0.2.0 compatibility audit

Compared the preserved Backend 0.2.0 source snapshot against 0.2.11:

- fixed `/api/*` route set: 18 in 0.2.0, 18 in current; no baseline route disappeared.
- administrator settings DOM IDs: 46 in 0.2.0, 46 in current; no baseline ID disappeared.
- all static settings-page onclick/onchange handlers resolve to defined JavaScript functions.
- `backend.py` top-level functions: no 0.2.0 function removed.
- `HAController`: no 0.2.0 method removed.
- `Manager`: the only removed 0.2.0 method is `_record_getinfo_result`, intentionally retired with heap/GETINFO investigation telemetry in 0.2.10; it is not part of control behavior.
- existing SQLite tables/columns are preserved; current backend only adds `auth_sessions` for persistent administrator login.
- HA defaults remain 0.1 W / 30 s for power and 1 Wh / 60 s for energy; device availability grace remains 30 s.

## Regression result

Full suite after the fix and compatibility tests:

- 161 tests run
- OK
- 4 skipped because the optional local 1.0.105 firmware fixture is intentionally not distributed with the backend source tree

The 0.2.8–0.2.10 transient-loss policy, 45-second hard command deadline, observed-only HA relay state, 30-second HA availability grace, OTA safety and Child Lock behavior are unchanged.

## Production verification

Backend 0.2.11 was rebuilt and deployed on the production server. Docker health is `healthy`; `/api/health` reports Backend `0.2.11` with three currently powered devices online. The served `app.js` contains exactly one `applyHA()` and one `haIntervalOptions()` definition, and the settings load contract resolves correctly. The three powered devices reconnected after the container recreation and resumed normal GETINFO/Child Lock synchronization. After the 2026-09-17 distribution refresh, production was rebuilt/recreated once more from the final release tree; runtime hashes now match `backend.py`, `ha_bridge.py`, `firmware_manager.py`, `static/app.js`, `static/index.html`, `static/provision.zip`, and `static/provision-android.apk` in the production source tree. The served Provision ZIP and Android APK therefore correspond to the active 2026-09-17 Provision package and Android 1.5.8 artifact.
