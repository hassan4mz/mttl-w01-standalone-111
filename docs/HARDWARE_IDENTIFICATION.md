# MTTL-W01 Hardware Identification

## Confirmed controller

- Product: LG U+ / Tonly **MTTL-W01** smart power strip.
- Main Wi-Fi MCU / SoC: **Realtek RTL8711AF**.
- Architecture: **ARM Cortex-M3**, Realtek Ameba-1 family, 2.4 GHz 802.11 b/g/n 1T1R WLAN SoC.
- Package class documented for Ameba-1 RTL8711AF: **QFN48, 6 x 6 mm**.
- RTL8711AF configuration provides **1 MiB flash** and about **512 KiB SRAM**.

Public teardown evidence identifies the controller on the MTTL-W01 Control PCB as RTL8711AF:
- Hackaday project `LG Smart Power Plug MTTL-W01`, Device Internals:
  https://hackaday.io/project/202043-lg-smart-power-plug-mttl-w01/log/237419-2-device-internals

Realtek Ameba-1 documentation groups RTL8711AF with the RTL8711AM / RTL8195AM software family. Therefore firmware symbols such as `SpicReadIDRtl8195A` are **SDK-family names and are not evidence that the physical MTTL-W01 SoC is RTL8195A**.

Reference:
- Realtek Ameba-1 UART update application note, AN0096:
  https://raw.githubusercontent.com/ambiot/ambd_sdk/12dab4363fd0087eb4874461f8d3f6094110595f/doc/AN0096%20Realtek%20Ameba-all%20xmodem%20uart%20update%20firmware.pdf

## Confirmed flash identity from real hardware

On 2026-09-12 a temporary diagnostic build based on canonical 1.0.111 read the JEDEC ID that the stock Realtek boot code had already cached after `SpicReadIDRtl8195A()`.

Observed real-device JEDEC RDID:

```text
C2 20 14
```

Interpretation:

- `C2` = **Macronix / MXIC** manufacturer ID.
- `20 14` = Macronix 8-Mbit Serial NOR identity.
- Capacity = **8 Mbit = 1 MiB**.
- Linux SPI-NOR maps `C2 20 14` to `mx25l8005` / 1 MiB.
- Macronix's own migration note shows **MX25L8005 and MX25L8006E share the same RDID `C2 20 14`**.

Therefore the strongest exact statement supported by hardware evidence is:

> **RTL8711AF with Macronix 8-Mbit / 1-MiB SPI NOR, JEDEC C2 20 14, MX25L8005 / MX25L8006E-compatible family.**

Do **not** claim the exact flash die revision is specifically MX25L8006E or specifically MX25L8005 from RDID alone. The two parts cannot be distinguished by the three-byte `9Fh` RDID value. A package/die marking or another distinguishing command would be needed to resolve the exact revision.

References:
- Macronix AN0055 migration note: MX25L8005 and MX25L8006E both RDID `C2 20 14`:
  https://www.macronix.com/Lists/ApplicationNote/Attachments/1991/AN0055V4-Migrate_to_MX25L1606E_and_MX25L8006E-0118.pdf
- MX25L8005 datasheet:
  https://www.macronix.com/Lists/Datasheet/Attachments/8568/MX25L8005ZM%2C%203V%2C%208Mb%2C%20v2.0.pdf
- MX25L8006E datasheet:
  https://www.macronix.com/Lists/Datasheet/Attachments/8680/MX25L8006E%2C%203V%2C%208Mb%2C%20v1.6.pdf

## Flash endurance relevance

Both plausible Macronix revisions are in the same endurance class:

- MX25L8005: **minimum 100,000 erase/program cycles**, 4-KiB sector erase supported.
- MX25L8006E: **typical 100,000 erase/program cycles**, 4-KiB sector erase supported.

MTTL-W01 firmware uses a config sector with read-modify-erase-rewrite behavior. Stock relay persistence and canonical 1.0.111 Child Lock persistence share this flash erase domain. The stock firmware already avoids relay-state writes when the persisted relay state is unchanged; Backend also does not periodically SET Child Lock on reconnect/poll.

Consequently, normal Child Lock usage contributes negligible wear compared with ordinary relay-state persistence. High-frequency relay automation is the more relevant wear case.

## Diagnostic evidence and cleanup

Temporary diagnostic FWR used for the JEDEC observation:

```text
SHA256 ed431d430ab388f6507dfff00a915453a318775d32a92320e711e50ab28e93ea
```

It modified only the canonical 1.0.111 Child Lock GET diagnostic path to report the cached ID and did not issue a new runtime SPI RDID transaction.

After observing `C22014`, the test device was reflashed with canonical official 1.0.111 and Backend recorded OTA `SUCCESS` plus GETINFO verification for canonical SHA:

```text
7766d8c309ea77361a31b2af0c710ee24d05d076a48ecc0c8ee9b627bb288dfb
```

The temporary Backend diagnostic profile/parser/UI and diagnostic FWR were removed after the test. The historical OTA job remains only as audit evidence.
