#!/usr/bin/env python3
"""Host tests for soak.py's series: hand-written notes and bench restarts
taken off the board's drop counts. Run: python3 tools/test_soak.py"""
import json
import tempfile
import unittest
from pathlib import Path

import soak


def rec(n, at, link, wifi, since):
    return {"n": n, "at": at, "link_drops": link, "wifi_disconnects": wifi, "bench_since": since}


class Series(unittest.TestCase):
    def write(self, rows, notes=()):
        d = Path(tempfile.mkdtemp(prefix="a5ba2-soaktest-"))
        log = d / "soak.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in rows))
        if notes:
            soak.notes_path(log).write_text("".join(json.dumps(n) + "\n" for n in notes))
        return log

    def test_restart_drop_left_out(self):
        s0 = "2026-11-17T03:00:00-05:00"
        s1 = "2026-11-17T03:25:33-05:00"
        log = self.write([rec(0, "2026-11-17T03:20:00-05:00", 0, 0, s0),
                          rec(1, "2026-11-17T03:26:00-05:00", 1, 0, s1),
                          rec(2, "2026-11-17T03:28:00-05:00", 1, 0, s1)])
        samples, _ = soak.series(log)
        self.assertEqual([s["link_drops"] for s in samples], [0, 0, 0])
        self.assertEqual(samples[1]["link_drops_raw"], 1)
        self.assertEqual(soak.summary(log)["bench_restarts"],
                         [{"at": s1, "link_drops_left_out": 1}])

    def test_wifi_drop_still_counts(self):
        s0, s1 = "2026-11-17T03:00:00-05:00", "2026-11-17T03:25:33-05:00"
        log = self.write([rec(0, "2026-11-17T03:20:00-05:00", 0, 0, s0),
                          rec(1, "2026-11-17T03:26:00-05:00", 1, 1, s1)])
        samples, _ = soak.series(log)
        self.assertEqual(samples[1]["link_drops"], 1)

    def test_drop_without_restart_counts(self):
        s0 = "2026-11-17T03:00:00-05:00"
        log = self.write([rec(0, "2026-11-17T03:20:00-05:00", 0, 0, s0),
                          rec(1, "2026-11-17T03:22:00-05:00", 1, 0, s0)])
        self.assertEqual(soak.series(log)[0][1]["link_drops"], 1)

    def test_note_and_old_log(self):
        # a log from before bench_since was logged: only the note applies
        rows = [{"n": 0, "at": "2026-11-17T03:20:00-05:00", "link_drops": 0},
                {"n": 1, "at": "2026-11-17T03:26:00-05:00", "link_drops": 2}]
        log = self.write(rows, [{"at": "2026-11-17T03:25:33-05:00", "note": "bench restart",
                                 "exclude": {"link_drops": 1}}])
        samples, _ = soak.series(log)
        self.assertEqual(samples[1]["link_drops"], 1)
        self.assertEqual(soak.summary(log)["bench_restarts"], [])


class FakeBench:
    def __init__(self, sys_reply):
        self.sys_reply, self.asked = sys_reply, []

    def ask(self, cmd, body=None):
        self.asked.append(cmd)
        return self.sys_reply


class RestartCause(unittest.TestCase):
    CAUSE = "squeezelite_exit by squeezelite at 0x400d1234 after 332 s"

    def test_from_diag(self):
        b = FakeBench({})
        self.assertEqual(soak.restart_cause(b, {"reset_reason": "restart", "restart_cause": self.CAUSE}), self.CAUSE)
        self.assertEqual(b.asked, [])

    def test_diag_full_falls_back_to_state_sys(self):
        b = FakeBench({"ok": True, "restart_cause": self.CAUSE})
        self.assertEqual(soak.restart_cause(b, {"reset_reason": "restart"}), self.CAUSE)
        self.assertEqual(b.asked, ["state/sys"])

    def test_old_firmware_and_other_resets(self):
        self.assertIn("before #222", soak.restart_cause(FakeBench({"ok": True}), {"reset_reason": "restart"}))
        b = FakeBench({})
        self.assertIsNone(soak.restart_cause(b, {"reset_reason": "panic"}))
        self.assertIsNone(soak.restart_cause(b, None))
        self.assertEqual(b.asked, [])

    def test_summary_lists_restarts(self):
        d = Path(tempfile.mkdtemp(prefix="a943e-soaktest-"))
        log = d / "soak.jsonl"
        rows = [{"n": 0, "at": "2026-11-17T11:50:00-05:00", "uptime_s": 300},
                {"n": 1, "at": "2026-11-17T11:54:00-05:00", "uptime_s": 20, "rebooted": True,
                 "reset_reason": "restart", "restart_cause": self.CAUSE}]
        log.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(soak.summary(log)["restarts"],
                         [{"at": rows[1]["at"], "reset_reason": "restart", "restart_cause": self.CAUSE}])


if __name__ == "__main__":
    unittest.main()
