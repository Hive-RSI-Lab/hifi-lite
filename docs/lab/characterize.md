# Lane hifi-lite-characterize: notes

The thread is `hifi-lite-characterize`, and the agent is a5ba2. The rung is "Self-test reports" in goals.md.

## What's there
- **`api diag`** on the board is in hifi-receiver's `firmware/components/hifi/core/hifi_diag.[ch]`. The pure parts and the JSON writers are host-tested in `firmware/test/test_diag.c`. The ESP side is `esp/hifi_diag_esp.c`, and the audio counters are in patch 0009. Calls:
  - `diag`: heap, PSRAM, boot times, reset history, NVS and power.
  - `diag/tasks`: stack high-water mark, core, priority and CPU % per task. It pages with `from`.
  - `diag/net`: RSSI, channel, connects and drops, and the link's bytes.
  - `diag/audio`: underruns, buffer, DMA latency and played seconds.
  - `diag/ota`: the last update's size, total time and flash-write time.
  - `diag/echo`: up to 1 MB of filler sent up the bench link and timed.
  - The docs are in `docs/test-api.md` under "Diagnostics". The code was merged in PR #188 (into h2/lite-bringup).
- **`tools/diag_report.py`** gathers from one board (`--dev ID`) or all of them (`--all`) over serialtab. It writes `reports/<board>-<date>.md` and `reports/raw/<board>-<date>.json`, then rebuilds `reports/README.md` with a side-by-side table, outliers and worries. `reports/NOTES.md` is the hand-written reading, put first in the README. `--readme-only` rebuilds the README from the raw files. Its tests are in `tools/test_diag_report.py` (`uv run --with pytest pytest -q tools/test_diag_report.py`).
  - It runs the self-test and echo only on a board the lane holds a lease on, unless you pass `--selftest`/`--echo`. On other boards it only reads.
  - It doesn't depend on the lab, so it can move into `~/modules/fw` with serialtab as its transport.

## Gotchas
- A serialtab body must be one quoted JSON string: `api tone "{\"type\":\"sine\",...}"`. Bare JSON gets "the body is one quoted JSON object".
- The recovery image links part of squeezelite (output.c through `dac_set`) but not the decoder, so a squeezelite patch that calls into decode_external.c must make that reference weak, or recovery fails to link with duplicate symbols.
- Bench replies are capped at 1000 characters, so every diag reply stays under 900 and the task list pages.
- The task core is only known with `CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID=y` (set in lite-main). It is null for unpinned tasks.
- PVDD can read up to 0.3 V above VBUS from divider and ADC tolerance. That's not a fault.
- The bench used to drop an `api` line whose body held NaN or Infinity, with no reply, so the caller timed out. `remote_bench.parse_api` now refuses it with "the body isn't JSON" (the h2-bench-dev service picks the fix up at its next restart).
- Don't run the self-test or a diag gather on Lite 3 while the soak runs: the soak holds the amp off, so the self-test fails amp_no_fault, and the tones push CPU to 80–100 %.
- `NOTES.md` placeholders: `{{min:KEY}}`, `{{max:KEY}}`, `{{argmin:KEY}}` (the board's name), `{{Lite 3:KEY}}`, and `{{argmin:KEY>OTHER}}` (OTHER's value on the board that has the lowest KEY). Add a conversion with `|kb`, `|kb1`, `|mbit`, `|s` or `|h`. An unfilled one shows as **?** in the README.

## Next
- The soak on Lite 3 is running as the user unit `a5ba2-soak` (12 h from 2026-11-17 03:11, log `lab/data/soak-2026-11-17T0311.jsonl`, watch with `journalctl --user -u a5ba2-soak`). It plays -20 dBFS tones with the amp held off, and one cycle in 15 at -40 dBFS with the amp on during the day. There's no safe forced Wi-Fi reconnect over the bench (rejoining needs the network password), so it counts the natural drops instead. Chart it with `tools/soak.py --chart FILE` when it ends.
- The review fixes for PR #188 are in PR #192 (branch h2/diag-review-fixes), and Lite 3 runs that image. Lite 4 gets it through aeb53 in hifi-lite-security, with the LAN-gate patch. Then rerun `--all` so all five have the full figures.
