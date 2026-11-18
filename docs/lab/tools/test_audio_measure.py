#!/usr/bin/env python3
"""Host tests for audio_measure.py: the sine fit, the analysis, the reply
reader and the chart, on synthetic captures shaped like Lite 2's loopback
replies (2026-11-17). Run: python3 tools/test_audio_measure.py"""
import base64
import json
import math
import os
import random
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import audio_measure as M


def pcm(left, right):
    v = []
    for a, b in zip(left, right):
        v += [max(-32768, min(32767, round(a * 32768))), max(-32768, min(32767, round(b * 32768)))]
    return base64.b64encode(struct.pack(f"<{len(v)}h", *v)).decode()


def sine(n, freq, amp, rate=48000, noise=0.0, h2=0.0, dc=0.0, drift=0.0, seed=1):
    r = random.Random(seed)
    return [dc + drift * i / n + amp * math.sin(2 * math.pi * freq * i / rate + .3)
            + h2 * amp * math.sin(2 * 2 * math.pi * freq * i / rate) + r.gauss(0, noise) for i in range(n)]


def reply(rms=(0.0273, 0.0267), frames=16384, samples=None):
    rep = {"source": "loopback", "rate": 48000, "frames": frames, "v_per_fs": [20.32, 20.32],
           "stats": [{"rms_fs": r, "peak_fs": r * 1.5, "dc_fs": 0.0, "clipped": 0} for r in rms]}
    if samples: rep["pcm"] = pcm(*samples)
    return rep


class Fit(unittest.TestCase):
    def test_clean_sine_has_tiny_residual(self):
        x = sine(128, 1000, 0.0386)
        f1, n = M.thdn(x, 1000, 48000)
        self.assertAlmostEqual(f1, 0.0386 / math.sqrt(2), places=5)
        self.assertLess(n / f1, 1e-3)   # 16-bit rounding only

    def test_noise_reads_as_its_rms(self):
        x = sine(128, 1000, 0.0386, noise=0.0002)
        f1, n = M.thdn(x, 1000, 48000)
        self.assertAlmostEqual(n, 0.0002, delta=0.00005)

    def test_harmonic_counts_in_thdn_not_in_n_only(self):
        x = sine(128, 1000, 0.0386, h2=0.01)
        f1, n = M.thdn(x, 1000, 48000)
        _, n_only = M.thdn(x, 1000, 48000, harmonics=6)
        self.assertAlmostEqual(100 * n / f1, 1.0, delta=0.1)
        self.assertLess(100 * n_only / f1, 0.1)

    def test_drift_from_the_adc_filter_is_taken_out(self):
        x = sine(128, 1000, 0.0386, dc=0.02, drift=-0.01)
        f1, n = M.thdn(x, 1000, 48000)
        self.assertLess(n / f1, 1e-3)
        _, n0 = M.thdn(x, 1000, 48000, trend=0)
        self.assertGreater(n0 / f1, 0.05)   # with DC alone the ramp stays in

    def test_median(self):
        self.assertEqual(M.median([3, 1, 2]), 2)
        self.assertEqual(M.median([4, 1, 2, 3]), 2.5)
        self.assertTrue(math.isnan(M.median([])))


def rows_for(dev="aabbccdd743c"):
    rows = [{"dev": dev, "kind": "meta", "info": {"name": "Hi-Fi Lite 2"}, "audio": {"channel": "stereo"}}]
    for hz in M.FR_FULL:
        g = 10 ** (-1 / 20) if hz in (20, 20000) else 1.0   # -1 dB at the ends
        rows.append({"dev": dev, "kind": "fr", "mode": "stereo", "freq": hz, "level": -20, "reply": reply((0.0273 * g, 0.0267 * g))})
    for hz in M.FR_OCT:
        rows.append({"dev": dev, "kind": "fr", "mode": "left", "freq": hz, "level": -20, "reply": reply((0.0273, 0.0273 * 0.004))})
    for lvl in M.THD_LEVELS:
        a = 0.1 * 10 ** (lvl / 20) * math.sqrt(2) * 2.73
        for i in range(M.SNIPS):
            s = (sine(128, 1000, a, noise=0.0002, seed=i), sine(128, 1000, a * 0.98, noise=0.0002, seed=50 + i))
            rows.append({"dev": dev, "kind": "thd", "mode": "stereo", "freq": 1000, "level": lvl, "i": i,
                         "reply": reply(frames=128, samples=s)})
    for amp in (True, False):
        for i in range(3):
            rows.append({"dev": dev, "kind": "noise", "mode": "stereo", "amp": amp, "i": i,
                         "reply": reply((0.0003 if amp else 0.0002, 0.0003 if amp else 0.0002))})
    rows.append({"dev": dev, "kind": "fr", "mode": "stereo", "freq": 440, "level": -20, "reply": {"error": "busy"}})
    settling = reply((0.032, 0.0218)); settling["stats"][0]["dc_fs"] = -0.028
    rows.insert(len(M.FR_FULL) + 1, {"dev": dev, "kind": "fr", "mode": "left", "freq": 20, "level": -20, "reply": settling})
    rows.insert(1, {"dev": dev, "kind": "settle", "mode": "stereo", "freq": 1000, "level": -20, "reply": reply((0.5, 0.5))})
    return rows


class Analyse(unittest.TestCase):
    def setUp(self):
        self.an = M.analyse(rows_for())

    def test_response_is_relative_to_1k(self):
        fr = self.an["modes"]["stereo"]["fr_db"]
        self.assertAlmostEqual(fr[1000][0], 0.0, places=6)
        self.assertAlmostEqual(fr[20][1], -1.0, places=3)
        self.assertEqual(M.band(fr, 20, 20000), (fr[20][0], 0.0))

    def test_settling_captures_are_left_out(self):
        self.assertEqual(self.an["modes"]["stereo"]["fr"][1000], [0.0273, 0.0267])    # not the settle row
        self.assertEqual(self.an["modes"]["left"]["fr"][20], [0.0273, 0.0273 * 0.004])  # the later, settled one
        self.assertEqual(self.an["dropped"], [{"mode": "left", "freq": 20, "level": -20}])

    def test_error_replies_are_skipped(self):
        self.assertNotIn(440, self.an["modes"]["stereo"]["fr"])

    def test_thdn_follows_the_noise(self):
        t = self.an["modes"]["stereo"]["thd_pct"]
        lo, hi = t[(1000, -30)][0]["thdn"], t[(1000, -6)][0]["thdn"]
        self.assertGreater(lo, hi * 10)          # 24 dB more signal, same noise
        self.assertAlmostEqual(t[(1000, -20)][0]["thdn"], 100 * 0.0002 / 0.0273, delta=0.25)

    def test_summary_volts_and_isolation(self):
        s = M.summary(self.an)
        self.assertAlmostEqual(s["noise_off_uv"][0], 1e6 * 0.0002 * 20.32, places=3)   # rms_fs is RMS already
        left = next(m for m in s["modes"] if m["mode"] == "left")
        self.assertAlmostEqual(left["iso_db"], 20 * math.log10(0.004), places=3)
        self.assertIsNotNone(left["fr_20_20k"][0])

    def test_each_mode_comes_from_its_latest_run(self):
        old = rows_for()
        for r in old:
            if r["kind"] == "fr" and r["freq"] == 1000: r["reply"] = reply((0.9, 0.9))   # the old run's figures
        old.append({"dev": "aabbccdd743c", "kind": "fr", "mode": "mono", "freq": 1000, "level": -20, "reply": reply()})
        rerun = [r for r in rows_for() if r["kind"] == "meta" or r.get("mode") == "left"]
        an = M.analyse(old + rerun)
        self.assertEqual(an["modes"]["left"]["fr"][1000], [0.0273, 0.0273 * 0.004])   # from the re-run
        self.assertEqual(an["modes"]["stereo"]["fr"][1000], [0.9, 0.9])               # stereo only in the old run
        self.assertIn("mono", an["modes"])

    def test_a_cut_snippet_is_skipped(self):
        rows = rows_for()
        rows.append({"dev": "aabbccdd743c", "kind": "thd", "mode": "stereo", "freq": 3000, "level": -20, "i": 0,
                     "reply": {**reply(frames=128), "cut": True}})
        an = M.analyse(rows)
        self.assertNotIn((3000, -20), an["modes"]["stereo"]["thd_pct"])

    def test_chart_writes_a_png(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.png"
            M.chart(self.an, p)
            self.assertGreater(p.stat().st_size, 10000)


class Run(unittest.TestCase):
    """run(): whatever fails, the board ends with the tone stopped, the amp on and its mode back."""

    def _run(self, fail_on):
        calls = []

        class Fake:
            def __init__(self, *a): pass

            def api(self, op, body=None, timeout=20):
                calls.append((op, body))
                if fail_on(op, body, calls): raise RuntimeError(f"api {op}: --- no answer to {op}: lost")
                if op == "state/audio": return {"audio": {"channel": "left"}}
                if op == "capture": return reply(frames=body["frames"])
                return {"ok": True}
        saved = M.Board, M.SETTLE_S, time.sleep
        M.Board, M.SETTLE_S, time.sleep = Fake, 0, lambda s: None
        try:
            with tempfile.TemporaryDirectory() as d:
                a = types.SimpleNamespace(dev="dev1", lane="l", out=str(Path(d) / "r.jsonl"), modes="stereo")
                with self.assertRaises(RuntimeError): M.run(a)
        finally:
            M.Board, M.SETTLE_S, time.sleep = saved
        return calls

    def test_amp_comes_back_on_when_the_amp_off_noise_fails(self):
        calls = self._run(lambda op, body, c: op == "capture" and ("amp", {"enable": False}) in c and ("amp", {"enable": True}) not in c)
        self.assertEqual(calls[-3:], [("tone", {"type": "stop"}), ("amp", {"enable": True}), ("channel", {"channel": "left"})])

    def test_a_failed_tone_stop_still_restores_the_mode(self):
        calls = self._run(lambda op, body, c: (op, body) in (("capture", {"source": "loopback", "frames": M.LONG}), ("tone", {"type": "stop"})))
        self.assertEqual(calls[-1], ("channel", {"channel": "left"}))
        self.assertNotIn(("amp", {"enable": True}), calls)


class Cut(unittest.TestCase):
    def test_a_cut_capture_keeps_its_stats(self):
        whole = json.dumps(reply(samples=(sine(4096, 1000, 0.04), sine(4096, 1000, 0.04))), separators=(",", ":"))
        r = M.parse_reply(whole[:996])
        self.assertTrue(r["cut"])
        self.assertNotIn("pcm", r)
        self.assertEqual(r["stats"][1]["rms_fs"], 0.0267)

    def test_other_cut_lines_are_errors(self):
        self.assertEqual(M.parse_reply('{"ok":tr')["error"], "cut")


class Reader(unittest.TestCase):
    """Board.api: the echo, then the next API line, from where the log stood."""

    def test_reads_the_reply_after_its_echo(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["H2_SERIAL_DIR"] = d
            (Path(d) / "remote").mkdir()
            log = Path(d) / "remote" / "dev1.log"
            log.write_text('2026-11-17T12:00:00 API {"old":true}\n')
            sent = []

            def ctl(req, timeout):
                sent.append(req)
                def later():
                    time.sleep(0.3)
                    with open(log, "a") as f:
                        f.write(f'2026-11-17T12:00:01 > {req["cmd"]}   (a5ba2)\n')
                        f.write('2026-11-17T12:00:01 API {"ok":true}\n')
                threading.Thread(target=later).start()
                return {"ok": True}

            sys.modules["serial_bridge"] = types.SimpleNamespace(remote_ctl=ctl)
            b = M.Board("dev1", "lane1", "a5ba2")
            self.assertEqual(b.api("tone", {"type": "silence"}, timeout=5), {"ok": True})
            self.assertEqual(sent[0]["cmd"], 'api tone "{\\"type\\":\\"silence\\"}"')
            self.assertEqual(sent[0]["lane"], "lane1")

    def _board(self, d, writer):
        os.environ["H2_SERIAL_DIR"] = d
        (Path(d) / "remote").mkdir(exist_ok=True)
        log = Path(d) / "remote" / "dev1.log"

        def ctl(req, timeout):
            threading.Thread(target=lambda: (time.sleep(0.3), writer(log, req))).start()
            return {"ok": True, "id": "ab12cd34"}
        sys.modules["serial_bridge"] = types.SimpleNamespace(remote_ctl=ctl)
        return M.Board("dev1", "lane1", "a5ba2"), log

    def test_follows_the_log_across_its_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            def writer(log, req):
                with open(log, "a") as f: f.write(f'2026-11-17T12:00:01 > {req["cmd"]}   (a5ba2)\n')
                os.replace(log, log.with_name(log.name + ".1"))
                with open(log, "a") as f: f.write('2026-11-17T12:00:02 API {"ok":true,"after":1}\n')
            b, log = self._board(d, writer)
            log.write_text("2026-11-17T12:00:00 " + "x" * 200 + "\n")
            self.assertEqual(b.api("info", timeout=5), {"ok": True, "after": 1})

    def test_another_agents_echo_is_not_ours(self):
        with tempfile.TemporaryDirectory() as d:
            def writer(log, req):
                with open(log, "a") as f:
                    f.write(f'2026-11-17T12:00:01 > {req["cmd"]}   (a5ba2x)\n')
                    f.write('2026-11-17T12:00:01 API {"theirs":true}\n')
                    f.write(f'2026-11-17T12:00:02 > {req["cmd"]}   (a5ba2)\n')
                    f.write('2026-11-17T12:00:02 API {"ours":true}\n')
            b, _ = self._board(d, writer)
            self.assertEqual(b.api("info", timeout=5), {"ours": True})

    def test_a_line_not_sent_fails_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            def writer(log, req):
                with open(log, "a") as f: f.write("2026-11-17T12:00:01 --- a5ba2's line ab12cd34 not sent: the board is offline\n")
            b, _ = self._board(d, writer)
            t = time.time()
            with self.assertRaisesRegex(RuntimeError, "not sent"): b.api("info", timeout=5)
            self.assertLess(time.time() - t, 3)


if __name__ == "__main__":
    unittest.main()
