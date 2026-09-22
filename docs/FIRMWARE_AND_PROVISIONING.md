# Firmware / OTA / Provisioning

## Current release

- Canonical firmware: **1.0.111**, `HARDWARE_OTA_BOOT_VERIFIED`
- SHA256: `7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb`
- Device SW / SoftAP: `0.1.55`
- Official rollback: **1.0.110**, SHA `318f44567cd90613a3a485a66bb398c80bc60c891699772d438c9cfd6cb33059`
- Stock-compatible rollback: **1.0.66**, SHA `d780b578af69d52f3a05191a8e7d91a20e05085a912722327481cd5663682c04`
- Pinned source 1.0.105 SHA: `f78aee903d392f2b8470c845d9e76c33d91fdcf1da57e6c7e714cbedc9edaefa`

1.0.112/1.0.113/1.0.114 are historical/withdrawn and are not active profiles. Never flash the withdrawn 1.0.113. Historical evidence remains under `re/`. Operational Backend startup removes any legacy 112/113/114 profile rows and `official_1.0.112/113/114.fwr` residue from the active store; OTA job history is preserved. The production patcher/assets no longer include the failed 1.0.113 builder/blobs.

## Hardware identity

- SoC: **Realtek RTL8711AF** (Ameba-1 / Cortex-M3)
- Real-device JEDEC RDID: **`C2 20 14`** = Macronix 8-Mbit / 1-MiB SPI NOR
- RDID is shared by MX25L8005 and MX25L8006E; exact die revision is not asserted.
- Both candidates are ~100,000 P/E-cycle class.
- SDK function names containing `RTL8195A` are common Ameba-1 SDK names, not the board SoC identity.

See `HARDWARE_IDENTIFICATION.md` for evidence and diagnostic provenance.

## Version lineage

### LG U+ 1.0.66 baseline

Stock-compatible firmware. It does not contain the UP-local event extension or Child Lock. Normal status LED behavior is stock. OTA uses TCP80.

### UP 1.0.110 — changes from 1.0.66

- UP TCP10086 local backend support.
- Physical relay changes immediately push `up:event:onoff:<selector>:<on|off>` instead of waiting for the next GETINFO poll.
- Normal Wi-Fi + UP connected state turns the orange status LED off instead of continuing the legacy 1.5s blink.
- Child Lock is not present.
- OTA uses TCP80.

### UP + Child Lock 1.0.111 — changes from 1.0.110

- Adds physical short-press Child Lock for All/master + Outlet1..4.
- Lock state persists in bank1 offset `0x900..0x903`: magic `43 4C`, mask, inverse mask. Invalid/read-fail/unresolved is fail-open.
- Long Factory and RESET_AP button paths remain available; remote UP/HA relay control and protection paths remain enabled.
- Factory clears Child Lock; RESET_AP preserves it.
- Retains 1.0.110 immediate physical event reporting and normal-connected orange LED OFF behavior.
- Device SW / SoftAP is 0.1.55.
- Raw HTTP OTA connect uses TCP49217 first and retries TCP80 only when the primary connect fails. HTTPS/443 path is unchanged.

## Backend-managed OTA

Already-provisioned devices stay on home Wi-Fi and receive `up:ota:<backend IP>` over the existing TCP10086 session. Backend serves the selected validated FWR from container TCP10080. Production Compose publishes both host 49217 and host 80 to this listener.

```text
Backend TCP10086 -> up:ota:<host>
Device -> TCP49217 /ota.bin        (1.0.111 primary)
       -> TCP80 /ota.bin           (111 connect fallback; 110/66 direct)
Device -> reboot -> TCP10086 reconnect
Backend -> target version + fresh GETINFO verification
```

Rules:

- Selecting/preparing a profile never auto-queues OTA.
- One OTA job runs at a time.
- Idle `/ota.bin` returns 404.
- Timeout/ambiguous command result is not automatically retried.
- `SUCCESS`/`DELAYED_SUCCESS` requires target-version reconnect + fresh GETINFO after transfer.

## PC direct OTA / provisioning

PC tooling intentionally hosts OTA on **TCP80 only**. This works for all official versions because 66/110 use TCP80 and 111 falls back from 49217 to TCP80.

Windows entry points:

- `BUILD_FIRMWARE_WINDOWS.bat` — build/verify exact 66/110/111 from pinned 1.0.105 or exact LG U+ 1.0.66 source; no FWR is bundled.
- `OTA_WINDOWS.bat` — SoftAP connect and OTA only.
- `PROVISION_GENERIC_WINDOWS.bat` — Wi-Fi provisioning only; does not change stored UP backend address.
- `PROVISION_UP_WINDOWS.bat` — `up:ip` + `up:connect`, cmd103 exact-MAC attach verification.
- `INSTALL_WINDOWS.bat` — source/build → 111 selftest → SoftAP OTA → UP provisioning.

Python entry point: `provision/mttl_setup.py`. Legacy `mttl_up_setup.py` remains supported.

### SoftAP recovery UX

SoftAP auto-detection failure is treated as recoverable rather than fatal. The PC tool asks the user to connect to `TONLY_TAP_*`, then performs two Enter-triggered rechecks. On Windows each recheck also retries available scan/saved-profile connection. After two failures the user chooses `y/n`: `y` advances to the real operation so its protocol checks make the final decision; `n` exits cleanly. The user is not asked to type the SoftAP SSID manually.

The same recovery flow is used if Wi-Fi roams away just before OTA/Provision or if the SoftAP service does not return promptly after OTA. Protocol mutation ambiguity is not auto-retried: after an OTA/provision command may have been accepted, the tool verifies state rather than blindly sending it again.

## Android

Current app: **1.5.8 / versionCode 16**.

- Exact official 66/110/111 builds are supported.
- Unknown-SHA imported FWR is accepted only after wire/header/model/checksum/version validation and three explicit warning confirmations.
- cmd103 exact MAC is preferred for UP attach verification; SSID suffix is fallback only.
- Legacy 0.1.54 root TCP80 redirect is opt-in and temporary.
- 1.5.8 retains SoftAP auto-scan/connect for provisioning, hardens wrong-device/duplicate-operation/route checks, and blocks vendor-source download/build while attached to an MTTL SoftAP unless a validated source cache already exists.
- UP Backend IPv4 is not restricted to 192.168.1.x; direct SoftAP OTA route validation remains intentionally strict where the device path requires it.

## Static audit notes

Fresh 66→111 audit: `re/recheck_66_vs_111_20260912/FULL_STATIC_REAUDIT_66_TO_111_2026-09-12.md`. All 38 final diff runs are classified.

The current 111 OTA fallback wrapper is runtime-safe but is placed in dormant MQTT CONNECT Will fields rather than a true dead cave. Exact 111 default Will flag is 0 so those fields are not consumed. If hardening the binary later, relocate this wrapper before enabling MQTT Will.

## Distribution / source policy

Distribution ZIPs and Docker images contain no `.fwr` binaries. Firmware is prepared only in the user's environment. The tools first try the pinned 1.0.105 vendor URL; if unavailable, place a user-obtained exact 1.0.105 or LG U+ 1.0.66 FWR in the PC tool folder, or `/data/firmware-source/` for Backend/Docker. Exact SHA256 matching is mandatory. Generated 66/110/111 files live only in the user's runtime/work directory.
