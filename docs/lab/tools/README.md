# Lab tools

The scripts the agents used to measure the five boards over the remote
bench. They are kept as they ran; they reach the boards through the lab's
serial bridge (`serialtab`, `serial_bridge`), which is not part of this repo,
so they will not run here unchanged. The pure parts (the analysis, the
report tables, the judging) are covered by the `test_*.py` files, which run
anywhere:

```sh
cd docs/lab/tools && python3 -m unittest
```

| Script | What it does |
|---|---|
| `diag_report.py` | Runs the board's self-test and diagnostics and writes the per-board reports in `../reports/` |
| `audio_measure.py` | Frequency response, THD+N, noise and channel isolation through the codec's loopback taps |
| `soak.py` | Long playback soak: samples heap, temperature, Wi-Fi and the governor, and charts it |
| `scan_board.py` | A port scan of one board, run from another board on the same LAN |
