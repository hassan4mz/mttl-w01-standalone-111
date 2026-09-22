# MTTL-W01 RTL8195A 6h11 WLAN/LPS patch feasibility — 2026-09-17

## Conclusion
A targeted firmware recovery patch is technically feasible on canonical FW 1.0.111 without replacing the WLAN library.
The preferred first candidate is a native-old-stack recovery patch: when `linked_status_chk` reaches the same no-RX threshold used by later Realtek stacks, queue the existing old-stack LPS control type 4. Type 4 already performs `DelayLPSLastTimeStamp = now` followed by `LPS_Leave()`.

This is not equivalent to a full later `LPS_CTRL_DENY` backport because later revisions also take a short wake lock (~1–3 s depending on revision). The old stack nevertheless prevents immediate LPS re-entry for about 1 s through `PS_RDY_CHECK(DelayLPSLastTimeStamp)`.

## Why this is feasible
- FW 1.0.66 and canonical 1.0.111 are byte-identical in the relevant WLAN functions and addresses.
- Existing 1.0.111 Child Lock / UP / OTA patches do not overlap the WLAN patch region.
- `rtw_lps_ctrl_wk_cmd()` already accepts and queues LPS control requests from normal WLAN paths.
- Old type 4 is already called by `rtw_xmit()` with `queued=1`, so the execution context is product-native and established.
- Later Realtek `LPS_CTRL_DENY` also uses `queued=1` from `linked_status_chk`.
- Old type 4 maps to `rtw_get_current_time()` -> store `DelayLPSLastTimeStamp` -> `LPS_Leave()`.
- No new structure offsets, new OS primitive, or WLAN ABI is required.

## In-place implementation candidate
Canonical 1.0.111 contains a diagnostic-only no-RX BB-register logging block at `0x1003D650..0x1003D67D`.
It reads/logs CCK/OFDM BB registers (`0xA0A`, `0xC50`) when a debug flag is enabled, but is not part of the WLAN control-state transition.

The candidate reuses this block instead of introducing a code cave:

```text
0x1003D64E  NOP                       ; both debug states enter recovery check
0x1003D650  LDRB.W r0,[r4,#0x7C0]    ; stored no-RX count
0x1003D654  CMP r0,#5
0x1003D656  BLS 0x1003D662
0x1003D658  MOV r0,r4                 ; adapter
0x1003D65A  MOVS r1,#4                ; native old LPS control type 4
0x1003D65C  MOVS r2,#1                ; queue command
0x1003D65E  BL 0x10036F26             ; rtw_lps_ctrl_wk_cmd
0x1003D662  B 0x1003D6FA              ; resume original linked_status_chk
```

The old function increments the no-RX counter before this location. Testing stored count `>5` is therefore equivalent to the later Realtek check of pre-increment count `>4`.

Offline candidate:
`CANDIDATE_ONLY_1.0.111_native_type4_norx_app.bin`

- same image size: 327,928 bytes
- changed range only: `0x1003D64E..0x1003D67D`
- changed bytes: 48
- canonical SHA256: `6b2d650a35728eb7fbcb16f4c69025f36dc49fa8da21cc4988710315158d37f6`
- candidate SHA256: `5e780aa38f2385f3f718d8f24f01dc36fe11c1561b14abdd5b87476df007d00f`

This file is analysis-only and has not been packaged or deployed.

## Risk assessment

### Candidate A — native type-4 no-RX recovery
Implementation risk: low-to-medium.
Runtime risk: medium-low.
Root-cause coverage confidence: medium-high, but not proven until the 22,260 s boundary is crossed on hardware.

Main residual risk: type 4 lacks the later driver's explicit short wake lock. The old stack's timestamp gate suppresses LPS re-entry for roughly 1 s, which may be enough for a forced `LPS_Leave()` to restore RX, but this is not statically provable.

Expected benign side effect: the original optional no-RX BB debug log at this block is removed. Normal WLAN behavior is otherwise unchanged until no-RX count exceeds the threshold.

### Candidate B — full later type-6 backport
Implementation risk: medium-high.
Runtime risk: medium.
Not recommended as first patch.

Reasons: old `lps_ctrl_wk_hdl` uses a six-entry TBB table (types 0..5); adding type 6 requires handler restructuring/trampoline. The old SDK also lacks the later `rtw_wakelock_timeout` API, so exact semantics require introducing or emulating an OS-level wake-lock mechanism.

### Candidate C — disable LPS globally
Implementation risk: low-to-medium.
Runtime/behavioral risk: medium.
Useful as a controlled causality test, not preferred as production behavior.

Because the strip is mains-powered, energy cost is not important, but disabling LPS changes WLAN behavior continuously rather than only during the pathological no-RX state.

### Candidate D — replace lib_wlan.a with a later Realtek library
Risk: very high. Reject.
Adapter / pwrctrl / mlme structure layouts and offsets changed across SDK generations. A binary library swap is ABI-unsafe and could fail far outside the LPS path.

### Candidate E — timer-based reconnect shortly before 6h11
Risk: medium-high and architecturally poor. Reject.
It depends on an unexplained association-relative time and deliberately creates recurring disruption instead of repairing the detected no-RX state.

## Failure modes to watch for with Candidate A
1. Recovery command is queued but the radio falls back into LPS after the old 1 s inhibit window; blackout still occurs.
2. Genuine long RF/AP outages also cross the no-RX threshold and invoke type 4. This should be benign because it only asks the existing stack to leave LPS, but must be tested.
3. Command queue allocation/enqueue failure. Later Realtek also uses the queued path; failure would simply leave existing watchdog/disconnect recovery as fallback.
4. Loss of the optional BB-register diagnostic log in the reused 44-byte region. This affects observability, not relay or network state logic.
5. Exact 22,260 s initiating trigger remains unknown. The patch targets the observed failure state and the recovery Realtek later added; it does not claim to remove the initiating trigger.

## Safe test gate
Do not fleet-deploy first.
Use one device and retain canonical 1.0.111 / rollback artifacts.

Required pre-flash checks:
- verify only the intended 48-byte range differs
- disassemble the final packaged APP after FWR generation
- verify package decode/encode round-trip and image checksums
- verify OTA and fallback/recovery path

Required functional checks before the long run:
- cold boot / warm reboot
- provisioning / reconnect
- four outlets + All control
- Child Lock persistence and physical-button policy
- UP connect / GETINFO / control
- AP interruption / reconnect
- no spontaneous relay-state change

Long-run acceptance:
- cross at least two expected 22,260 s boundaries (~12.5 h minimum; 18 h preferred)
- no RX stall at the boundary, or clear immediate recovery before TCP blackout
- no reboot or relay mutation
- no new association churn
- no memory/command-queue growth

If Candidate A still blacks out at the same boundary, do not stack additional blind patches. The next controlled discriminator should be one-device global-LPS-off. If global LPS off eliminates the boundary, then implement a stronger DENY-equivalent wake hold; if it does not, the LPS hypothesis must be revised.

## SoftAP / recovery-path isolation check — 2026-09-17
The candidate patch block is inside the STA-associated branch of `linked_status_chk()`.
Stock 3.5 disassembly resolves the gate as:

```text
linked_status_chk:
    ...
    BL is_client_associated_to_ap
    CMP r0,#0
    BEQ non-STA/IBSS path
    ...
    [no-RX watchdog block patched at canonical 0x1003D64E..0x1003D67D]
```

Canonical 1.0.111 has the same gate at `0x1003D5BE -> BL 0x1004093C`; symbol resolution from the matching SDK object identifies it as `is_client_associated_to_ap()`.
Therefore pure SoftAP/AP mode does not execute the modified no-RX block. If the application image boots correctly, forcing the unit back to provisioning SoftAP should bypass this patch logic.

Residual brick risk remains only for failures outside this runtime path: malformed package/header, corrupted APP image, or a boot-time failure unrelated to the no-RX patch. These must be controlled by final-package decode/disassembly/hash checks before flashing.
