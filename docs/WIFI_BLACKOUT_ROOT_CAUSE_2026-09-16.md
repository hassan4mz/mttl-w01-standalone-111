# MTTL-W01 recurring Wi-Fi blackout root-cause analysis — 2026-09-16

## Executive status

The recurring UP/TCP failure is not a backend-only or TCP-PCB-only problem. A synchronized production packet capture proves that the device loses its usable IP/Wi-Fi path for about 16 seconds, then performs ARP/network recovery and reconnects.

Two timing layers must be kept separate:

1. **~22,260 s (~6 h 11 m) association-age RX/beacon stall** — initiating low-level mechanism still unresolved.
2. **~16 s outage/recovery interval after the trigger** — high-confidence match to the stock Realtek RTL8195A no-beacon/link watchdog.

Backend 0.2.11 retains the hardened transient-loss handling while firmware analysis continues.

## 2026-09-17 refinement — current conclusion

The overnight 1.0.66 control device reproduced the same boundary: association at 00:23:30 and deauth/disassoc at 06:34:44, or 22,274 s. One-second AP station traces and L3/TCP alignment place the **initial RX/beacon stall about 10–15 s earlier, centered near 22,260 s**. This proves the fault predates the 1.0.110/1.0.111 UP patch series.

Changing UP polling from 15 s to 13 s changed the command count by about 15.4% but did not move the failure boundary, so fixed poll/GETINFO count and cumulative UP traffic are no longer credible triggers. AP GTK/net reauth is 3600 s and no matching hourly Auth/Assoc cycle is observed. No executable 22,260 s literal or corresponding generic timer wrap has been found.

The strongest firmware evidence is the Realtek WLAN evolution: the product/3.5-family stack lacks a no-RX forced LPS recovery, while later public Ameba1 WLAN libraries retain `LPS_CTRL_DENY = 6`. In the same no-RX window it takes a short wake lock, updates `DelayLPSLastTimeStamp`, and calls `LPS_Leave()`. A conservative 1.0.111 test patch can reuse the old stack's existing type-4 `SPECIAL_PACKET` path to update that timestamp and leave LPS without replacing the WLAN library. This is **analysis-only and has not been packaged or deployed**.

Current detailed evidence:
- `WIFI_BLACKOUT_FW66_LPS_EVIDENCE_2026-09-17.md`
- `FIRMWARE_LPS_PATCH_FEASIBILITY_2026-09-17.md`

## Production evidence

Captured device: `88d03934e51d` / `192.168.1.191`, firmware `0.1.55-1.0.111`.

- 18:47:13.703 KST: last normal ICMP reply.
- 18:47:14: ICMP replies stop.
- 18:47:15.771: backend sends periodic GETINFO; TCP data is not acknowledged.
- 18:47:20.773: backend TCP_INFO classifies `unacked_at_timeout`, `retrans_delta=4`.
- 18:47:29.967 / 18:47:30.102: ARP probes for the device's own `192.168.1.191` appear with sender IP `0.0.0.0`.
- 18:47:30.645: device ARPs for backend `192.168.1.146`; ICMP immediately recovers.
- 18:47:30.939: first returning SYN uses the same source port `30888`.
- 18:47:33.705: fresh TCP handshake succeeds; bootinfo and GETINFO resume.

The same `unacked_at_timeout` pattern has been observed on all four deployed units and their UP session lifetimes cluster around ~22,268 seconds.

## Realtek WLAN watchdog match — high confidence

The original Realtek/Ameba WLAN library used by the stock firmware contains:

- `rtw_mlme.o::_dynamic_check_timer_handlder`
  - rearms itself every **2000 ms**.
  - calls `rtw_dynamic_chk_wk_cmd`.
- `rtw_cmd.o::rtw_dynamic_chk_wk_cmd`
  - while associated as a station, calls `linked_status_chk`.
- `rtw_mlme_ext.o::linked_status_chk`
  - compares station RX/TX counters with the previous sample and checks `HalGetNullTxRpt`.
  - when traffic/null-TX progress is absent, increments a no-beacon/link-failure counter.
  - at the beginning of a failure it sends probe requests.
  - after repeated failures it asks the LPS path to wake/leave power save.
  - when the counter reaches `g_max_no_beacon_retry`, it calls `receive_disconnect(..., 0xffff)`.
- `g_max_no_beacon_retry` is **8** in the bundled Realtek library.

Therefore the normal watchdog detection window is approximately:

`2 s × 8 = 16 s`

This matches the production packet capture: usable traffic disappears at ~18:47:14 and ARP/IP recovery begins at ~18:47:30.

The stock 1.0.66 image already contains the same `linked_status_chk` logic (`sub_1003D5AA`) and the same threshold. This watchdog was not introduced by the 1.0.110/111 UP patches.

## Network-stack consequence

The matching Ameba SDK `wifi_disconn_hdl()` stops DHCP and marks the lwIP interface link-down after WLAN disconnection. This explains why ICMP and TCP disappear together. The backend GETINFO timeout is an observation of the link outage, not its cause.

## Trigger hypotheses — 2026-09-16 snapshot (superseded by 2026-09-17 refinement above)

### P1 — WLAN/AP link-layer event at association age ~22.2 ks

Strongest remaining class of causes. The Realtek watchdog only fires after it sees no link progress; something first makes the station stop receiving/transmitting/beacons. AP `wl sta_info` also showed association-age resets around the failures.

Need to distinguish:
- AP-initiated deauth/disassoc/rekey/steering, versus
- RTL8195A radio/MLME state becoming stuck and self-recovering through the no-beacon watchdog.

The decisive evidence is AP-side `wlcevent`/deauth/disassoc reason logging or a radio/bridge capture spanning one boundary.

### P2 — per-station WPA/PTK rekey or related security transition

Still plausible because a per-association security event could follow station age. The RTL client group-key path is reactive to EAPOL-Key frames; no client-side six-hour group-key timer was found.

Public ASUSWRT source defaults commonly show GTK rotation at 3600 s and PTK rekey disabled, but the actual XD6 NVRAM values must be read before using that as evidence for this installation. A BSS-wide GTK timer is also less consistent with the observed staggered per-device failures.

### P3 — RTL8195A LPS/power-save state bug or long-lived driver counter

Possible but not proven. `linked_status_chk` explicitly invokes LPS control after several failed checks, so LPS participates in recovery. No ~22,268 s LPS timer or matching threshold has been found so far.

No immediate/constant around 11,134 dynamic checks (`22,268 / 2`) was found in the application disassembly or WLAN object disassembly.

### P4 — DHCP T1/renew

Lower priority than link-layer causes.

- Standard lwIP DHCP renewal does not intentionally tear down the Wi-Fi association.
- The captured recovery clearly includes WLAN/IP link loss and later ARP reinitialization.
- No broadcast DHCP Discover/Request was observed in the capture.

Caveat: a T1 renewal is normally unicast to the DHCP server, so a capture on the HA Ethernet port cannot prove that no unicast DHCP renewal occurred elsewhere on the switched LAN. AP/router-side capture is required to completely exclude this path.

### P5 — UP application / backend polling / heap / TCP-only failure

Effectively excluded as the root trigger by production evidence:

- ICMP disappears before/with the failed GETINFO.
- all four devices reproduce the lifetime pattern.
- heap remains stable.
- poll interval changes did not move the failure.
- legacy MQTT sessions could live much longer than six hours.

## Rejected numeric shortcuts

### Raw `0x5700` / 22,272 pattern

A matching byte pattern exists once in stock 66/110/111, but no executable timer xref has been established. It must not be described as a confirmed 6h11 timer.

### Raw `0xADF8` / 44,536 pattern

Although `44,536 = 2 × 22,268`, inspection does not support a DHCP-lease interpretation:

- one apparent match is an unaligned byte overlap inside code/data,
- the clear aligned occurrence is inside a repeated WLAN/PHY data table with interleaved `0x34` entries,
- there is no immediate `#44536` use in code and no useful pointer/xref proving timer semantics.

It is therefore not evidence of a 44,536-second lease/timer.

## Next analysis order — 2026-09-16 snapshot

1. Obtain AP-side deauth/disassoc/wlcevent reason at a boundary and actual security rekey NVRAM values.
2. Trace RTL8195A MLME/LPS/TSF driver state for long-lived per-association counters and overflow conditions.
3. Trace PTK/group-key transition paths and compare against the stock 66 implementation.
4. Capture DHCP renewal at AP/router side to close the T1 hypothesis.
5. Only after the initiating link-layer event is identified, design a firmware-side preventative patch. Avoid weakening or disabling the 16-second no-beacon watchdog prematurely; it is currently the mechanism that restores connectivity.
