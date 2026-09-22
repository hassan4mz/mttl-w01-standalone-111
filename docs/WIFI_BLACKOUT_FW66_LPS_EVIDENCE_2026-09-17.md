# FW 1.0.66 overnight boundary result — 2026-09-17

Device: D8:AA:59:D2:48:68 / 192.168.1.90 / FW 1.0.66

## AP timeline
- 2026-09-17 00:23:30 Assoc successful
- 2026-09-17 06:34:44 Deauth/Disassoc
- 2026-09-17 06:34:47 Auth/Assoc successful
- Association lifetime to deauth: 22,274 s

This matches the established FW 1.0.111 cycle of roughly 22,273 +/- 2 s.
Therefore the periodic WLAN failure predates the 110/111 patch series and is present in stock/legacy FW 1.0.66.

## L3/TCP boundary from HA ring capture
- 06:34:17.592: last normal GETINFO response from .90
- 06:34:32.459: next GETINFO sent; no response, retransmits continue
- 06:34:48.032/48.162: device ARP probes for its own IP after WLAN recovery
- 06:34:48.993: first reconnect SYN collides with stale TCP state
- 06:34:51.770: second SYN succeeds
- 06:34:51.773+: UP TCP traffic normal again

The observed order is consistent with the FW111 captures: WLAN/RX path fails first, TCP becomes unavailable as a consequence, then WLAN reassociation/IP recovery/TCP reconnect follow.

## Root-cause refinement — 2026-09-17 09:37 KST

### 13 s vs 15 s poll experiment
The periodic failure time is unchanged when the UP polling interval is changed from 15 s to 13 s.
At an association lifetime of about 22,260 s, this corresponds to roughly 1,484 polls at 15 s versus 1,712 polls at 13 s (about 15.4% difference).
Therefore a fixed UP poll count, GETINFO command count, or cumulative UP traffic threshold is not a credible initiating mechanism.

### Trigger time vs disconnect time
The AP Deauth/Disassoc event at about 22,273-22,275 s is downstream of the initial failure.
The 1 s boundary `wl sta_info` traces show receive progress stopping about 10-15 s earlier. After aligning the `sta_info` station-age basis with the actual association event, the first RX/beacon stall is approximately 22,259-22,265 s, centered very close to 22,260 s (6 h 11 min).
The subsequent 2 s `linked_status_chk` watchdog/probe sequence accounts for the later Deauth/Disassoc timestamp.

No meaningful executable literal for 22,260 s was found in the product APP using the following representations: 0x56F4 seconds, 0x2B7A two-second ticks, 371 minutes, 22,260,000 ms, or 0x2B7A0000 at 32,768 Hz. The generic 32-bit 32.768 kHz GTIMER wraps at about 36.4 h, not 6 h 11 min.

### Realtek 3.5 branch vs later public WLAN stack
The product WLAN fingerprint matches the old Realtek 3.5/3.5.2 family:
- `linked_status_chk`: 0x274 bytes, no LPS forced-recovery command
- `lps_ctrl_wk_hdl`: 0x64 bytes, accepts only LPS control types 0..5

The earliest public Ameba1 WLAN library found in the SDK git history (2019-10-24) already contains `LPS_CTRL_DENY = 6`, and the same behavior remains present through the sampled 2020-2022 public libraries. This is therefore a sustained Realtek recovery mechanism rather than a temporary experiment.

In the later stack, when `linked_status_chk` observes unchanged RX counters and NullTx failure for more than four checks, it issues:
`rtw_lps_ctrl_wk_cmd(adapter, LPS_CTRL_DENY, 1)`.
The `LPS_CTRL_DENY` handler performs:
1. `rtw_wakelock_timeout(1000)`
2. updates `DelayLPSLastTimeStamp` from `rtw_get_current_time()`
3. calls `LPS_Leave()`

Thus the later driver explicitly forces the radio out of LPS and suppresses immediate LPS re-entry in the exact no-RX window that leads the old product stack to disconnect after eight misses.
The later `linked_status_chk` also reads BB registers 0xC50 (OFDM DIG/IGI) and 0xA0A (CCK PD/CCA threshold) for diagnostics during this condition, further tying the recovery path to PHY/RX state rather than TCP or application polling.

### Other 3.5 -> later changes checked
- Core PMU/32K/TSF call structure is largely unchanged; many size changes are structure-layout/refactoring effects rather than new long timers.
- `OnBeacon` keeps the same call set and timer behavior; no 6 h timer was introduced/removed there.
- `odm_DIGbyRSSI_LPS` changes gain thresholds/diagnostics (including an upper value change from 62 to 70) but does not introduce a long-duration trigger.
- The later `rtl8195a_recv_tasklet` adds handling around RX `crc_err`/`icv_err` and connection cleanup. This is a received-frame error path and does not explain the observed complete no-RX stall.
- The larger later `ClientEAPOLKeyRecvd` implementation does not add a FreeRTOS long-period timer; no `xTimer*` call was found in the WLAN object code for this path.
- AP GTK rekey / net reauth is configured at 3600 s, but no hourly Auth/Assoc cycle is observed for the MTTL stations and it does not match the association-relative 22,260 s failure.

### Current root-cause model
`association -> approximately 22,260 s deterministic WLAN/PHY/LPS-side RX stall -> old 3.5 stack fails to force LPS exit -> 2 s linked-status watchdog probes and counts misses -> disconnect/reassociate around 22,273 s -> DHCP/IP recovery -> TCP reconnect`

The initiating 22,260 s mechanism is still not identified as a specific software timer or counter. The strongest static evidence now indicates that Realtek's later engineering response was to recover the no-RX/LPS state (`LPS_CTRL_DENY`) rather than to expose or remove a visible 6 h 11 min application timer.
