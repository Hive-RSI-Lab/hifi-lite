# ESP-NOW between two Hi-Fi Lites, alongside Wi-Fi and Bluetooth

Lane esp32-lab (a4b0e), 2026-11-17. Boards: Lite 1 (A, lite-1) sends; Lite 2 (B, lite-2) echoes, and
Lite 4 (B, lite-4) for the last four flood runs (marked L4) while Lite 2 is offline.
Both run the lab image (`board/lite-lab`, the Lite image plus the lab frame) and stay connected to the home Wi-Fi
as stations, with Bluetooth (A2DP sink) on, as in the product. The experiment is `esp/lab_espnow.c`; the
board-free counting (sequence numbers, loss, round trips, clock offset) is `core/hifi_link.c`, host-tested.

## Method

- Each run is one `lab/tools/lab_run.py espnow ...` call. It starts B's echo, then A's role, both with a deadline
  (`max_s`) a little past the run, waits, and saves both boards' `result` to `lab/data/espnow-<tag>-<time>.json`.
  `lab/tools/lab_table.py` prints the table below from those files.
- **ping**: A sends 64-byte frames at a fixed rate (20 or 100 Hz) at 1 Mbps; B answers each. Loss is counted at
  A from sequence numbers; RTT is send-to-reply on A's clock. Each reply also carries B's clock, which gives
  an offset and a drift estimate (NTP-style, best of each 1 s window).
- **flood**: A sends 250-byte frames as fast as the send callbacks allow, with at most `window` (4) frames in
  flight; B counts what arrives. Loss and throughput are what B received.
- `ps` is the station's power save during the run (restored afterwards). `audio` means both boards were playing a
  silent tone the whole run (the full I2S/codec path at zero amplitude: the CPU, DMA and interrupt load of
  playing, without sound).
- A frame counts as `nacked` when the ESP-NOW send callback reports failure after the MAC's own retries.

## Results

| run | ps | rate | audio | load | frames | loss | nacked A/B | RTT p50/p95/p99 ms, or throughput | drift ppm | min internal heap |
|---|---|---|---|---|---|---|---|---|---|---|
| ping p20 | min | 1M | no | 20 Hz | 400 | 9.25% | 36/2 | 3.73 / 17.8 / 27.7 | +23.6 | 12919 |
| ping p20 psmax | max | 1M | no | 20 Hz | 400 | 5.75% | 23/1 | 3.68 / 22.3 / 32.1 | −2.0 | 12775 |
| ping p20 audio | min | 1M | yes | 20 Hz | 400 | 2.75% | 11/0 | 4.22 / 15.8 / 27.6 | −2.3 | 12735 |
| ping p100 | min | 1M | no | 100 Hz | 2000 | 4.35% | 78/9 | 3.69 / 32.0 / 60.4 | −2.9 | 12775 |
| ping p100 audio | min | 1M | yes | 100 Hz | 2000 | 2.55% | 49/5 | 4.06 / 29.6 / 75.8 | −0.8 | 12571 |
| flood f1m | min | 1M | no | w4 | 741 | 39.1% | — | 95 kbps | | 12543 |
| flood f24m | min | 24M | no | w4 | 3093 | 33.9% | 1604 sent-side | 403 kbps received (598 sent) | | 12407 |
| flood f54m | min | 54M | no | w4 | 2291 | 41.9% | — | 300 kbps | | 12407 |
| flood f54m audio (L4) | min | 54M | yes | w4 | 2413 | 29.8% | 1032 sent-side | 312 kbps (417 sent) | | 14843 |
| flood f54m w1 (L4) | min | 54M | no | w1 | 1138 | 45.9% | 973 sent-side | 147 kbps (255 sent) | | 14723 |
| flood f54m w16 (L4) | min | 54M | no | w16 | 3816 | 21.9% | 1066 sent-side | 466 kbps (595 sent) | | 14655 |
| flood f24m psmax (L4) | max | 24M | no | w4 | 2034 | 34.4% | 1073 sent-side | 263 kbps (377 sent) | | 14723 |

The Lite 2 runs were on channel 11, the L4 runs on channel 6: the home access point had moved channel in
between. The L4 runs used the lab10 image (the same experiment, with more internal RAM left: finding 7). Because
the board and the channel both changed, compare the L4 rows with each other, not one by one with the Lite 2
rows.

### Bluetooth or modem sleep? (L4, lab11b)

The same flood each time: 15 s, 54M, window 16, 250-byte frames, Lite 1 → Lite 4, channel 6, no audio. Only
the echo end (Lite 4) changes. The lab's `bt` experiment (`esp/lab_bt.c`, USB and Hive only like the rest of the
lab) takes Bluetooth out of the way at runtime and puts it back on stop: `off` disables Bluedroid and then the
controller (`esp_bluedroid_disable`, `esp_bt_controller_disable`); `quiet` keeps Bluetooth up, turns page and
inquiry scans off (not connectable, not discoverable) and sets `esp_coex_preference_set(ESP_COEX_PREFER_WIFI)`.
Lite 1 kept Bluetooth on and modem sleep `min` throughout. Each set-up ran twice, in the order a, b, c, d, then
again. Files: `lab/data/espnow-flood-f54m-w16-bt*.json`.

| set-up (Lite 4) | Bluetooth | ps | received (run 1, run 2) | lost | nacked at A | throughput |
|---|---|---|---|---|---|---|
| (a) as shipped | on | min | 2789, 3331 | 29.2%, 25.2% | 1162, 1133 | 361, 431 kbps |
| (b) BT off | off | min | 2599, 2227 | 30.4%, 35.1% | 1148, 1205 | 336, 288 kbps |
| (c) BT off, radio always on | off | none | 28180, 27690 | **0.0%, 0.0%** | **0, 0** | **3634, 3580 kbps** |
| (d) BT quiet + prefer Wi-Fi | on, no scans | min | 2665, 3691 | 30.9%, 22.9% | 1193, 1101 | 345, 474 kbps |

Every stop brought Bluetooth back (controller and Bluedroid enabled; the A2DP sink needs a board restart after
`off`, which both boards had). Lowest internal heap in any of these runs: 14315 B.

**Reading: it is the receiver's modem sleep, not Bluetooth's slots.** Turning Bluetooth off alone (b) changed
nothing: about 1150 frames nacked in 15 s, as with it on. Turning the receiver's modem sleep off (c) took the loss
to zero in both runs and the throughput up about nine times, to 3.6 Mbit/s, with the sender still in modem sleep
and still running Bluetooth. Keeping Bluetooth quiet and preferring Wi-Fi in the coexistence (d) didn't help
either, so it isn't a fix for a product image with nothing paired. The fixed loss per second of finding 2 is the
time the receiver's radio is asleep between beacons. Why `ps max` lost no more than `ps min` wasn't measured;
with this access point's beacon timing they probably sleep for much the same share of the time.

The catch: the receiver can only turn modem sleep off with the Bluetooth controller disabled (finding 3). A board
that must receive ESP-NOW at full rate has to give up Bluetooth while it does; one that keeps Bluetooth on must
live with about a quarter to a third of frames lost under load and retry them.

### Lossless with Bluetooth still on? (Lite 1 → Lite 4, lab12)

The same flood again (54M, window 16, 250-byte frames, channel 6, no audio), with Bluetooth on and modem sleep
`min` on both boards unless the row says otherwise. Lite 4's Bluetooth was up and connectable, but nothing was
streaming to it. The new knobs are in `esp/lab_espnow.c`: `retry`, `retry_ms`, `sync_us`/`win_us`/`off_us`,
`beacon` and `li` (see the comment at its top). The access point beacons every 100 TU (102.4 ms) with DTIM 1, and
its DTIM falls at TSF phase 0. Lite 4's default `listen_interval` is 3. "Airtime" counts the frames sent for
each frame delivered, so the base flood already wastes some airtime on frames the receiver slept through.
Most rows ran for 15 s. The rows marked 30 s were run again later for 30 s, to check that the best rows hold up.
Files: `lab/data/espnow-flood-btonA-*.json`, `espnow-flood-btonB-*.json` (the 30 s runs) and
`espnow-flood-btoffwin-*.json`.

| method | lost | Mbit/s delivered | what it costs |
|---|---|---|---|
| none (base) | 20.6%; 25.9% (30 s) | 0.68; 0.49 | airtime 1.26–1.35× |
| retry 1 | 6.1% | 0.88 | airtime 1.18× |
| retry 2 | 5.8% | 0.47 | airtime 1.39× |
| retry 4 | 1.3% | 0.50 | airtime 1.34× |
| retry 4, 20 ms apart | 3.0% | 0.34 | airtime 1.53× |
| retry 6 (30 s) | 1.1% | 0.39 | airtime 1.47× |
| **retry 8** | **0.09%; 0.44%, 0.31% (30 s)** | **0.45; 0.40, 0.45** | airtime 1.38–1.44×; 396–995 frames per run arrive out of order |
| **retry 8, 50 ms apart** | **0.38%; 0.46% (30 s)** | **0.54; 0.40** | airtime 1.30–1.46×; 522–1130 out of order, a retried frame ≥ 50 ms late |
| bursts at the DTIM wake, window 5 / 10 / 20 / 40 ms | 80.8 / 76.3 / 53.9 / 54.6% | 0.01 / 0.04 / 0.14 / 0.14 | airtime 2.2–5.3×; the 5 and 10 ms windows are at or below the 10 ms tick, so they weren't kept reliably (the lab now refuses under 10 ms) |
| burst window 20 ms, 50 ms after the DTIM | 54.1% | 0.13 | airtime 2.2× |
| burst window 10 ms at the DTIM + retry 4 | 22.2% | 0.05 | airtime 3.5× |
| receiver ps max, listen_interval 1 / 3 / 10 | 32.2 / 34.0 / 29.3% | 0.38 / 0.36 / 0.45 | worse than ps min |
| Bluetooth controller off on the receiver for the run, ps none | **0.0%** | **4.06** | 609 ms to turn it off, 48 ms to turn it on; the A2DP sink is gone until the board restarts, so a connected phone drops |

**Reading.**
- **Retrying is the fix that keeps Bluetooth on.** If the sender resends each frame that wasn't acknowledged, up to 8 times, the loss is under 1% in all five runs: three resending at once (0.09–0.44%) and two 50 ms apart (0.38–0.46%). The cost is 1.30–1.46× the airtime and about 0.4–0.5 Mbit/s delivered, about a ninth of the 3.6–4.1 Mbit/s that Bluetooth off gives. A retried frame arrives out of order, so the receiver needs a reorder buffer by sequence number. Four retries (1.3%) and six (1.1%) are not enough. Spacing the retries 50 ms apart works as well as resending at once, but it adds latency.
- **Timing the sender to the DTIM wake makes it worse.** A likely reason, not tested: the receiver's radio does not stay awake for a fixed time after each beacon but while traffic keeps it busy, so a steady flood keeps it awake most of the time and bursts let it fall asleep between them. The 20 ms window lost as much 50 ms after the DTIM as right on it (54%), so the timing doesn't matter. The TSF is the access point's clock, read on both boards, and the TBTT phase read from the beacon was 0.
- **listen_interval doesn't help.** `ps max` lost 29–34% at listen_interval 1, 3 and 10, more than `ps min` lost the same morning. The value set reads back, but the access point was not reassociated, so the AP side may still use 3. The ps min/max comparison in the section above rested on runs from another day.
- **Short controller-off windows work and cost Bluetooth.** With the controller off and modem sleep off, the loss is 0 at 4 Mbit/s. A window costs about 0.65 s to switch (the 400 ms Bluedroid wait makes up most of it), and it costs A2DP: the sink isn't set up again until a restart. In this firmware a window always drops a connected phone, however short the window is. So it is a mode switch (an ESP-NOW-only mode), not something to slip between audio frames.

Not measured here: whether retries still deliver at a higher offered load. The A2DP stream's own dropouts beside
them are in the next section.

### Radios side by side with real audio (Lite 1 → Lite 4, a2dp2c)

The question the table above left open: does a live A2DP stream drop out when ESP-NOW or BLE shares the radio?
Lite 1 is an A2DP source (`esp/lab_a2dp.c`, role source: it takes its own sink down and streams a 441 Hz tone,
44.1 kHz stereo SBC) and Lite 4's sink plays it (role sink: it counts packet gaps, bytes its output buffer
dropped or discarded, and the output's dry spells). `lab/tools/a2dp_run.py` rebooted the sink first in these
runs (see the bond note below; that is now opt-in with `--reboot`), starts the load before the source connects (`--order load-first`) or once the link is up
(`--order stream-first`), resets the sink's counters and reads them 30 s later. Both boards stay on the home
Wi-Fi with ps min. The image is a Classic-only lab image with lab_a2dp (`esp32-lab-a2dp2c`), because the
dual-mode BLE image ran out of internal RAM (the ble row).

| load beside the stream | runs | what happened | sink in 30 s | lowest internal heap |
|---|---|---|---|---|
| **none** | 2 | link up in 1.5 s (ctl2) | 2069–2074 packets, 1411–1412 kbit/s; **0 dropped, 0 discarded, 0 dry spells**; longest gap 76–80 ms, 41–42 gaps over 50 ms, none over 100 ms | sink 7.6–7.9 KB, source 7.8–8.3 KB |
| **ESP-NOW first** (flood ×8 retry, or 50 Hz ping) | 2 clean (5 connects) | **the A2DP connect fails every time**: "connect failed, reason 0" (a page timeout) after 7.6 s | nothing streamed | — |
| **stream first, then ESP-NOW** | 1 | the link came up in 0.9 s, the load at 1.5 s, then the lab refused the ESP-NOW start: "too little free RAM" (8.9 KB free on the source, the floor is 10 KB) | as alone: 2070 packets, 1408 kbit/s, 0 dropped, 0 dry, 47 gaps over 50 ms | source 7.8 KB |
| **BLE link** | 1 (dual mode, no BLE load yet) | **doesn't fit**: on the dual-mode image the stream alone took both boards under the lab's 7 KB stop floor in 2.5–3.5 s (sink min 4.5 KB, source 4.8 KB) | 24 packets before the stop | sink 4.5 KB, source 4.8 KB |
| **both** | not run | doesn't fit (the ble row, plus ESP-NOW blocking the connect) | | |

**Reading.**
- **A stream alone is clean.** No dropped or discarded bytes and no dry output in 90 s of tone over three runs (the stream-first run ended up alone too).
  The packets come in bursts: the longest gap was 80 ms, with about 45 gaps a run over 50 ms and none over
  100 ms, which the sink's buffer covers. The only dry spells were one or two of 46–49 ms in the first second,
  while the output started, before the counting window.
- **ESP-NOW receive blocks the A2DP connect.** With the ESP-NOW echo up on the sink board, every connect failed
  at the page stage, in both clean runs (sink rebooted first) and with both a flood and a light 50 Hz ping. The
  control straight before (sink rebooted, no load) connected first time in 1.5 s. This isn't in the IDF's
  coexistence table (`docs/en/api-guides/coexist.rst` in IDF 5.5; the boards run 4.4), which rates ESP-NOW
  receive "S" against every BR/EDR state: stable in STA mode, which both boards were, not supported otherwise. Whether the ESP-NOW echo with no traffic at all blocks it
  too wasn't tested.
- **Once a stream is up, there is no RAM to start ESP-NOW.** An A2DP link holds about 6.3 KB of internal RAM on
  each board: the sink goes from about 15.3 KB to 9.1 KB, the source to 8.9 KB, with lows of 7.6–7.9 KB. The
  lab frame starts nothing under 10 KB (`HIFI_LAB_HEAP_FLOOR`) and stops a run under 7 KB. ESP-NOW itself needs
  only about 0.3 KB (finding 7), so the floor, not ESP-NOW, is what refuses it; but the margin left (about
  1.5 KB above the stop floor at the lows) is too thin to try it in the lab without lowering a safety floor,
  and that wasn't done.
- **BLE and A2DP don't fit together on the current dual-mode image.** It boots at about 12 KB once lab_a2dp is
  in (ble7 had 15.2 KB without it). A BLE link takes about 3.3 KB (ble.md) and an A2DP link about 6.3 KB: 9.5 KB
  together, leaving about 2.5 KB. The measured run hit the stop floor with the stream alone, before any BLE.
  Finding room is a RAM job (moving lab and stack statics to PSRAM, or a product image without the lab), not a
  radio one, so the dropout question for BLE stays open.
- **What can share the radio with a live stream:** Wi-Fi as a station (every run here). Not ESP-NOW
  receive while the stream must connect, and not ESP-NOW at all unless about 16 KB of internal RAM is free
  before the stream (the radio image below has that, and answers the dropout question). Not BLE on a dual-mode image unless it has about 9.5 KB free beyond its own boot needs (steady; about 11 KB above its run floor with the dips).
  A product that wants ESP-NOW beside A2DP must keep ESP-NOW off while a phone pages in, or accept that a
  phone can't connect while it runs.

**A sink bug the runs found.** After a source run, Lite 4 refused that source's next connect ("connect failed,
reason 0", 7.6 s) until Lite 4 rebooted. The source deletes its bond when it stops (it gives its own sink
back); the sink keeps the bond it made. A sink-only reboot cleared it every time. The same may hit the product:
a phone that has forgotten the Lite may not connect again until the Lite restarts. That isn't proven. The lab
fix is for lab_a2dp's sink role to forget the bond of whatever connected during the run. That is now in
(hifi-receiver #216): at every stop the sink calls `esp_bt_gap_remove_bond_device` for its source, once that
source has no ACL link up, and reports `forgot`. Thirteen runs since, back to back with no sink reboot, all
connected on the first try in 0.64–3.5 s, so `a2dp_run.py` no longer reboots the sink (`--reboot` still does).
The earliest ESP-NOW runs (r1–r3) were before the reboot was added, so only the two clean runs count above.

Data: `lab/data/a2dp-*.json` (none: a2dp2c, ctl2; espnow: r4, ping; stream first: sf; dual mode: a2dp1).

### With room for a second radio (Lite 1 → Lite 4, radio4 and radio6)

The rows above stopped at RAM, so the next images made room. The radio image (`board/lite-lab-radio`: the BLE
lab image, dual mode, with lab_a2dp) moves squeezelite's decode and stream thread stacks to PSRAM
(`patches/0019`, `CONFIG_HIFI_LAB_PSRAM_STACKS`, lab images only). They were pthreads, and on IDF 4.4 a pthread's
stack is internal RAM: 14.5 KB and 4.3 KB. The PSRAM switches the IDF offers for Bluetooth and Wi-Fi
(`CONFIG_BT_ALLOCATION_FROM_SPIRAM_FIRST`, `CONFIG_BT_BLE_DYNAMIC_ENV_MEMORY`, `CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP`)
were already on in the Lite's base config, and the largest named static in internal RAM is 2 KB, so the stacks
were the win. With the stream up, free internal heap went from under 5 KB (this dual-mode image before the change) to about 25 KB, about 21 KB with a
BLE link up too. Every run here is stream first (the link up, then the load), 30 s, with no sink reboot between
runs. The longest gap is over the whole stream, its start included; the other counts are over the 30 s window.

| load beside the stream | runs | sink in 30 s | the second radio | free internal heap at the end, sink / source |
|---|---|---|---|---|
| **none** | 4 (r4bond1-2, r6bond1-2) | **0 dropped, 0 discarded, 0 dry spells**; longest gap 76 ms, 39–50 gaps over 50 ms, none over 100 ms | | 24.7–24.9 / 25.2–25.5 KB |
| **ESP-NOW flood**, retry ×8 (250 B, window 16, 54M) | 2 (r4esp1-2) | **0 dropped, 0 discarded**; longest gap 80–81 ms, **217–222 gaps over 50 ms**, none over 100 ms; **one dry spell of 50 ms** in r4esp2, none in r4esp1 | 448–458 kbit/s sent (retries included), about 18% of frames failed a first try, 329–340 kbit/s received | 24.2–24.3 / 24.9–25.1 KB |
| **BLE ping**, 20 Hz | 2 (r4ble1-2) | **0 dropped, 0 dry spells**; longest gap 75–76 ms, 24–25 gaps over 50 ms | 1800 of 1800 pings answered | 21.1 / 21.2 KB |
| **both** | 1 (r6both1) | **0 dropped, 0 dry spells**; longest gap 82 ms, 142 gaps over 50 ms | ESP-NOW 373 kbit/s sent, 273 received (0.07% lost); BLE 1800 of 1800, link up in 1.2 s | 21.5 / 21.1 KB |

**Reading.**
- **BLE beside a stream costs nothing measurable.** The pings all came back and the stream's gaps were no worse
  than with no load (if anything fewer over 50 ms). The controller schedules BLE's connection events around the
  A2DP link.
- **An ESP-NOW flood with retries costs a little.** No audio data was lost, but the A2DP packets came in longer
  bursts: five times as many gaps over 50 ms, still none over 100 ms, and one 50 ms underrun in 60 s of
  streaming, which is audible as a click. A flood is the worst case (26% of all sends were NACKed and retried);
  control traffic at ping rates should be far lighter, but that wasn't run beside a stream. With BLE added the
  flood got less through (273 against 330–340 kbit/s) and the stream had no underrun in its one run.
- **ESP-NOW receive still has to wait for the connect.** None of these runs started ESP-NOW first; the
  connect failure in the table above is a radio effect, not RAM, and these images don't change it.
- **The RAM holds.** The lowest free internal heap in any run was 20.6 KB (the source, with both loads), far
  above the lab's floors. A BLE link takes about 3.5 KB of it, ESP-NOW about 0.6 KB.

Data: `lab/data/a2dp-*-r4*.json`, `a2dp-*-r6*.json`, and the second radio's results in
`espnow-flood-a2dp-*` and `ble-ping-a2dp-*` with the same tags.

### The control plane beside a live stream, for party mode (Lite 2 → Lite 3, 2026-11-17)

Party mode needs a control channel between boards while one of them streams A2DP: a clock or sync beacon, not
a flood. So this rung ran ESP-NOW at control rates beside a live stream, BLE started before the A2DP connect, and
high-rate FLAC with the decoder stack in PSRAM. Boards: Lite 2 (`lite-2`) is the A2DP source and the
ESP-NOW/BLE pinger, Lite 3 (`lite-3`) the sink and the echo, both on the radio image (lite-lab-radio).
ESP-NOW is at 1 Mbps with modem sleep `min` (the only mode Bluetooth allows, finding 3), 64-byte frames. Each run
starts the stream, then the beacon, and pings for 135 s. The sink counts cover 30 s of that.

**One-way latency.** The pinger stamps each PING (t1). The echo stamps its arrival (t2) and the PONG's send (t3).
The pinger stamps the PONG back (t4). The firmware fits the echo's clock to the pinger's along a drift line over
the run, then gives the forward (t2 − t1) and back (t4 − t3) delays. Like any two-way clock sync, it takes the
quickest trips to be the same both ways, so the spread is measured and the level rests on that. "Jitter p99" is
p99 − p50. The RTT histogram stops at 100 ms, so an RTT p99 of ">100" means past the cap; the one-way figures
have no cap.

| beacon | runs | sent | lost | nacked: pinger / echo | forward p50 / p99 ms | forward jitter p99 | back p50 / p99 ms | RTT p50 / p99 ms | sink, 30 s: dropped, dry spells, gaps > 50 / > 100 ms | A2DP connect s |
|---|---|---|---|---|---|---|---|---|---|---|
| none | rcnone1, rcnone2 | | | | | | | | 0, 0 (one of 14 ms under 20 in rcnone1), 45 and 38 / 0 | 2.5, 1.0 |
| **5 Hz** | rc5b, rc5c | 675 each | **41.0%, 40.3%** | 56 / 221, 43 / 231 | **30.7 / 80.6, 31.0 / 81.4** | 49.9, 50.4 | 19.4 / 117.7, 21.5 / 117.4 | 70.8, 77.8 / >100 | 0, **one of 46 ms** in rc5b, 90–106 / 0 | 1.3, 1.2 |
| 10 Hz | rc10 | 1350 | 28.0% | 116 / 263 | 27.2 / 92.0 | 64.7 | 8.5 / 104.5 | 43.0 / >100 | 0, 0, 221 / 0 | 1.6 |
| **20 Hz** | rc20 | 2700 | **3.5%** | 51 / 46 | **18.3 / 67.3** | 49.0 | 3.0 / 66.4 | 25.6 / 91.4 | 0, 0, 239 / 0 | 1.2 |
| 50 Hz | rc50 | 6750 | **0.7%** | 46 / 2 | 20.6 / 64.9 | 44.4 | 4.2 / 67.2 | 30.5 / 73.8 | 0, 0, 237 / 0 | 1.5 |
| 20 Hz, 128 B | rc20l128 | 2700 | 2.6% | 24 / 50 | 20.0 / 68.2 | 48.3 | 3.8 / 69.7 | 27.6 / 80.7 | 0, 0, 239 / 0 | 1.8 |
| 5 Hz, **no stream** | rcbase5 (60 s) | 300 | **57.0%** | 33 / 138 | 28.6 / 87.1 | 58.4 | 12.9 / 109.2 | 51.7 / >100 | no stream | - |

A 128-byte beacon at 20 Hz behaves like the 64-byte one, so the frame size doesn't matter at control sizes. A 32-byte run
was refused: the lab's ping needs at least 46 bytes for its timestamps.

A first 5 Hz run (rc5a) was cut at 162 pings by a bench restart. It read the same: 37.7% lost, forward 28.2 / 90.5 ms,
and two dry spells (92 ms). The longest gap in the stream was 76–81 ms in every run, beacon or not.

**BLE started before the A2DP connect** (rcblefirst1-3: the BLE link up first, then the A2DP connect, BLE ping
at 20 Hz, 64 B): **it doesn't change the connect.** All three connected on the first try, in 1.1, 2.7 and 1.2 s,
inside today's spread for a connect with no load (1.2–2.5 s, rcnone1 and the stream-first runs above). The BLE
link came up in 0.6–0.9 s and all 2700 pings came back in each run, RTT p50 29–32 ms, p99 61–64 ms (the BLE
connection interval is 15 ms). The two sinks with counts had 0 dropped, 0 dry spells, longest gap 66 ms, 25 and 34
gaps over 50 ms. In rcblefirst3 the source stopped answering the stop at the end of the run, so its a2dp file
wasn't written. Its BLE file was. Free internal heap with BLE up: 17.5 KB source, 18.2–19.3 KB sink, against
21.0–21.2 KB with ESP-NOW.

**High-rate FLAC with the decoder stack in PSRAM** (`flac_run.py`, a 96 kHz/24-bit stereo clip decoded in a loop on
Lite 2, the A2DP source, core 1; the decoder's 12 KB stack in PSRAM or internal RAM):

| run | decoder stack | A2DP | speed (× real time) | CPU % | worst frame ms | late frames | free internal heap, min | stack free min |
|---|---|---|---|---|---|---|---|---|
| alone | internal | no | 2.04–2.08 | about 49 | - | 0 | 16.5 KB | - |
| alone | PSRAM | no | 1.99 | 51 | - | 0 | 29.1 KB | 7608 B |
| rcbeside (stream didn't start) | PSRAM | no | 1.95 | 51.9 | 47 | 0 | 25.9 KB | 7596 B |
| **rcbeside2** | **PSRAM** | **yes, 1411 kbit/s** | **1.85** | **54.8** | 59 | **0** | **19.4 KB** | 7596 B |
| rcbeside2 | internal | yes | **refused**: a 12 KB internal stack would leave under 10 KB (20.6 KB free with the stream up) | | | | | |

The decoder stack in PSRAM costs about 5% speed alone and 10% beside a live stream, and saves 12 KB of internal RAM.
That is the only way it fits beside A2DP: with the stream up the board has 20.6 KB free, and the lab guard refuses
an internal 12 KB stack. Beside the stream, the decoder kept up with no late frame and used 55% of one core, and the
stream didn't notice: 0 dropped, 0 dry spells, longest gap 80 ms, 92 gaps over 50 ms in 61 s, against 81 in the same
61 s with no decoder (the refused run, which streamed anyway). The source's A2DP feed never waited over 29 ms. The
lowest free heap with stream and decoder both up was 19.4 KB on the source (17.4 KB low-water over the whole run),
well above the 12 KB self-test floor. In `flac-internal-rcbeside2-*.json` the `result` is the previous run's,
because the start was refused; `start` holds the refusal.

**Reading.**
- **The loss is the receivers' modem sleep, and slow beacons get the worst of it.** At 5 Hz 40% of round trips
  fail, at 10 Hz 28%, at 20 Hz 3.5%, at 50 Hz 0.7%. At low rates most failures are on the way back
  (echo → pinger: 221–263 nacked against 43–116). That fits finding 2: a station in modem sleep `min` sleeps
  between beacons, and frames sent while it sleeps go unacknowledged. Steady traffic keeps both radios awake
  more of the time. Each lost beacon is a whole lost beacon, because ESP-NOW has no retry beyond the MAC's.
  The stream isn't the cause: the same 5 Hz beacon with no stream (rcbase5) lost more, 57%.
- **The delay is tens of ms, with a long tail.** A beacon that lands takes 18–31 ms at the median and 65–92 ms at
  p99. Most of that is modem sleep too, not the stream: at 5 Hz with no stream the median is 28.6 ms forward and the
  RTT 52 ms, the same as with the stream. The 3.7 ms RTT in finding 4 was with the radio awake. Beside the stream,
  Bluetooth's slots add a little on top. This is fine for a clock beacon that carries its own send time, and for "play at T".
  It is not fine for anything that trusts arrival time.
- **The stream doesn't notice a beacon.** No run lost audio, and no run at 10 Hz and up had a dry spell. The A2DP
  packets come in longer bursts: 90–239 gaps over 50 ms in 30 s, against 45 with no load, never one over 100 ms.
  The one 46 ms dry spell came at 5 Hz, where the beacon is lightest, so it isn't the beacon's load.
- **BLE is the cleaner control radio beside A2DP**: no loss at 20 Hz, a steadier RTT, and the controller
  schedules it around the stream. The cost is 3.5 KB more internal heap.

Data: `lab/data/a2dp-*-rc*.json` (the stream's counts), `espnow-ping-a2dp-espnow-rc*.json` and
`ble-ping-a2dp-ble-rcblefirst*.json` (the beacon's), `flac-*-rcbeside*.json`.

## Findings

1. **The channel is the access point's.** A station that is connected can only use ESP-NOW on the channel its
   AP is on (`esp_now_peer_info.channel = 0`, "the current one"). Two boards on the same home Wi-Fi meet there
   for free; boards on different APs/channels can't talk without one of them leaving its AP. Any design that
   needs ESP-NOW between rooms has to assume one AP channel, or a board that roams with its peer.

2. **The losses are frames the receiver never heard, because its radio was in modem sleep (proven in the
   lab11b runs above).** Every
   lost frame is a MAC-level failure: in ping p100, A's 78 nacks plus B's 9 are exactly the 87 lost; in flood f24m,
   1604 nacked at A against 1585 missing at B. Nothing was dropped inside the boards (`queue_full`, `no_mem`,
   `send_fail` all 0). With the Bluetooth controller on, the station must stay in modem sleep, so its radio is off
   between beacons and frames sent then go unacknowledged. The lab11b runs isolated it: with the receiver's
   controller disabled and its modem sleep off, the same flood lost nothing; with the controller disabled and
   modem sleep left on, it lost as much as ever. Light traffic loses 2.55–9.25% at 20–100 Hz; a flood loses a third
   or more.

   The L4 floods support the "receiver off for part of the time" reading. The number lost in 15 s stayed almost
   the same, 967–1073, whether A sent 2106 frames (window 1) or 4889 (window 16), at 24M or 54M, with or without
   audio. About 70 frames a second go missing whatever the load. A busy channel would lose a share of the
   frames, so the loss would grow with the load; this loss doesn't. It looks like the receiver's radio is away
   for a fixed slice of each second, in modem sleep or serving Bluetooth's time slots. `ps max` lost no more than
   `ps min` (1073 against 1032–1066), which made Bluetooth's time slots look the likelier cause. The lab11b runs
   tested that and ruled it out: Bluetooth off changed nothing, the receiver's `ps none` took the loss to zero.
   The slices are the station's modem sleep.

3. **`ps none` needs the Bluetooth controller off.** Asking for `WIFI_PS_NONE` on this IDF 4.4 image with the BT
   controller enabled aborts inside the coexistence blob (a panic, the board restarts). The lab refuses it with a
   400 while the controller is enabled; with the `bt` experiment's `off` (controller disabled at runtime) it is
   allowed, and that is how set-up (c) above ran on the product image. The `bt` stop puts modem sleep back to
   `min` before it enables the controller again. The fw template carries the rule: never `WIFI_PS_NONE` with the
   controller enabled.

4. **Round trips are short when frames land, long-tailed when they don't.** The median RTT is 3.7 ms at 1 Mbps
   for 64 bytes; p95 is 16–32 ms and p99 27.6–75.8 ms: the tail looks like the sender's MAC retrying until the receiver
   wakes. For control messages, sync beacons or "now playing" this is fine with an app-level retry; for audio
   frames it means buffering ≥ 100 ms, and multi-hundred-kbps streaming loses too much under modem sleep (with the receiver's modem sleep off it
   lost nothing at 3.6 Mbit/s).

5. **Audio didn't make it worse.** With both boards playing (silent tone), loss was 2.55–2.75%, against 4.35–9.25%
   without. The run-to-run spread from the shared air (the home Wi-Fi, neighbours) is larger than any audio
   effect, so the honest reading is "no measurable harm", not "audio helps". The tone is silent, so this is the
   I2S/DMA/CPU load, not Bluetooth audio streaming over the air (that is the BLE + A2DP rung).

6. **The send window matters more than the PHY rate.** On Lite 2, a 4-frame window received 95 kbps at 1M,
   403 kbps at 24M and 300 kbps at 54M. On Lite 4 at 54M, window 1 received 147 kbps, window 4 312 kbps and
   window 16 466 kbps. The loss per second stays the same (finding 2), so more frames in flight means more of
   them land. Expect ~0.3–0.5 Mbps of useful ESP-NOW alongside STA + BT (3.6 Mbit/s with no loss once the receiver's
   controller is off and its modem sleep is off), with a fifth to a third of frames
   needing a retry at the application. Use a window of 16 or more for bulk data.

7. **RAM is the tight resource.** Internal RAM with the product running is ~13 KB free; ESP-NOW's init takes
   nothing visible but the lab keeps its own buffers in PSRAM, and the lowest internal heap seen in any run was
   12.4 KB. The lab stops an experiment by itself under its floor. A product that adds ESP-NOW must keep its
   queues in PSRAM too. The margin is a few hundred bytes: lab7 put the receive callback's 270-byte item in
   static DRAM, and after 20 flood runs on Lite 1 the free internal heap sat at 11.8 KB, under the self-test's
   12000-byte check (the first run takes ~230 B that ESP-NOW keeps after `esp_now_deinit`; the next 18 took
   nothing, so not a leak). lab8 moves that item to PSRAM, which wasn't enough (11879 B at boot); lab9
   also moves the lab tick's 3 KB stack to PSRAM (it never writes flash) and gives ~15 K free: 15303 B before
   20 flood start/stops on Lite 1, 15075 B after, self-test passing at 14887 B. Queue and semaphore control blocks can't move: the
   ESP32's atomic compare-and-set doesn't work on PSRAM.

8. **Clock drift needs longer runs.** 20-second runs give drift estimates from −2.9 to +23.6 ppm: too short
   for the offset noise (a few ms of asymmetric delay). The clock-sync rung uses minutes-long runs and a
   regression over windows, not the end points.

9. **A board can hang, and the lab image couldn't recover.** Lite 2 stopped answering two seconds after a clean
   54M echo stop (the next command was a silent-tone start) and stayed off the network: the image had the task
   watchdog's panic turned off, so a stuck task meant a stuck board until someone pulls the plug. The lab image
   now (lab7):
   - sets `CONFIG_ESP_TASK_WDT_PANIC=y` with a 15 s timeout (lab variant only), so a hung task restarts the
     board, and the crash marker says which experiment was running. No Lite self-test report in the logs shows
     a watchdog reset at the old 5 s, so 15 s is margin, not a fix for a seen fault;
   - watches its own dial-in from an esp_timer, outside the lab's lock: off it for 15 minutes while the dial-in
     is configured, the lab restarts the board, and the next self-test says "the lab restarted it, off the
     dial-in 15 min";
   - watches its own tick from the same timer: a tick that hasn't run for 3 minutes (stuck holding the lab's
     lock, which the task watchdog doesn't see if the task isn't subscribed) restarts the board, and the
     self-test says "the lab restarted it, its tick stuck 3 min". A drill image (`board/lite-lab-stuck`) hangs
     the tick on purpose once to prove it.
   Every board the Hive leaves on a network without a person nearby should do all three; the fw template says so.

## Reproduce

```
cd projects/hifi-lite-lab/lab/tools
./lab_run.py espnow --a lite-1 --b lite-2 --role ping --secs 20 --rate_hz 100 --tag p100-psmin
./lab_run.py espnow --a lite-1 --b lite-2 --role flood --secs 15 --rate 24m --tag f24m
./lab_run.py espnow ... --audio          # both boards play a silent tone during the run
# Bluetooth on, ps min (lab12 image): retries, DTIM-timed bursts, listen_interval, controller-off window
F="--a lite-1 --b lite-4 --role flood --secs 30 --rate 54m --window 16 --len 250 --ps min"
./lab_run.py espnow $F --tag btonB-r8 --retry 8 [--retry_ms 50]
./lab_run.py espnow $F --tag btonA-dtim-w20000 --sync_us 1 --win_us 20000 [--off_us 50000]
./lab_run.py espnow $F --tag btonA-psmax-li10 --ps max --ps-a min --li 10
./lab_run.py espnow $F --tag btoffwin --bt-b off --ps none --ps-a min   # restart Lite 4 afterwards for A2DP
./lab_table.py
# A2DP beside the radios (a2dp2c image: lab_a2dp, Classic only)
./a2dp_run.py --src lite-1 --sink lite-4 --secs 30 --with none --tag ctl
./a2dp_run.py --src lite-1 --sink lite-4 --secs 30 --with espnow --tries 3 --tag r4
./a2dp_run.py ... --with espnow --espnow-role ping --tries 2 --tag ping
./a2dp_run.py ... --with espnow --order stream-first --tag sf
# Control plane beside a live stream (lite-lab-radio image, Lite 2 → Lite 3)
./a2dp_run.py --src lite-2 --sink lite-3 --secs 30 --with espnow --espnow-role ping --espnow-rate 5 --espnow-len 64 --order stream-first --tries 2 --tag rc5b
./a2dp_run.py --src lite-2 --sink lite-3 --secs 30 --with ble --order load-first --tries 2 --tag rcblefirst1
./lab_run.py espnow --a lite-2 --b lite-3 --role ping --rate_hz 5 --ps min --len 64 --secs 60 --tag rcbase5
./flac_run.py --dev lite-2 --stack psram --secs 30 --beside lite-3 --a2dp-secs 60 --tag rcbeside2
```

The reusable piece is the `espnow-link` component template in `~/modules/fw` (`fw new component espnow-link`):
peer setup on the station's channel, framed messages with sequence numbers, loss/RTT/clock counting, host test.
