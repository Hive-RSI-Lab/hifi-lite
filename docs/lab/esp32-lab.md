# Lane esp32-lab: the Lites as an ESP32 lab

Owner: a4b0e. Thread: esp32-lab. Boards: Lite 1 (lite-1), Lite 2 (lite-2).
Firmware branch: `h2/esp32-lab` of the hifi repo (worktree `~/.cache/h2-tmp/a4b0e/hifi`).
Templates: branch `h2/fw-new-component` of `~/modules/fw`.

## The lab frame

A lab image is the Lite image (`lite-main`) with `CONFIG_HIFI_LAB=y`. It is the
board folder `firmware/board/lite-lab/`, a *variant*: its `base` file names
`lite-main`. `tools/fetch.sh` takes the base's defaults and partition table and
adds the variant's `sdkconfig.extra`. Bootloader and partitions stay the same,
so a Wi-Fi update can carry the image either way.

It keeps everything that makes a Wi-Fi image safe:
- the remote dial-in;
- the Hive key, so every console command is signed;
- the OTA trial and rollback;
- the self-test.

On top of that it adds:

- **`api lab`** (signed like every other command):
  - `{"op":"list"}` (also a plain GET)
  - `{"op":"start","exp":"espnow",...}`
  - `{"op":"stop","exp":...}`
  - `{"op":"result","exp":...}`

  One experiment runs at a time. Each start carries a deadline (`max_s`, at
  most 3600 s), so a lost stop can't leave a radio experiment running. The lab
  also stops an experiment by itself when internal RAM falls under its floor.
- **Crash marker:** the running experiment's name sits in RTC no-init RAM.
  After a panic, watchdog or brownout restart, the self-test line says which
  experiment was running.
- **Self-test line `lab`:** the frame is up, which means:
  - its lock exists;
  - at least one experiment is registered;
  - its tick task ran in the last 5 s.
- **Boot trial:** a lab image is kept only when, on top of the product's
  checks (I2C, heap, Wi-Fi, relay), the lab frame is up (`lab_on`/`lab_ok` in
  `hifi_trial_t`). Otherwise the board rolls back after the trial limit (120 s).
- **Rollback drill:** `board/lite-lab-drill/` (`CONFIG_HIFI_LAB_DRILL`) is the
  lab image with the frame reporting itself broken. Flash it only to prove the
  rollback, then put lite-lab back.

The board-free logic lives in `core/hifi_lab.c` (the registry, deadlines and
JSON) and `core/hifi_link.c` (frames, loss, RTT, clock offset). It is
host-tested in `test/test_lab.c` (`make -C firmware/test`). The IDF side is
`esp/hifi_lab_esp.c` and `esp/lab_espnow.c`.

The drivers are `lab/tools/lab_run.py` (two-board runs through `serialtab
send --dev`). Results go to `lab/data/*.json`, and the write-ups go to `lab/*.md`.

## Log

- 2026-11-17 07:10Z: the first lab image (`hifi-lite-lab`, 3.0 MB) went onto
  Lite 1 over Wi-Fi (bringup 8f855352). It marked itself good and its self-test
  passed, with `lab` reading "1 experiments". `api lab` lists `espnow`.
- 2026-11-17 07:26Z: rollback drill. `hifi-lite-lab-drill` (the frame reports
  itself broken) went onto Lite 1; the board never marked it good and rolled
  back to the lab image by itself after the trial limit. Lite 1 back on lab.
- 2026-11-17 ~07:30Z: lab3 on both boards (Lite 1, then Lite 2). The first
  image couldn't start a run: only ~13 KB of internal RAM is free with Wi-Fi,
  BT and audio up, and an internal-RAM queue and stack didn't fit. lab3 keeps
  ESP-NOW's queue, worker stack and histogram in PSRAM.
- 2026-11-17: asking for `ps none` with BT on panicked the board (the IDF 4.4
  coexistence blob). The lab now refuses it with a 400.
- 2026-11-17 07:34–07:39Z: the ESP-NOW matrix, 8 runs saved
  (`lab/data/espnow-*.json`, written up in `lab/espnow.md`).
- 2026-11-17 07:39Z: Lite 2 hung right after a clean 54M echo stop and stayed
  off the network: the image had no task-watchdog panic. lab5 adds
  `CONFIG_ESP_TASK_WDT_PANIC=y` and a dial-in watch (15 min off the dial-in
  while configured: the lab restarts the board and the self-test says so).
  Lite 2 waits for a power cycle by the person.

- 2026-11-17 ~08:15Z: review fixes from a65ab (esp32-lab-review 11170300845e)
  in lab7: frame v2 with a length field, run-tagged finishes, an esp_timer
  watch for the dial-in and for a stuck lab tick (3 min), task watchdog 15 s.
  `board/lite-lab-stuck/` is a drill image that hangs the tick once to prove
  the watch.
- 2026-11-17: BLE size probe (`board/lite-lab-ble/`, not flashed). Dual-mode
  Bluetooth with Bluedroid GATT server and client adds ~190 KB of flash and
  ~13 KB of IRAM: the full Lite image no longer fits its 3.06 MB app slot.
  Without Spotify Connect (`CONFIG_CSPOT_SINK` off) it fits: app 2.70 MB (88%),
  IRAM 98.2% (2338 B left), static DRAM 46352 B. So BLE experiments run on a
  lab image without Spotify; AirPlay, A2DP and LMS stay, so BLE beside A2DP can
  still be measured. A product with BLE (provisioning, say) needs a bigger app
  slot (the 4 MB flash has room only if recovery shrinks) or one sink fewer;
  NimBLE is no way out here, since IDF runs one Bluetooth host and A2DP needs
  Bluedroid.
- 2026-11-17 12:00–12:45Z (aeb53, borrowed with a4b0e's agreement in
  11170700edf9): Lite 1 ran `aeb53-lanscan2` and then `aeb53-lanscan3`
  (hifi-receiver #203). Both add the `lanscan` op and move the lab and
  listener tables to PSRAM, so it boots at 12555 B internal heap free against
  11535 B before. Lite 1 scanned Lite 4, 5 and 3 from the LAN; the results
  are in `security.md` ("The network side and the radio") and
  `lab/data/lanscan-*.json`. Lite 1 went back to `esp32-lab-lab11b` afterwards.
- 2026-11-17 13:46–13:54Z (a4b54, leased with a4b0e's and aeb53's agreement):
  the frame tag (hifi-receiver #207, HMAC-SHA256 with a fresh key per run) on
  Lites 1, 4 and 5. Lite 5 forged Lite 1's flood to Lite 4
  (`lab/tools/forge_run.py`, data `lab/data/forge-*.json`). With a stranger's
  key, 539 forged frames were counted bad and loss stayed at 0.3% (genuine
  frames lost on a busier channel). Replays counted only as dups. The control,
  tagging with the run's own key, threw loss to 18.5%. About 30 forged frames a
  second slow the flood ninefold. That is jamming, which the tag can't stop.
  The tagged image carried 2.8 Mbit/s against 3.8 for lab12. That is an upper
  bound on the tag's cost, because the two images also differ by #205 and #206.
  Lite 1 and 4 went back to lab12, and Lite 5 went back to hifi-lite-aaf0483.
- 2026-11-17 22:40–23:58Z: Bluetooth as a party source, on the product image (Lite 2 leads, Lite 3 is the
  A2DP source on its lab image, leased from a3f17). Lite 2's internal heap with A2DP streaming: 13655 B free
  (12291 lowest) before #232, 32519 (31595) with it. A stand-in for the party worker (hifi PR #237,
  `board/lite-party-standin`) sending 8 × 1452 B every 64 ms to Lite 3 ran beside A2DP: 13.3 min of A2DP,
  11.5 of it sending (at 133/s, a tick rounding since fixed); lowest 30975 B, 0 underruns, no disconnects. One
  follower only. While it sent, a new A2DP connection failed ("reason 0", 3 of 3, twice), but Lite 3 was both
  source and target, so that is unconfirmed as a leader limit. Opus review of #237 found these; fixed in the PR.
  Written up in `party-mode.md` 5.1, data `lab/data/bt-*.json`. To repeat with a7fcd's worker when it lands.
  Lite 3's power_on resets 00:15–00:35Z were the bench supply, not firmware.

## Gaps found in the tools

- `fw new check` caught three IDF errors in the espnow-link template that the
  host test couldn't see:
  - a role/frame-type enum clash;
  - `MACSTR` and `esp_random` moving to `esp_mac.h` and `esp_random.h` in IDF 5;
  - the ESP-NOW callback shapes changing in 5.0 (receive) and 5.5 (send).
- `serialtab publish DIR` wants the `build/` dir with `bench.json` inside it.
  Given the project dir, it failed with a traceback, not a message (fixed by
  ae214, post 11170235cfb4).
- The lab image had the task watchdog's panic off (inherited from the product
  defaults), so a hang was permanent on an unattended board. Fixed in the lab
  variant; the product image should get the same (the hardening lane).
