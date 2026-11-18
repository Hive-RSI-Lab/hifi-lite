# Self-test reports

One report per board per day, from `tools/diag_report.py` (on-board `api diag`, `state` and the self-test, over the remote bench). This page compares each board's latest. Rebuild it with `tools/diag_report.py --readme-only`.

## Reading it

All five Lites pass their self-test, and nothing here needs action before the next build. Every figure in this reading is filled in from the same run as the table below (`NOTES.md` holds placeholders that `--readme-only` fills), so the two can't disagree. In plain words:

- **Lites 3 and 4 run the diagnostics code** (`api diag`), so only they have stack, CPU, Wi-Fi drop, boot, underrun and NVS figures. Lite 4 has it because the code is now in `main`, and the security lane flashed Lite 4 from `main`. Lite 3 runs the same code plus the review fixes (PR #192). Lites 1, 2 and 5 are read from `state` and the self-test, which give heap, PSRAM, RSSI and the power rails; their columns fill in when they next take a `main` image. Uplink and OTA figures are only on Lite 3, because the report runs the 1 MB uplink test only on a board this lane holds.
- **Heap.** Every board reads it the same way (`state`'s byte-addressable internal RAM), and all five are in line: 13 to 16 KB free. An earlier version of this page said Lite 3 had twice the heap of the others. That was wrong: its old `diag` also counted IRAM. The lowest free heap since boot is 10.3 KB, on Hi-Fi Lite 1, after only 34 s up. That still works, but it is the tightest figure on the page: if a board ever fails to allocate, start here. The soak on Lite 3 tracks it over hours.
- **Stacks.** The tightest app stack (the task, then the bytes it has never used) is hifi_power 1008 B on Lite 3 and hifi_relay 716 B on Lite 4. `hifi_relay`, which parses JSON, is the one to watch: on the 4 KB stack in `main` it is already that low after 13 minutes on Lite 4, and Lite 3 read 708 B on it hours after boot. PR #192 gives it 5 KB, and Lite 3 now has 1.8 to 1.9 KB of it free. `hifi_power` sits just under the 1 KB line the report flags on both. The `ipc0`, `ipc1` and `IDLE` tasks run close to their stack size by design (ESP-IDF sizes them), so they are left out.
- **CPU.** The busiest core was 18.1 % busy on Lite 3 and 10.6 % on Lite 4, mostly the I2S output and the power monitor, so there is plenty of headroom. A test tone is different: `hifi_tone` takes most of a core while it plays (the soak reads 80 to 100 % on that core), so read CPU only between tones.
- **Wi-Fi.** The boards read -51 to -44 dBm; the weakest is Hi-Fi Lite 3. That is still a good signal, but if it ever drops audio, check it first. It is probably placement or its antenna rather than a fault.
- **Uplink and timing.** 1 MB went up Lite 3's bench link at 2.1 Mbit/s. Wi-Fi and the remote link came up 9.0 to 9.3 s after boot. Lite 3's last OTA spent 19.5 s writing to flash. There have been 0 underruns on either board. The output path held 128 to 139 ms of audio when read: that is the I2S DMA alone when idle, and the output buffer adds to it while a stream plays.
- **Power.** On all five boards the PD contract holds at 20 V (VBUS 19.49 to 20.24 V). The current each charger offers differs, from 1.5 A to 5.0 A (the table has each board). That is the charger, not the board, but a 1.5 A (30 W) contract caps the amp's power on a board that has one. The most amp faults on any board is 0. Some boards read PVDD slightly *above* VBUS, by up to 0.31 V on Hi-Fi Lite 4. That can't be true in hardware, so it is the tolerance of the two voltage dividers and the ADC (about 1 to 2 %). Treat rail readings as about ±0.3 V, or calibrate each board against a meter if a tighter figure matters.
- **Soak (Lite 3, 2026-11-17).** It ran 7.7 h of a planned 12 (232 cycles), until a power cut at 10:56 EST when Lite 3's USB-C plug shifted. Chart: [lite3-soak-2026-11-17.png](lite3-soak-2026-11-17.png). No tone failed, nothing underran, and the board didn't reboot. Free heap fell once, by about 736 B, after the one Wi-Fi drop early on, then stayed flat for 7 h. The lowest-since-boot figure and every stack stayed flat too. That step is the Wi-Fi reconnect leak the security lane is chasing; the next long soak waits for its fix. The link dropped 9 times (a bench restart is left out). Most of those were the board giving up while the bench still held the link. From hifi-receiver #212, each drop logs why.
- **Uptime** differs only because the boards were restarted at different times, so it doesn't mean anything.

## Side by side

| | [Hi-Fi Lite 1](lite-1-2026-11-17.md) | [Hi-Fi Lite 2](lite-2-2026-11-17.md) | [Hi-Fi Lite 3](lite-3-2026-11-17.md) | [Hi-Fi Lite 4](lite-4-2026-11-17.md) | [Hi-Fi Lite 5](lite-5-2026-11-17.md) | spread |
|---|---:|---:|---:|---:|---:|---:|
| self-test | pass | pass | pass | pass | pass |  |
| up (s) | 34 | 180 | 316 | 796 | 77 | 34 to 796 |
| heap free (B) | 13603 | 14067 | 16243 | 15143 | 12875 | 12875 to 16243 |
| heap min (B) | 10563 | 10987 | 13179 | 12095 | 12379 | 10563 to 13179 |
| largest block (B) | – | – | 13312 | 14336 | 11776 | 11776 to 14336 |
| PSRAM free (B) | 1202511 | 1204043 | 1202435 | 1204323 | 1201303 | 1201303 to 1204323 |
| tightest app stack (B) | – | – | 1008 | 716 | 1084 | 716 to 1084 |
| busiest core (%) | – | – | 18.1 | 10.6 | 10.6 | 10.6 to 18.1 |
| RSSI (dBm) | -47 | -44 | -51 | -46 | -47 | -51 to -44 |
| Wi-Fi drops | – | – | 0 | 0 | 0 | 0 to 0 |
| link drops | – | – | 0 | 0 | 0 | 0 to 0 |
| uplink (kbit/s) | – | – | 2140.5 | – | 2187.4 | 2140.5 to 2187.4 |
| boot to link (ms) | – | – | 9287 | 8974 | 9263 | 8974 to 9287 |
| OTA flash write (ms) | – | – | 19482 | – | – |  |
| underruns | – | – | 0 | 0 | 0 | 0 to 0 |
| latency at gather (ms) | – | – | 128 | 139 | 139 | 128 to 139 |
| PD contract (A) | 1.5 | 3.0 | 1.5 | 3.0 | 5.0 | 1.5 to 5.0 |
| VBUS (V) | 20.18 | 20.21 | 19.92 | 19.49 | 20.24 | 19.49 to 20.24 |
| PVDD (V) | 20.13 | 20.26 | 19.81 | 19.8 | 20.25 | 19.8 to 20.26 |
| amp faults | 0 | 0 | 0 | 0 | 0 | 0 to 0 |
| NVS used (%) | – | – | 20.0 | 19.8 | 19.8 | 19.8 to 20.0 |

## Outliers

- Hi-Fi Lite 3: busiest core (%) 18.1 against a median of 10.6 (worse)

## Worrying

- Hi-Fi Lite 1: watch: no `diag` from this image: health from `state` only
- Hi-Fi Lite 1: watch: internal heap fell to 10563 B free since boot
- Hi-Fi Lite 2: watch: no `diag` from this image: health from `state` only
- Hi-Fi Lite 2: watch: internal heap fell to 10987 B free since boot
- Hi-Fi Lite 3: watch: task hifi_power has 1008 B of stack never used (under 1024)
- Hi-Fi Lite 3: watch: internal heap fell to 13179 B free since boot
- Hi-Fi Lite 4: watch: task hifi_power has 1000 B of stack never used (under 1024)
- Hi-Fi Lite 4: watch: task hifi_relay has 716 B of stack never used (under 1024)
- Hi-Fi Lite 4: watch: internal heap fell to 12095 B free since boot
- Hi-Fi Lite 5: watch: internal heap fell to 12379 B free since boot
