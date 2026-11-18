# BLE GATT between two Hi-Fi Lites, alongside Wi-Fi and the A2DP sink

Lane esp32-lab (a4b0e), 2026-11-17. Boards: Lite 1 (A, lite-1) is the GATT client and pings or floods;
Lite 4 (B, lite-4) is the GATT server and echoes. Lite 2 is offline and waiting for a power cycle.
Both boards run the BLE lab image (`board/lite-lab-ble`). That is the lab image with the controller in dual mode
(Classic + BLE) and Bluedroid's GATT server and client. Spotify Connect is left out so it fits; the reason is below.
Both stay connected to the home Wi-Fi as stations, with the A2DP sink up as in the product. The experiment is
`esp/lab_ble.c`. It speaks the same frames as the ESP-NOW one, and the board-free counting is `core/hifi_link.c`,
which is host-tested. Images: ble6 for the first four runs, then ble7, which reports the connection interval
and keeps the link's MTU in the result. Both images are the same on the air.

## Method

- Each run is one `lab/tools/lab_run.py ble ...` call. It starts B's echo first: B advertises its lab mark (company
  0xffff, "hl" and its Wi-Fi MAC) only during a run. It then starts A's role, which scans for that mark, connects,
  asks for a 247-byte MTU, finds the lab service, turns notifications on and runs for 30 s. Both boards'
  results are saved to `lab/data/ble-<role>-<tag>-<time>.json`, and `lab/tools/ble_table.py` prints the table
  below.
- **ping**: A writes 64-byte PING frames (write without response) at 20, 50 or 100 Hz. B answers each with a
  notification. The RTT is measured from the send to the reply, on A's clock, minus B's turnaround time. Each reply
  also carries B's clock, which gives an offset and a drift estimate (the lowest-delay sample in each 1 s window).
- **flood**: A writes 244-byte DATA frames (a 247-byte MTU minus 3) as fast as the stack takes them. A waits for a
  free buffer on the link and for the stack not to be congested. B counts the frames, in order, by sequence
  number.
- `interval` is the connection interval the link ran at. A asks for it after connecting (`interval_ms`; 7.5 ms
  is 6 units of 1.25 ms). Without a request, Bluedroid's default is 15 ms with a 6 s supervision timeout.
  With a request the timeout is 4 s.
- `audio` means both boards played a silent tone for the whole run. That loads the full I2S/codec path at zero
  amplitude. It is **not** an A2DP stream over the air (see finding 6).

## Results

| run | interval ms | audio | load | frames back | lost | RTT min/p50/p95/p99/max ms, or kbit/s | drift ppm | min heap A / B |
|---|---|---|---|---|---|---|---|---|
| ping20 (ble6) | 15 (default) | no | 20 Hz | 600 of 600 | 0 | 18.9/29.8/55.6/61.9/74.8 | +43.4 | 11399 / 11943 |
| ping100 (ble6) | 15 (default) | no | 100 Hz | 3000 of 3000 | 0 | 11.1/29.9/55.5/64.5/79.3 | +67.3 | 11255 / 11915 |
| ping iv8 | 7.5 | no | 50 Hz | 1500 of 1500 | 0 | 12.0/18.2/42.9/55.0/61.2 | −11.5 | 11319 / 11999 |
| ping iv15 | 15 | no | 50 Hz | 1500 of 1500 | 0 | 16.6/29.2/52.3/62.4/78.0 | +27.8 | 11363 / 12007 |
| ping iv30 | 30 | no | 50 Hz | 1500 of 1500 | 0 | 23.7/57.9/90.8/92.9/119.8 | +14.2 | 11323 / 11979 |
| ping iv50 | 50 | no | 50 Hz | 1500 of 1500 | 0 | 16.0/88.4/100.0/100.0/220.2 | +402.9 | 11319 / 11979 |
| ping audio50 | 15 | yes | 50 Hz | 1500 of 1500 | 0 | 13.3/29.4/54.6/63.4/77.9 | −12.2 | 11227 / 11943 |
| flood244 (ble6) | 15 (default) | no | 244 B writes | 8019 of 8029 | 0 | 474.8 sent, 473.4 received | | 11239 / 11947 |
| flood iv8 | 7.5 | no | 244 B writes | 6365 of 6373 | 0 | 394.9 sent, 393.6 received | | 10967 / 11943 |
| flood iv15 | 15 | no | 244 B writes | 7556 of 7564 | 0 | 467.7 sent, 466.3 received | | 10971 / 11943 |
| flood iv30 | 30 | no | 244 B writes | 7254 of 7264 | 0 | 456.8 sent, 456.2 received | | 10967 / 11975 |

In the flood rows, "frames back" is what B received in order. The 8–10 frames between that and "sent" were still
in flight when the run stopped. B saw no sequence gaps, duplicates or bad frames. Every link came up in 0.4–0.9 s
from the start of the scan, with no rejected connections and no congestion events.

## Findings

1. **BLE lost nothing; ESP-NOW lost 2.5–9% at the same rates.** All 9600 PINGs got their PONG and every flood
   arrived complete and in order. BLE's link layer acknowledges and retransmits every packet inside the connection
   events, and the controller schedules those events around Classic BT and Wi-Fi itself. ESP-NOW's frames go
   unacknowledged whenever the receiver's radio is away (espnow.md, finding 2). For anything that must arrive
   (commands, sync beacons, settings), a BLE link between two Lites needs no retry layer at the application.

2. **The round trip is about two connection intervals, so set the interval for the latency you need.**
   The median RTT is 18 ms at 7.5 ms, 29 ms at 15 ms, 58 ms at 30 ms and 88 ms at 50 ms. That is 1.8–2.4
   intervals: the write goes out in one connection event and the notification comes back in the next. The tail
   is a few intervals more (p99 55 ms at 7.5 ms, 100 ms at 50 ms), and the longest round trip in any run was
   220 ms. The ping rate made no difference (20 and 100 Hz both had a 30 ms median at 15 ms): the stack batches
   frames into the same events. Against ESP-NOW (3.7 ms median, a long tail and losses), BLE is slower in the
   median and steadier in the tail.

3. **About 470 kbit/s of payload, and the shortest interval is not the fastest.** 244-byte writes without
   response gave 467–475 kbit/s at 15 ms and 457 kbit/s at 30 ms, but only 395 kbit/s at 7.5 ms. With short
   intervals, more of each connection event goes to fixed overhead and to the slots that Wi-Fi and Classic BT take.
   That is an inference: the cause wasn't isolated. Expect about 0.45 Mbit/s of reliable BLE between two Lites
   beside the station and the A2DP sink. That is the same as ESP-NOW's best (466 kbit/s, window 16), with no
   losses.

4. **Dual mode costs about 12 KB of internal RAM.** The cuts below win it back for this image only.
   ble4 (dual mode, nothing trimmed) and ble5 (Wi-Fi static TX buffers 8 → 4, no BLE pairing, small BLE scan
   caches, a GATT table for one service) both failed their boot trial: ble5 had 9.1–9.4 KB internal free against
   lab10's 14.9 KB, and the self-test needs 12000 B. ble6 also trims the static Wi-Fi RX buffers (8 → 5; the 40
   dynamic ones stay in PSRAM), the Wi-Fi management short buffers (32 → 12) and the classic ACL links in the
   controller (to 1, as the A2DP sink takes one source). It passed with 15.0–15.2 KB free, the same as lab10.
   Fewer static TX and RX buffers may cost Wi-Fi throughput under bursts, and with BLE sharing the radio they
   may cost the dial-in under heavy load. Neither was measured: the dial-in stayed up through every run here,
   but no run loaded Wi-Fi. That is one reason these cuts are for the lab image only; a product image with BLE
   needs a dial-in soak under Wi-Fi load before it ships them. The BLE pairing cut saves nothing while Classic
   is on (Classic keeps the SMP code).

5. **Heap during runs stays above the lab's floors.** The lowest internal heap in any run was 10967 B, on the
   client during a flood. The lab frame won't start a run below 10000 B and stops one below 7000 B, so every
   run stayed above both. The 12000-byte check is the boot self-test's, and every board passed it after these
   runs (ble7: 15271 B on Lite 1, 15071 B on Lite 4). The link's buffers (queue, worker stack, statistics) are in
   PSRAM, as in ESP-NOW. The ~3.3 KB dip is Bluedroid's own: link state, the GATT client's cache and its buffers.

6. **BLE beside a real A2DP stream: no measurable effect, once there is RAM for it.** The "audio" run plays a
   silent tone through I2S. It showed no effect (0 lost, a median RTT of 29.4 ms against 29.2 ms), but the radio
   carried no audio. The A2DP-source rung (espnow.md, "Radios side by side with real audio") then streamed a real
   tone from Lite 1 to Lite 4. On this dual-mode image with the A2DP source added, the boards boot at about 12 KB
   of internal RAM and an A2DP link takes about 6.3 KB on each board; the stream alone took both boards under the
   lab's 7 KB stop floor in 2.5–3.5 s (lows 4.5 and 4.8 KB), before any BLE. A BLE link adds about 3.3 KB
   (finding 5), so with the stream up it needs about 5 KB above the run floor (free heap runs about 1.5 KB under
   its steady level). The radio image (`board/lite-lab-radio`) moved squeezelite's decode and stream stacks to
   PSRAM (patches/0019) and had about 25 KB free with the stream up. There, a 20 Hz BLE ping started after the
   stream, in 2 runs of about 90 s (audio counted over 30 s): 1800 of 1800 pings answered each run, no audio data
   lost, no underrun, longest A2DP gap 75–76 ms and 24–25 gaps over 50 ms, against 76 ms and 39–50 with no load.
   With an ESP-NOW flood as well (one run): 1800 of 1800, no underrun, 142 gaps over 50 ms (the flood's doing).
   Free heap with BLE up was about 21 KB; the lowest in any run, 20.6 KB. A BLE link started before the A2DP
   connect was not tried. Data and the table: espnow.md, "With room for a second radio".

7. **30-second runs can't measure drift.** Estimates ranged from −12 to +67 ppm, and +403 ppm at 50 ms. The
   best-of-each-second offset sample is quantised by the connection interval: an error of a few ms over 30 s is
   ±100 ppm, and at 50 ms it is worse. ESP-NOW's 3.7 ms round trips gave −1 to −3 ppm on the same boards.
   Clock sync over BLE needs minutes-long runs and the controller's own event timing (the anchor points),
   not GATT round trips. Over ESP-NOW it is easier.

8. **Who can connect.** B advertises only during a run, and only answers the board named as its peer. The peer's
   BLE address is worked out from its Wi-Fi MAC with the same offset this board's own two addresses have (+2 on
   these boards). Any other device that connects is dropped at once and counted (`rejected`, 0 in every run).
   There is no pairing or bonding, and nothing is stored. The link is not encrypted. A product link that carries
   commands needs LE Secure Connections pairing, or keys at the application layer.

9. **Flash: BLE doesn't fit beside everything else.** The GATT server and client add about 190 KB of flash and
   13 KB of IRAM, so the full Lite image no longer fits its 3.06 MB app slot. The BLE lab image leaves Spotify
   Connect out; AirPlay, A2DP and LMS stay. A product image with BLE needs a bigger app slot or a lighter stack
   (NimBLE, which wasn't tried here). The same config makes the recovery build 2.0 MB against its 1.75 MB slot,
   so the BLE lab image is only ever flashed as the app over OTA. The boards keep the recovery image they had
   before, which has no BLE.

## Reproduce

```
cd projects/hifi-lite-lab
python3 lab/tools/lab_run.py ble --a lite-1 --b lite-4 --role ping --secs 30 --rate_hz 50 --interval_ms 15 --tag iv15
python3 lab/tools/lab_run.py ble --a lite-1 --b lite-4 --role flood --secs 30 --interval_ms 15 --tag flood-iv15
python3 lab/tools/lab_run.py ble ... --audio      # both boards play a silent tone during the run
python3 lab/tools/ble_table.py
```

The image is `board/lite-lab-ble` (published as esp32-lab-ble7). The board API is
`api lab {"op":"start","exp":"ble","role":"echo|ping|flood","peer":"<the other board's id>",...}`, with optional
`rate_hz`, `len`, `count`, `interval_ms` and `scan_s` (see the top of `esp/lab_ble.c`). The reusable piece is the
`ble-link` component template in `~/modules/fw` (`fw new component ble-link`): the GATT server and client, the
advert mark, the peer-address rule, and the same frames and counting as `espnow-link`, with a host test.
