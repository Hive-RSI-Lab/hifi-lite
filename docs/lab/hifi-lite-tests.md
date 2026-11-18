# Lane hifi-lite-tests: simulation and testing for the Lite firmware

Owner a4b54. Thread hifi-lite-tests. Asked in 11170143212c (person) through a3f17's 11170146ee6d.

## Done
- **Coverage** (PR #187, merged 2026-11-17). Run `make -C firmware/test coverage` or `fw coverage firmware --src components/hifi/core`. Core is at 99.0% of lines, 83.9% of branches and 204/204 functions.
- **Bring-up regressions on the code the board runs** (`firmware/test/upstream`). This fetches upstream squeezelite-esp32 at the pinned commit, applies `patches/`, and builds the patched functions against stubs. The tests cover:
  - next/prev freezing the controls with nothing playing (0008)
  - a phone dropping mid-song and BT pairing (0007)
  - the Spotify takeover (source checks)
  
  Each test fails on pristine upstream. Set `HIFI_UPSTREAM=<checkout>` to run offline.
- **Fuzzing** (PR #187). There are five libFuzzer harnesses (clang, ASan, UBSan, float-cast-overflow) in `firmware/test/fuzz`: console, API, JSON, the relay's signed envelope and websocket.
  - `make -C firmware/test` runs each for `FUZZ_SECS` (10 s).
  - `make -C firmware/test fuzz-long` runs all five for 10 min through heavy.
  - Radio fuzzers (PR #201, 2026-11-17): `fuzz_espnow` (the espnow-link frame decoder and its sink/pinger counting) and `fuzz_ble` (ble7's GATT write handler and lab_ble's receive) run in the same make target. 10-min runs: about 6.4 M and 7.7 M inputs. Finds fixed, each with a regression seed: a sequence wrap that counted every frame as lost, PONG times out of order or past the cap, and a round-trip sum that could overflow. Not modelled: frames are not authenticated, so a forged MAC or forward sequence jump can still inflate loss counts. The same fixes are in hive-fw's espnow-link and ble-link templates (#66).
  - The 10-min run on 2026-11-17 had no crash: 44M to 489M runs per harness.
  - Bugs found and fixed, each with a regression test:
    - the ota/begin size was cast past size_t
    - the remote id was cast past uint64
    - the capture frames were cast past int
    - NaN/inf got past every range check
    - a remote command was counted as both accepted and rejected

- **QEMU boot and fault scenarios** (hifi #191, hive-fw #62, merged 2026-11-17). `fw qemu --scenario` boots the real Lite image. Faults go in over the gdb stub to QEMU-only shims for the ADC, GPIO and heap. There are six scenarios, all passing: selftest, brownout, pd_loss, i2s_stall, full_flash (NVS packed full) and heap_squeeze.
  - `make -C firmware/test` runs selftest, and `make -C firmware/test qemu-faults` runs all six.
  - Both need `fw build . --qemu` first, run from firmware with HIFI_HIVE_PUBKEY set, through heavy.
  - Logs are in `.fw-src/build-qemu/fw-qemu-NAME.log`.
- **fw module** additions are merged: `fw coverage`, `fw fuzz` and `fw qemu --scenario`.
- **HIL on Lite 5** (2026-11-17). The image was built from h2/lite-bringup 3d7a1cc and sent over Wi-Fi as `hifi-lite-hil`; the self-test gate passed. The suites are `fw hil` with the plan `tools/hil-lite.json`: read, bad (refusals must be 4xx), controls (the next/prev-then-volume freeze regression, then volume and mute are put back) and selftest. The after check fails on a restart, a heap fall of more than 4 KB, or a least-ever-free internal heap under 10331 B, which is Lite 5's 12379 B less 2 KB. 32/32 passed and 17 were skipped because the bench refuses those lines over Wi-Fi (the host fuzzers cover them). No firmware fix was needed. `fw hil` is merged in hive-fw (#63) and installed, so every lane can run it. The bad and controls suites now save the board's state first and put it back (volume, mute, max volume, EQ, sub); with that, 36/36 passed. A run stops when the board goes quiet or offline and clears its held lines. Results are in `reports/hil/lite-5-2026-11-17.json`, `...-fwhil.json` and `...-fwhil2.json`, and the diag report in `reports/lite-5-2026-11-17.md`.

- **State saves read one section** (2026-11-17, hifi-receiver #195). Lite 5's whole `api state` answer was 890-950 characters against the bench's 1000-character line cut. The firmware now answers `api state/<section>` (power, amp, governor, audio, net, sys, ir, controls, update), and the plan's saves and checks read `state/audio`, about 220 characters live. A host test fails if any section can reach 960 characters: it fills every field at its longest, with track text all in characters that need escaping (worst case audio is 959). Track text now stores control characters as spaces, so none can cost six characters. The whole state can't be held under 800 itself, because the track text alone can add 220+ characters, so the guard is on the sections the bench reads. HIL on Lite 5 with the new image: 37/37 passed, 17 skipped as before, in `reports/hil/lite-5-2026-11-17-statepart.json`.

- **Track text capped, guard lowered** (2026-11-17, hifi-receiver #196, merged as 74f0a13). Title is cut at 64 bytes, artist and album at 48, on a UTF-8 boundary. The section guard is now 850 characters, and the worst case for `state/audio` is 832, so a new audio field has room before the bench's 1000-character cut.
- **Nightly HIL on Lite 5** (2026-11-17). Reminder e2f42f wakes this lane at 03:00 EST (08:00 UTC) every night, clear of Lite 3's soak. The woken agent takes the lease, runs `fw hil` with `tools/hil-lite.json`, writes `reports/hil/lite-5-DATE-nightly.json`, releases the lease, and posts one line, or the failure, in the lane thread. A failure or a crossed heap floor shows up within a day. The pre-nightly run on hifi-lite-aaf0483 failed only the heap floor (10319 B against 10331, already low at boot, so not the plan's doing); Lite 5 went to hifi-lite-3cc11f6, which includes #206 (lab and listener tables in PSRAM), and passed 37/37 with the least-ever-free at 16831 B (`reports/hil/lite-5-20261117-1414-prenightly-3cc11f6.json`).
- **`fw hil` for a bench tick** (hive-fw #64, merged as ace1884, installed). `--json` is written whole on every exit but `--list`, including a crash or a stop, and its folder is made. SIGTERM stops sending, clears the bench, writes `exit: 1, stopped: true` naming the case in flight, and exits within about 10 s. `--list` exits 0 on a good plan, 2 on a bad one.
- **Reconnect heap plan** (2026-11-17, hifi-receiver #208 and #214). `tools/hil-wifidrop.json` runs on any lab board: `--suite start` begins 30 Wi-Fi drops on one boot (`wifidrop`, 1500 s cap), then about 15 minutes later `--suite result` needs phase done, 30 drops and heap_ok, which means free internal heap back within 200 B of the start. Before patch 0020 each drop leaked about 730 B; after it, Lite 5 passed 2/2 at -112 B (`reports/hil/lite-5-20261117-wifidrop-result.json`). The board is offline about 10 s per drop, so send it nothing else during the run.
- **Lab plan** (2026-11-17). `tools/hil-lab.json` tests the lab image: info, diag, the lab list and ESP-NOW result, six refusals, then a 100-frame ESP-NOW ping from Lite 1 to Lite 4's echo, with peer counters on both ends. It passed on Lites 1 and 4 (esp32-lab-ble7) in a gap agreed with esp32-lab. 25 and 32 of the 100 pings got no MAC ack into the echo end, while every ping that arrived was answered; that is reported to esp32-lab. Results are `reports/hil/*-20261117-110[89]-lab-*.json`.
- **Self-test loopback right after a flash or cold start** (a943e, 2026-11-18, thread lite-channel-mode; #253, #257 and #259 on h2/lite-bringup, #259 merged as 825dfcc). The first self-test after a flash sometimes failed loopback, and an immediate rerun passed. The fault was the ES8388 capture's first blocks after power-on. The fix: the amp settles before the loopback, the ADC comes up Vref first, the whole I2S receive ring is emptied, and the first 100 ms of fresh ADC input is dropped. Results: 25/25 self-tests on Lite 2, then 25/25 on Lite 3, by the same protocol on each: one flash of the fixed image (Lite 2 through the bench's bring-up tool, Lite 3 over Wi-Fi), a self-test, then 12 reboots with 2 self-tests after each. The reboots were software restarts through the board's API (`api reboot`), not power cuts, so a true cold start was not part of these runs. Every driven-leg block was at 0 dB, the other leg never went above −33 dB, and crosstalk was −49 to −62 dB. Control: the old image on Lite 3 still failed its first self-test after a flash, both at 12:23Z and 12:58Z, so the fault follows the image.
- **Combined update on Lite 1** (a3f17, 2026-11-18, sync 6). hifi-lite-126f47a (main 707baf8, PR #260; #245-#255, #257-#259; not #256) flashed to Lite 1 over Wi-Fi at 13:05Z, fleet e88c716a: 3066928 B, sha256 533fed37..., self-test passed, settings unchanged (vol 20, Boost 0, cap 15 W, sub on -9 dB, stereo, EQ kept). Boot heap 39643 B, was 46287 on the old image; the extra Wi-Fi TX buffers (4 to 6) explain most of it. Watch it over the next days. Post 11180806f361.

## Audio over the loopback taps (a5ba2, lane hifi-lite-characterize)

`tools/audio_measure.py run --dev D --lane L` plays tones through the board and records them with `capture` over the loopback taps: the speaker legs, through a divider, into the ES8388's ADC. It needs a main image at or after 59e738b (PR 219), and a full run takes about 6 minutes. `report reports/raw/audio-D-DATE.jsonl` draws `reports/audio-D-DATE.png` and prints the figures. Host tests: `python3 tools/test_audio_measure.py`.

What the figures are:
- **Response:** each sine at -20 dBFS, dB against 1 kHz. Stereo is measured at 31 points; the other modes at octaves.
- **THD+N:** 1 kHz at -20 dBFS, over 0–24 kHz with no 20 kHz filter, from a sine fit on 128-frame snippets (the bench cuts longer replies).
- **Noise:** silence with the amp on, as mV rms at the speaker leg. The leg is 20.32 V per ADC full scale, a datasheet figure until calibrated. (Noise figures posted before the 2026-11-17 review fix were 1.41 times too low.)
- **Muted side:** in left or right mode, what the muted side reads at 1 kHz.

This is the path's figure, not the amp's: the tap and the 16-bit ADC add their own noise.

| Board | Mode | Response 20 Hz–20 kHz (dB) | THD+N 1 kHz -20 dBFS (%, L / R) | Noise, amp on (mV, L / R) | Muted side (FS) |
|---|---|---|---|---|---|
| **Pass band** | every mode | within ±1.5 | ≤ 2.5 (mono ≤ 5) | ≤ 5 | ≤ 0.001 |
| Lite 2, 2026-11-17 | stereo | -0.49 to +0.95 | 1.45 / 1.08 | 3.9 / 3.6 | – |
| | left | -0.58 to +0.92 | 1.77 / – | 4.2 / 3.4 | 0.0005 |
| | right | -0.52 to +0.96 | – / 1.38 | 4.2 / 3.8 | 0.0006 |
| | mono | -0.46 to +0.92 | 3.14 / 2.60 | 3.9 / 3.5 | – |
| Lite 3, 2026-11-17 | stereo | -0.41 to +1.13 | 1.18 / 1.14 | 3.1 / 4.4 | – |
| | left | -0.54 to +1.01 | 1.31 / – | 3.2 / 4.1 | 0.0005 |
| | right | -0.52 to +0.99 | – / 1.39 | 2.9 / 4.5 | 0.0004 |
| | mono | -0.43 to +0.95 | 2.60 / 2.43 | 3.0 / 4.2 | – |
| Lite 1 | | not measured: went offline mid-run (left mode, 18:57 UTC on 2026-11-17). It came back later, but has since been leased for the party-mode run and playing music, so it was left alone | | | |
| Lite 4, 2026-11-18 | stereo | -0.61 to +1.05 | 1.12 / 1.20 | 3.0 / 2.5 | – |
| | left | -0.52 to +1.00 | 1.31 / – | 2.9 / 2.0 | 0.0005 |
| | right | -0.46 to +0.95 | – / 1.46 | 2.8 / 2.6 | 0.0004 |
| | mono | -0.59 to +0.92 | 2.86 / 2.70 | 2.9 / 2.5 | – |
| Lite 5 | | not measured: its link dropped at the first read (19:14 UTC, offline since 19:04 after a reflash). Its self-test profile also expects no amp supply (pvdd_on fails), so the speaker legs may carry nothing to measure | | | |

Lite 2: pass on every row. Chart: `reports/audio-lite-2-2026-11-17.png`; raw data: `reports/raw/audio-lite-2-2026-11-17.jsonl`.

Lite 3: pass on every row. Chart: `reports/audio-lite-3-2026-11-17.png`; raw data: `reports/raw/audio-lite-3-2026-11-17.jsonl` (mono measured in a second run). Lite 3 ran the a7fcd-party-4 lab image. Its stereo response rises to +1.1 dB at the top, against +0.95 on Lite 2. Its right leg is the noisier one (4.4 mV, against 3.1 mV on the left). With the amp held off, its left leg reads 41 mV (Lite 2: 20 mV), which fits a floating leg. Its diagnostics also show 3 unplanned power cuts. THD+N at 1 kHz: 4.1 / 3.4 % at -30 dBFS, 0.62 / 0.59 % at -12 and 0.49 / 0.47 % at -6. At 3 kHz, -20 dBFS: 1.2 / 1.2 %.

Lite 4: pass on every row. Chart: `reports/audio-lite-4-2026-11-17.png`; raw data: `reports/raw/audio-lite-4-2026-11-17.jsonl` (run at 03:16 UTC on 2026-11-18, after its Bluetooth speaker outing). It is the quietest of the three (2.0–3.0 mV). With the amp held off, its left leg reads 46 mV (Lite 3: 41, Lite 2: 20), so all three float that way. THD+N at 1 kHz: 4.0 / 2.5 % at -30 dBFS, 0.70 / 0.60 % at -12 and 0.51 / 0.45 % at -6. At 3 kHz, -20 dBFS: 1.2 / 1.1 %. Its diagnostics show 2 unplanned power cuts.

Other Lite 2 figures:
- Between 40 Hz and 16 kHz the response is within -0.13 to +0.55 dB.
- Mono plays at -6.0 dB against stereo, as set.
- THD+N at 1 kHz: 3.5 / 4.0 % at -30 dBFS, 0.71 / 0.75 % at -12 and 0.47 / 0.41 % at -6. At 3 kHz, -20 dBFS: 1.4 / 1.3 %.

How the Lite 2 figures read:
- **THD+N falls about as fast as the level rises**, so noise dominates it. With the harmonics taken out, about half of it is left.
- **The muted side reads about 0.0005 FS at any tone level.** So isolation in dB depends on the level used to test: 48–50 dB under a -6 dBFS tone (a943e, thread lite-channel-mode) and 33–35 dB under -20 dBFS (here). The pass band is therefore set on the level itself.
- **With the amp held off, the legs read more, not less** (20 / 12 mV). They probably float, so that reading is not the floor of the measurement.
- **The first capture after a mode change caught the mixers still moving** (DC -0.03 FS, the muted side not yet muted). The tool now throws one capture away after each switch, and `report` drops a response capture with more than 0.01 FS of DC.
- **A capture lost to the link redialling is retried once**, after the mode, the amp state and the tone are set again.
- **The muted-side figure includes that side's noise floor**, so the real crosstalk is at most that.

## In progress
- remote-bringup-tools (ae214) is adding `fw hil --plan` to the bench tick after the self-test gate, on #64's `--json` and SIGTERM. Next: set a heap floor in `tools/hil-lab.json` from a few runs, and run the lab plan on Lites 1 and 4 only in a gap agreed with esp32-lab.

## How to run
On hardware: take the board's lease, then `fw hil --dev D --plan tools/hil-lite.json --lane hifi-lite-tests --json reports/hil/D-DATE.json`.
From `firmware`: `heavy --quick make -C test run FUZZ_SECS=5 HIFI_UPSTREAM=~/.cache/hifi-fw-build/lite-main`.
Fuzzing needs a clang that links libFuzzer: `$CLANG` or `~/.local/opt/LLVM-*/bin/clang`.

## Power loss (USB-C cut or glitch) — a32e7, 2026-11-17

Trigger: the person's Lite 3 rebooted when its USB-C plug shifted (soak cycle 232, `lab/data/soak-2026-11-17T0311.jsonl`; came back `power_on`, NVS and settings intact; the soak tool logged only a tone failure).

**Code audit (firmware/components/hifi).** A cut at any point is safe; nothing needed a fix.
- NVS (settings, name, key slot, `hifi_diag` boots/resets): IDF NVS commits are CRC- and page-state-protected, so a torn write loses at most the last value, never corrupts the store.
- OTA write (`f_open`/`f_write`/`f_commit`): otadata changes only in `esp_ota_set_boot_partition`, after `esp_ota_end` validates. A cut mid-download, mid-write, or before set_boot leaves the old image booting.
- Cut between write and mark-valid (PENDING_VERIFY trial in `hifi_relay.c`): the bootloader rolls back to the old image. Safe, but silent: `hifi_rb_*` keys are written only on the judge's BAD path.
- Minor: `hifi_rb_from`/`why`/`report` are three separate writes; a cut between them is harmless.
- Noted, not changed: `pvdd_fails` in `hifi_power.c` clears only by the 60 s window or `amp_reset`, not on reaching ON.

**Host tests** (`firmware/test/test_power.c`): short PG glitch rides through, long VBUS loss restarts cleanly (amp and PVDD off in the same step, soft start after), repeated glitches never latch, renegotiation dip, PVDD collapsing ahead of VBUS. `test_diag.c`: `power_cuts` counter. `make`: diag 184 checks, power 107, 0 failed.

**Visible.** Firmware counts unplanned `power_on`/`brownout` restarts (NVS `pwr_cuts`, `"power_cuts"` in `api diag`; the first boot and non-power resets don't count; a deliberate unplug counts too). `serialtab diag` shows `FAULT: N unplanned power cuts` in the board's note (test in `test_serialtab.py`). Fleet OTA summaries are unchanged: they carry no health data.

**Ride-through.** See `hifi-order-docs/hardware/LEARNINGS.md`: the buck input holds about 1–3 ms, so any real contact break resets the board.

## Faster Lite (k2410) — adff6, 2026-11-18, thread lite-speed

Measured on Lite 3 (also Lites 2 and 5 for boot) through serialtab's relay; the tower can't reach the boards' LAN, so every on-board number includes the relay hop (about 110–140 ms per round trip). App load was timed in headless Chrome against the app's mock board with a per-call delay (`$TMPDIR/adff6/pageload.py`), at 120 ms (like the relay) and 15 ms (like the home LAN).

| What | Before | After | How measured |
|---|---|---|---|
| App first draw, 120 ms per call | 470 ms | 296 ms | Chrome, mock board, median of runs |
| App first draw, 15 ms per call | 130 ms | 71 ms | same |
| Same, rebased over #247 (its poll re-reads info after a gap) | 378 / 71 ms (#247 alone, 120 / 15 ms) | 254 / 42 ms | same, 5 runs |
| App cached reload (files) | 39.5 KB gzipped resent each load | 304 Not Modified, no body | ETag; checked in QEMU on the real httpd: `/` and `/app.js` answer 304 with `Content-Length: 0` to a matching If-None-Match, and 200 with the body to a wrong tag |
| API GET (info, state, party, diag) | 117–142 ms | unchanged | relay `ask`, 3–5 each |
| Setting change (volume) to saved | one round trip, answer in the same second | unchanged | relay `send`; the reply is the full state |
| Tone start to playing / stop to idle | about 350 ms (312–459) / 640–680 ms | unchanged | `api tone`, state polled |
| Boot: app up / IP / dial-in | 1.3 s / 4.7 s / 9–10 s (35 s on Lite 4) | unchanged | `api diag` after reboot |
| Self-test | on demand (POST), not in boot or any user path | unchanged | code |
| OTA over the relay, 3 MB | 152.8 s on Lite 3 (25.3 s of it flash writing); 137.2 s on Lite 2 | 71.2 s on Lite 3 (26.6 s writing) | relay flash, Lite 3, one chunk at a time vs three in flight |
| Source switch, Bluetooth ⇄ Spotify | refused: the first source kept the output | Spotify over a playing Bluetooth phone: first audio 220–244 ms after its login (the 200 ms fade inside). Bluetooth back over Spotify: connect to streaming 0.8–3.1 s, sink plays it within 0.5 s | patch 0034 lab run, Spotify stand-in on Lite 3, Lite 5 as the Bluetooth phone, 8 cycles (`source-handoff.md`) |

**What changed** (hifi-receiver, branch `h2/lite-speed`):
- The app's start did info, then state, then party one after another (and state twice). It now asks for them together (`Promise.all`) in one batch, so a load costs one round trip instead of three or four. The same batch serves the 2 s poll.
- The relay inbox takes 12 messages (was 4) but at most 24 KB of them, so batched calls from two apps (or an app and an agent) aren't dropped, which cost the caller a 15 s timeout. The heap queued messages can hold goes down from 4 × 16 KB to 24 KB. (Review finding by a3f42.)
- The board's file handler sends an ETag (a hash of the gzipped file, computed per request with no heap) and `Cache-Control: no-cache`; a reload with a matching `If-None-Match` gets a 304 with no body. No heap or task change, so the boot-heap floor and the self-test are as before.

Merged: PR #246 into h2/lite-bringup as 676ed66 (Opus review by a3f42: no blockers; its relay-inbox finding and the 304 type nit fixed and re-reviewed; CI green). The 304 was checked on the real httpd in QEMU (200, then 304 with 0 B and the right type for a matching tag, 200 for a `W/` tag).

Heap: the change adds 64 B at boot (8 more inbox slots of 8 B); the file handler's tag lives on the stack. Read on Lite 3 running 676ed66 (build of c672540, published `adff6-676`), `api diag` at 40 s uptime: free 44419 B, min 43327 B, largest block 43008 B; boot app 1.3 s, IP 4.8 s, dial-in 10.3 s. The self-test's heap check: 44399 B free, 32747 B over its 12000 B floor. Floor kept. (Lite 3's older image at 07:14: free 34091 B, min 26967 B.) That self-test failed only on loopback, twice right after the flash: a943e's known first-self-test-after-boot fault, which only Lite 3 shows (lane lite-channel-mode); every other check passed.

**Relay OTA, several chunks at once** (shared module `modules/dash/remote_bench.py`, `ota_one`): the bench sent one 5.6 KB chunk and waited for its answer before the next, so about 120 s of 150 s was round trips. It now keeps up to three chunks in flight (`OTA_WINDOW = 3`, under the board's 24 KB inbox). Each answer's `next` offset is honoured: if the board refuses or skips, the bench cancels what's in flight and resends from where the board says (or from the last good offset when an answer has none, like the out-of-memory 500). Mock tests cover a lost answer, a refused last chunk and the OOM. Opus review: two rounds, nothing left to fix. Lite 3: 152.8 s → 71.2 s for 3 MB, flash writing unchanged at about 26 s. Two later 3 MB flashes of a7fcd-party6 on Lite 3, one on the first window (08:25Z) and one on the reviewed version live after the 08:26:58Z bench restart (08:29Z), took 72 s each, start of the OTA to written.

**The update a person waits on** (app: check, download, install, restart, back): not timed end to end, because no lab Lite has an update server (`update/check` answers 404 "no update server set (NVS hifi_update_url)"). From the code and the parts we can time:
- check: one HTTPS request, up to its 10 s TLS timeout; the board serves one request at a time, so the app's polls wait behind it.
- download and install: `download()` reads 4 KB from TLS, then writes it to flash, then reads the next, one after the other. Flash writing alone is about 26 s for 3 MB (above), so the download can't be faster than read time plus 26 s. The cut: double-buffer it (a small reader task fills one 4 KB buffer while the other is written), which would hide most of the write time behind the network. Cost: one more 4 KB buffer and a task stack during the update only.
- restart and back in the app: app up at 1.3 s, IP at 4.7 s; the app reconnects on its next poll.
- The app's polls had no time limit, so a slow update/check or upload could hang the page. PR #251 (merged as 13047ad after an Opus review) gives each poll 5 s, treats one slow poll as a busy board and two as offline, stops polling during an upload and times an upload out after 120 s.

Decided: leave Wi-Fi power save on; Bluetooth coexistence needs it.
Decided: keep the relay OTA chunks at 5.6 KB (messages over 16 KB would come out of internal RAM) but send three at once, within the 24 KB inbox (a3f17's ask, superseding the earlier "don't pipeline").
Decided: no update server just for timing (a3f17): the app's own update path is timed on its download from a public third-party image (Tasmota's ESP32 release, 2,234,912 B) with a wrong sha256, so it is refused at the end and nothing boots.
Decided: the URL update reads the next 4 KB while the last one is written to flash (two buffers, a reader task; PR #254, app only). Recovery keeps the one-buffer loop: +48 B there for the hook call, +1,168 B in the app.

**App update download, Lite 5 (lab image, then PR #254's build), same URL and same wrong sha:**

| | Before (one buffer) | After (two buffers, #254) |
|---|---|---|
| Ask to first progress (TLS, size, up-front erase) | about 7 s | about 7 s |
| Download and write rate | about 39 KB/s (3→80% in 44 s) | about 76 KB/s (6→97% in 27 s) |
| Ask to refusal ("sha256 mismatch") | about 64 s | about 37 s |

Untimed by this: the manifest check against a real update server (none is served, by a3f17's call) and the commit and restart after a good image (finish ran here, since the wrong sha was refused at it; boot to the app is known, 1.3 s). The board's split of write time against network wait goes to its serial log, which the Wi-Fi bench can't read.
Decided: leave the dial-in timing (relay task polls every 2 s, then TLS) alone; users don't feel it, since audio and the app are ready at IP. Candidate later: wake the relay task on IP, saving up to 2 s.
Decided: the self-test stays as is; it runs only when asked, so it isn't in the path users wait on.

## User paths and rough edges (k2410) — a05e4, 2026-11-18, thread lite-ux-tests

Goal: every way a person uses a Lite is tested somewhere, and the rough edges are fixed or filed. Three places run the tests:
- **App drives:** `app/tests/test_paths.py` drives the real app.js in headless Chrome against the mock board. The mock follows the firmware's answers and rules. `app/tests/cdp.py` uses the DevTools pipe, so no port is opened.
- **Lab Lites:** the `userpath` suite in `tools/hil-lite.json`, through serialtab. It saves each setting, changes it, checks it and puts it back.
- **Elsewhere:** other lanes' runs, cited in the table below.

**The list and where each runs**

| Path | App drive (mock) | Lab Lite | Notes |
|---|---|---|---|
| First run: join with the right password, a wrong one, one the firmware refuses, no network chosen | yes (4 tests) | no | A remote board isn't joined to a wrong network: it could drop off the Wi-Fi bench (below) |
| Forget Wi-Fi, back to setup | yes | no | same reason |
| Wi-Fi lost and rejoined, lost-receiver card, re-join of the same network | yes (4 tests) | wifidrop plan (30 drops, reconnect 12–25 s, heap held) | see "Reconnect heap plan" above |
| Each source shows what plays, name and volume per source (Spotify, AirPlay, Bluetooth, LMS, line in, DJ direct, idle) | yes | source only: `audio.source`/`input` read | Volume is one value for every source (firmware has no per-source volume), so the per-source check is that a switch keeps it |
| Transport buttons, IR teach and forget | yes | controls suite (next/prev freeze) | |
| DJ direct and back; the line input refused on a Lite | yes | yes (409 for `line`, `stream` ok) | |
| Volume, mute, max-volume cap | yes | yes: the cap clamps 80 to 50, 29 and 101 are refused, a step unmutes, all restored | |
| EQ, channel mode, sub level | yes | yes: set, refusals leave it unchanged, restored | The Lite's sub tops out at −9 dB (caps), and the app says so |
| Rename: good, refused (quote, empty, 33 chars) | yes | yes | a name with a CR is accepted as a letter r: rough edge 1 |
| Update: check, failed check and retry, install and restart, version re-read | yes | OTA from the bench only (`api ota` is USB-only); relay OTA timed in "Faster Lite" | |
| Restart, reset, self-test | yes | self-test suite; no reset on a remote board | |
| Restart and power loss | app: lost card, then found again with the new uptime | "Power loss" section above | |
| Weak charger then a good one; amp fault clear | yes | no (needs a lab PD source) | |
| Party on, lead, off | yes | no: needs two lab Lites together, and the party lanes run it | |
| Spotify | app view only | memory figures from Lite 1 (below) | the rest needs a phone (below) |

**Results**
- **App:** 45/45 pass on PR #247. When the drives were first written, 12 of the 21 path tests failed on the old app.js.
- **Lab Lite 2** (main image, 2026-11-18 07:00 UTC): `read` and `userpath` passed 41/42 on the first run. The one failure was the test's own −6 dB sub level, above the Lite's −9 dB top. After the fix it passed 33/33 with no restart and the heap held. The board's name, volume, EQ, channel and sub are back as they were. Evidence: `evidence/2026-11-18-lite2-userpath.json`.

**Fixed (PR #247, merged as 4bde53d into h2/lite-bringup, app.js, Opus-reviewed by a51bf):**
- The Wi-Fi join said nothing on a real board. It now watches `net` and says "Joined …" or "Could not join …".
- The page waited for the field to lose focus before it updated.
- A refused name still showed "Renamed".
- After an update, the firmware version shown was stale.
- Bluetooth with no track details looked idle.
- A failed update check removed its own button.
- The restart toast showed even when the restart failed.
- A lost receiver showed only a grey dot.
- A throw in the drawing code stopped polling for good.
- There were two cards titled "Input".
- A bug in a page showed as "receiver lost".
- A join hint stayed forever.

**Open rough edges**
1. **A name with a control character is saved mangled** (firmware, `hj_get_str` in `hifi_json.c`):
   - The JSON escapes `\r \b \f` come out as the letters r, b and f, and `\u0001` comes out as `?`. So the control-character check in `r_name` never sees them.
   - Shown on Lite 2: `{"name":"Kitchen\r"}` was accepted and saved as "Kitchenr" (`evidence/2026-11-18-lite2-name-cr.json`, then restored).
   - **Fixed** in PR #250 (squash 51b7b79 on h2/lite-bringup):
     - `hj_get_str` now decodes every escape, with `\uXXXX` as UTF-8 and surrogate pairs.
     - A control character in a name gets 400.
     - `\u0000`, an unknown escape, bad hex and a lone surrogate make the whole body "not a JSON object", wherever they appear.
     - Host tests cover each escape. Opus review by a51bf.
     - Lite 2 HIL on the image a05e4-names-51b7b79 (built from 51b7b79): userpath passed 38/38 with heap held (`evidence/2026-11-18-lite2-userpath-names.json`).
     - An extra escape probe passed 13/13 (`evidence/2026-11-18-lite2-name-escapes-2.json`). It covered `\b \f \u0001 \u001f`, tab and newline, each refused with "name has a control character". The name was kept, and an emoji name (a surrogate pair on the wire) was set, read back and restored.
     - Lite 2 went back to hifi-lite-lab-ps241 afterwards and its self-test passed.
   - Decided: a `\u0000` or bad escape in a name gets the generic "body must be a JSON object" rather than a name message. The whole body is refused, so no handler reads a half-decoded string.
2. **Fixed (2026-11-18, #249 merged into h2/lite-bringup): the end of a Spotify playlist looked like playing.**
   - Before: cspot's CSPOT_STOP was handled nowhere, so the output stayed held by Spotify and the app showed "Playing" with the last title.
   - Patch 0034 now lets the output go and clears the title on a Spotify stop. On the lab bench, 2 of 2 playlist ends let the output go and Bluetooth streamed right after (source-handoff.md). Real cspot with a phone is still to check (below).
3. **No `net.join` in the firmware:** the app infers a join from `net.ssid`. A `net.join` with the state and reason (wrong password, no DHCP) would let the app say why a join failed. This is a firmware follow-up.
4. **Fixed (2026-11-18, #255 merged into h2/lite-bringup as 6051b39): a long string with an escape was cut silently** (firmware, `hj_get_str_span` in `hifi_json.c`, found in a51bf's review of #250):
   - `hj_get_str_span` returns false for any string holding a `\`, so callers fall back to `hj_get_str`. That one cuts at the buffer size without saying so.
   - So a long remote URL, user, password or Wi-Fi field that holds an escape is stored cut short instead of refused.
   - **Fixed** in PR #255:
     - The new `hj_get_str_fit` says whether a value was cut. `hj_get_str` keeps its old behaviour.
     - A cut Wi-Fi ssid or password, remote url, user, password or prefix, or name now gets 400 instead of being stored short. The mock app server refuses the same Wi-Fi fields.
     - Host tests cover each field: every new Wi-Fi and remote case fails on the old code. CI is green and an Opus review found no bugs. Recovery grows by about 84 B, with about 109 KB still free.
     - Lite 2 on a05e4-cut-6051b39 (the merged code): userpath passed 38/38 with heap held (`evidence/2026-11-18-lite2-userpath-cut.json`).
     - A name probe sent as `\u00e9` escapes passed 9/9 (`evidence/2026-11-18-lite2-name-cut.json`): 40 é's and a 70-character escaped name were refused, the name was kept, an exact 32-byte fit of 16 é's was saved and read back, and the name was restored.
     - Lite 2 went back to hifi-lite-lab-ps241 and its self-test passed.
   - Not on a board: the Wi-Fi and remote refusals. The bench won't send those writes over Wi-Fi, because they could strand a board, so the host tests stand for them.
5. **Over Wi-Fi, a body with `\u0000` or a lone surrogate gets no answer** (found on Lite 2 after #250, `evidence/2026-11-18-lite2-name-escapes.json`):
   - The bench's `parse_api` (`modules/dash/remote_bench.py`) accepts these, because Python's JSON reader takes them. The bench then puts them in the signed frame.
   - The board's frame reader (`hifi_remote_handle`) reads `id` with `hj_get_num`, which now refuses the whole line when any string in it is bad. So the id reads as 0, and the board answers "not a v1 command" with id 0. The bench matches no pending call and logs "no answer" after 15 s.
   - The board stays well and nothing is saved. Over the LAN API the same body gets 400 "body must be a JSON object".
   - Fix (bench): have `parse_api` refuse `\u0000` and lone surrogates, the same way it already refuses NaN. That file is shared, so this goes to the bench's owners and isn't in #250.
   - **Fixed in the bench code (a3f17, 2026-11-18):** `parse_api` now refuses any string or key holding `\u0000` or a lone surrogate, with "the body isn't JSON the board reads", so the caller gets an answer at once instead of 15 s of silence. `test_remote_bench.py` covers three bad bodies and one good one (an emoji pair and Café) and passes. It took effect at the bench restart of 2026-11-18 12:32Z, after the morning benches; all five Lites answered a diag after it.
6. **A phone connecting over Bluetooth stalls a Wi-Fi stream** (found by af1e3 on the #256 bench, 2026-11-18, thread lite-source-handoff; numbers in PR #256's body):
   - Each phone connect stalls the LMS stream on the sink for 0–3 s, on the stock image and on #256 alike. That is about the stream's 2 s lead, so a connect during playback can be heard as a break.
   - The stock image also had underrun stretches of 106 s and 42 s over the 60 connects.
   - It matters when a phone connects without taking the output (a phone that auto-connects in range). When it does take the output, the handover stops the Wi-Fi source anyway.
   - **Benched on 126f47a (af1e3, 2026-11-18, `lab/data/lms-connect-re6-*`):** sink Lite 5, sender Lite 2 (the LMS stand-in, raw PCM at 1.4 Mbps), and Lite 3 paging. 10 connects per block.
     - **Cause:** while a phone connects and then streams A2DP, Wi-Fi throughput to the sink can drop below the stream rate. The sender then fell up to 10.5 s behind, and the sink played out its 2 s lead and went silent. So radio sharing triggers it, and the lead running dry is what you hear. It covers the whole connect plus A2DP streaming, not just paging.
     - **How often:** it is intermittent. At a 2 s lead on base, 3 of 58 valid connects lost more than 0.5 s (1.7 s, 5.1 s and 13.3 s). All three were in the first block after a fresh flash. The 13.3 s one ended with the stream dropping and restarting. The other 55 lost 1–239 ms, with no underruns. This morning's long stretches (106 s and 42 s) were all with Lite 3 as the sink.
     - **8 s lead:** 20 connects lost 2–16 ms with no underruns. But no connect in those blocks collapsed the throughput, so the deeper lead is untested against a collapse. By arithmetic, it would cover a 7.5 s shortfall but not the 10.5 s one seen.
     - **#256 (ABBA order, 2 s lead, 20 connects each):** there was no stall on either image, so #256 can't be scored on the stall. It connected faster: median 2.0 s against 2.5 s on base, and max 3.0 s against 4.5 s. Audio lost per connect was the same (median 5 ms). Merged into h2/lite-bringup as 16b8318 for the faster connect (a3f17, 2026-11-18, CI green on the updated branch); it goes to main with sync 7.
     - **Heap:** squeezelite allocates its buffers at start (480 KB stream, 1450 KB output in PSRAM), so a deeper lead costs no internal heap. The sink's free heap was about 21.3 KB after blocks at both 2 s and 8 s.
       - Per block, from the saved start and end readings (af1e3, 2026-11-18): the sink's since-boot low fell from about 20 KB to 10.5–12.2 KB in every block that began after a flash or restart, stalled or not, and did not fall further in later blocks. The lowest was 10,535 B, on base in a block with no stall (ABBA 3), so the low point follows the first connects after boot, not the stall. On #256 it was 12,115 and 12,163 B.
       - The sender's lowest point was 18,183 B (18,047 B in the Lite 3 sink block left out of the score). The pager's end readings weren't saved, so its block low is unknown (12.4–13.2 KB at block starts after its first block).
       - The bench tool now records each board's heap at every connect (see Open).
     - **Lite 3:** it ran without a restart through every block it paged in. A short block with Lite 3 as the sink was left out of the score, because Lite 3 restarted on its own (power_on) at its 4th connect. Before that it connected slowly (4.8–5.6 s).
     - **No firmware PR:** the stall didn't reproduce on demand, and the lever is lead depth. A real LMS playing local files sends as fast as TCP allows and fills 8 s of output, so the exposure is mainly radio and live streams that hold a small lead.
   - Open: find a trigger that reproduces the throughput collapse, then bench an 8 s lead against it.
     - **Trigger to try first: flash, then connect at once.** The collapse came only in the first block after a fresh flash (cycles 0–3 of that block), and the morning's long stretches were also on a freshly flashed sink. What differs then:
       - Wi-Fi rate control starts cold, so its rate table hasn't settled on the link yet.
       - It is the first Bluetooth and A2DP setup since boot. There are no cached SDP records or settled coexistence state, and the first A2DP stream sets up its codec and buffers.
       - The sink's free heap reaches its lowest point in that first block (above), and the pager's sink-clock offset starts cold.
     - **Plan:** flash the sink (or all three boards), start the LMS stream, and begin connects as soon as playback is steady, with a short baseline. Run this at a 2 s lead and at an 8 s lead, in ABBA order, with several fresh-flash blocks per lead and a restart-only block as a control. Use `lab/tools/lms_connect_run.py` with `--pager` and `--lead-ms 2000` / `--lead-ms 8000`.
     - **Score:** whether the 2 s blocks collapse (lost more than 0.5 s, or an underrun) and whether the 8 s blocks ride through the same collapse.
     - Also read the sink's log for the first connects after boot for any allocation failure (ENOMEM, a failed malloc, an A2DP or Wi-Fi buffer refused): its free heap drops to 10.5 KB there, below the self-test's 12 KB floor, even without a stall. A failure there would be a memory finding for the board, not just a bench number (a3f17).
     - It runs when the boards come back from party round 2; a3f17 hands them over.
     - `lms_connect_run.py` now reads each board's diag after every connect, so the data file holds each board's lowest free heap per block (`heap`, `summary.heap_min`). The low since boot is exact when it falls during the block; otherwise the tool uses the lowest polled free heap, and it flags a restart. Host test: `lab/tools/test_lms_connect_heap.py`.

**Spotify: what the bench can and can't test**
- **Host:** the takeover rules are in `firmware/test/upstream` (above). I read cspot's track handling in the patched tree:
  - A gapless track change is loaded at offset 1 and sends no PLAYBACK_START, so the title stays until the new TRACK_INFO.
  - A load or skip wipes the title first, which is right, because the old audio is flushed.
  - No change is needed.
- **App view:** the Spotify card (title, artist, play/pause/next) runs in the drives on the mock.
- **Memory** on Lite 1, which played Spotify for 10 minutes (`evidence/2026-11-18-lite1-spotify-heap.log`):

  | Figure | Measured | Target |
  |---|---|---|
  | Free heap | 38,011–38,495 B (about 13.4 KB before #232) | 30 KB |
  | Largest block | 36,864 B | 16 KB |
  | Boot minimum | 36,591 B | |

  It did not restart.
- **Needs a person's phone and a Spotify login:**
  - Connect discovery and login
  - real playback and the first sound
  - Spotify's volume staying in step both ways
  - a second account taking over
  - the end-of-playlist state (rough edge 2) on a real board

**Join timing (wrong password, slow DHCP).** Not timed on a board. What the code and the drop runs give:
- The Wi-Fi manager retries a failed connect twice (`CONFIG_WIFI_MANAGER_MAX_RETRY=2`), then polls slowly. The setup AP is held back for 10 minutes after a loss.
- Reconnects in the wifidrop runs took 12–25 s.
- The app waits 45 s before it says "Could not join", which covers both.

Decided: no wrong-password or slow-DHCP join on a remote Lite. A board that fails to join leaves the Wi-Fi bench, and only a person can bring it back. Time it on a local bench with a USB console.
Decided: no firmware change for Spotify titles on gapless changes. Gapless changes keep the title already.
Decided: the CR-in-name bug is filed, not fixed in this lane. The app can't send one, and the fix sits in the shared JSON reader, which every route uses.
Decided: the `userpath` suite stays in `tools/hil-lite.json`, and every write is put back. The CR probe isn't in the plan: it fails until the firmware is fixed and would turn the nightly red.
