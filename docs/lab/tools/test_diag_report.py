#!/usr/bin/env python3
"""Host tests for diag_report.py: the log parser, the metrics, the worries,
the outliers and the writers, on replies captured from Lite 3 (2026-11-17).
Run: python3 tools/test_diag_report.py"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import diag_report as R

DIAG = json.loads('{"uptime_s":12,"boot_ms":{"app":1278,"ip":4688,"link":9195},"heap":{"free":31995,"min":31059,"largest":15872,"total":325083},"psram":{"free":1204551,"min":1194419,"largest":1179648,"total":4101343},"reset_reason":"restart","resets":"restart,restart","boots":2,"nvs":{"nvs":{"used":101,"free":403,"total":504,"namespaces":8,"used_pct":20.0},"settings":{"used":141,"free":1875,"total":2016,"namespaces":1,"used_pct":7.0}},"power":{"state":"on","vbus_v":19.95,"vbus_i_a":null,"pvdd_v":19.8,"pg":true,"proto":"pd","contract_v":20.0,"contract_a":1.5,"amp_on":true,"amp_fault":false,"amp_faults":0,"amp_temp_c":null}}')
TASKS = {"n": 4, "from": 0, "next": 4, "sample_ms": 1011, "idle_pct": [89.7, 98.9],
         "cols": ["name", "stack_free", "core", "prio", "cpu_pct"],
         "rows": [["IDLE1", 928, 1, 0, 98.9], ["IDLE0", 932, 0, 0, 89.7], ["hifi_power", 1008, 0, 3, 7.1],
                  ["decode", 13960, 1, 5, 0.0]]}
NET = json.loads('{"wifi":{"rssi":-43,"channel":11,"bssid":"aa:bb:cc:dd:ee:ff","phy":"11n","connects":1,"disconnects":0,"last_reason":null,"up_s":53},"link":{"up":true,"drops":0,"up_s":48,"tx_bytes":2895,"rx_bytes":2756}}')
AUDIO = {"underruns": 0, "rate": 44100, "buffer_ms": 0, "dma_ms": 139, "latency_ms": 139, "played_s": 0}
OTA = {"version": "local.500.cmake-master", "bytes": 2996832, "total_ms": 128132, "write_ms": 19375,
       "kbps": 187.1, "write_kbps": 1237.4, "boot": 1}
SELF = json.loads('{"pass":true,"checks":[{"name":"pd_contract","pass":true,"value":"pd 20.0 V 1.50 A"},{"name":"heap","pass":true,"value":"17375 B free"}]}')
INFO = {"name": "Hi-Fi Lite 3", "fw": "0.1.0", "upstream": "squeezelite-esp32 master-v4.3 ee1720c",
        "flash_mb": 8, "psram_mb": 8}
STATE = json.loads('{"uptime_s":6796,"power":{"state":"on","vbus_v":19.76,"pg":true,"contract":{"protocol":"pd","epr":false,"v":20.0,"i_max_a":1.5,"w":30.0},"pvdd_on":true,"pvdd_v":19.82},"amp":{"enabled":false,"fault":false,"faults":0,"temp_c":null},"net":{"rssi":-48},"sys":{"heap_free":40000,"heap_min":30000,"psram_free":1300000,"reset_reason":"power_on"}}')


# Lite 3's own state: on the fixed image it counts heap the way diag does
STATE3 = copy.deepcopy(STATE)
STATE3["sys"].update(heap_free=31995, heap_min=31059)


def full():
    return {"at": "2026-11-17T03:00:00", "info": INFO, "state": STATE3, "diag": DIAG, "diag/tasks": TASKS,
            "diag/net": NET, "diag/audio": AUDIO, "diag/ota": OTA, "selftest": SELF,
            "selftest_at": "2026-11-17T02:21:38", "diag/echo": {"bytes": 1048576, "ms": 3617, "kbps": 2319.1}}


def old():
    """A board on the image before diag: 404 for diag, the rest from state."""
    return {"at": "2026-11-17T03:00:00", "info": dict(INFO, name="Hi-Fi Lite 2"), "state": STATE,
            "diag": {"error": "no such path", "status": 404}, "selftest": SELF, "selftest_at": ""}


LOG = """2026-11-17T02:13:21 > api diag/ota   (a5ba2)
2026-11-17T02:13:30 --- the link dropped
2026-11-17T02:13:30 --- no answer to diag/ota: the board's link dropped
2026-11-17T02:16:55 > api diag   (a5ba2)
2026-11-17T02:16:56 API {"uptime_s":5}
2026-11-17T02:18:11 > api diag/echo "{\\"bytes\\":1048576}"   (a5ba2)
2026-11-17T02:18:15 API {"bytes":1048576,"ms":3617,"kbps":2319.1}
2026-11-17T02:21:38 > api selftest "{}"   (bringup)
2026-11-17T02:21:39 API {"pass":true,"checks":[]}
2026-11-17T02:22:00 > api diag   (other)
2026-11-17T02:22:01 API {"uptime_s":70}
2026-11-17T02:23:00 > api diag/net   (a5ba2)""".splitlines()


class Log(unittest.TestCase):
    def test_reply_to_last_ask(self):
        self.assertEqual(R.parse_reply(LOG, "diag"), ("2026-11-17T02:22:01", {"uptime_s": 70}))

    def test_reply_by_sender(self):
        self.assertEqual(R.parse_reply(LOG, "diag", "a5ba2")[1], {"uptime_s": 5})

    def test_reply_with_body(self):
        self.assertEqual(R.parse_reply(LOG, "diag/echo")[1]["kbps"], 2319.1)
        t, s = R.parse_reply(LOG, "selftest")
        self.assertEqual((t, s["pass"]), ("2026-11-17T02:21:39", True))

    def test_no_answer(self):
        t, r = R.parse_reply(LOG, "diag/ota")
        self.assertIn("link dropped", r["_error"])

    def test_pending_and_absent(self):
        self.assertEqual(R.parse_reply(LOG, "diag/net"), ("2026-11-17T02:23:00", None))
        self.assertEqual(R.parse_reply(LOG, "diag/audio"), ("", None))

    def test_prefix_is_not_a_match(self):
        # `diag` must not take `diag/net`'s line
        self.assertEqual(R.parse_reply(LOG[-1:], "diag"), ("", None))

    def test_bad_json_kept_raw(self):
        t, r = R.parse_reply(["T1 > api info   (x)", "T2 API {broken"], "info")
        self.assertEqual(r, "{broken")


class FakeBench:
    def __init__(self, replies, log=()):
        self.replies, self.asked, self.log = replies, [], list(log)

    def ask(self, cmd, body=None):
        self.asked.append((cmd, body))
        return copy.deepcopy(self.replies.get(cmd, {"error": "no such path", "status": 404}))

    def last(self, cmd):
        return R.parse_reply(self.log, cmd)


class Gather(unittest.TestCase):
    def test_pages_tasks(self):
        p0 = dict(TASKS, n=4, next=2, rows=TASKS["rows"][:2])
        p1 = dict(TASKS, n=4, next=4, rows=TASKS["rows"][2:])
        b = FakeBench({"info": INFO, "state": STATE, "diag": DIAG, "diag/tasks": p0, "diag/tasks/2": p1,
                       "diag/net": NET, "diag/audio": AUDIO, "diag/ota": OTA})
        d = R.gather(b)
        self.assertEqual([r[0] for r in d["diag/tasks"]["rows"]], ["IDLE1", "IDLE0", "hifi_power", "decode"])

    def test_paging_stops_when_next_does_not_advance(self):
        p0 = dict(TASKS, n=4, next=2, rows=TASKS["rows"][:2])
        stuck = dict(TASKS, n=4, **{"from": 2, "next": 2}, rows=TASKS["rows"][2:3])
        b = FakeBench({"diag": DIAG, "diag/tasks": p0, "diag/tasks/2": stuck})
        R.gather(b)
        self.assertEqual([c for c, _ in b.asked].count("diag/tasks/2"), 1)

    def test_page_error_is_kept(self):
        p0 = dict(TASKS, n=4, next=2, rows=TASKS["rows"][:2])
        b = FakeBench({"diag": DIAG, "diag/tasks": p0})
        d = R.gather(b)
        self.assertEqual(d["diag/tasks"]["page_error"]["status"], 404)
        self.assertIn("task list cut short: 404", " ".join(t for _, t in R.worries(d)))

    def test_old_image_stops_at_404_and_reads_selftest_from_log(self):
        b = FakeBench({"info": INFO, "state": STATE}, LOG)
        d = R.gather(b)
        self.assertEqual([c for c, _ in b.asked], ["info", "state", "diag"])
        self.assertEqual(d["selftest_at"], "2026-11-17T02:21:39")

    def test_selftest_and_echo_only_when_asked(self):
        b = FakeBench({"diag": DIAG, "selftest": SELF, "diag/echo": {"kbps": 1}})
        R.gather(b, selftest=True, echo=True)
        self.assertIn(("selftest", {}), b.asked)
        self.assertIn(("diag/echo", {"bytes": 1048576}), b.asked)
        b = FakeBench({"diag": DIAG}, LOG)
        R.gather(b)
        self.assertFalse(any(c in ("selftest", "diag/echo") for c, _ in b.asked))


class Metrics(unittest.TestCase):
    def test_from_diag(self):
        m = R.metrics(full())
        self.assertEqual((m["heap_free"], m["heap_largest"], m["psram_free"]), (31995, 15872, 1204551))
        self.assertEqual((m["rssi"], m["channel"], m["echo_kbps"]), (-43, 11, 2319.1))
        self.assertEqual((m["stack_min"], m["stack_tightest"]), (1008, "hifi_power 1008 B"))
        self.assertEqual(m["cpu_busy_pct"], 10.3)
        self.assertEqual((m["vbus_v"], m["contract_v"], m["ota_write_ms"]), (19.95, 20.0, 19375))
        self.assertEqual(m["selftest"], "pass")
        self.assertNotIn("amp_temp_c", m)

    def test_heap_from_state_when_both(self):
        # an older diag image counted IRAM too; state is the common measure
        d = full()
        d["state"] = STATE
        m = R.metrics(d)
        self.assertEqual((m["heap_free"], m["heap_min"], m["heap_largest"]), (40000, 30000, 15872))
        d["state"] = {"error": "busy", "status": 503}
        self.assertEqual(R.metrics(d)["heap_free"], 31995)

    def test_from_state(self):
        m = R.metrics(old())
        self.assertEqual((m["heap_free"], m["heap_min"], m["rssi"]), (40000, 30000, -48))
        self.assertEqual((m["vbus_v"], m["contract_v"], m["contract_a"]), (19.76, 20.0, 1.5))
        self.assertEqual(m["reset_reason"], "power_on")
        self.assertNotIn("heap_largest", m)


class Worries(unittest.TestCase):
    def test_lite3_only_tight_app_stacks(self):
        # IDLE0/1 at 928 B are ESP-IDF's fixed stacks: not flagged above 256
        w = R.worries(full())
        self.assertEqual(w, [("watch", "task hifi_power has 1008 B of stack never used (under 1024)")])

    def test_system_task_limit(self):
        d = full()
        d["diag/tasks"] = dict(TASKS, rows=[["ipc0", 200, 0, 24, 0.0], ["IDLE0", 448, 0, 0, 90.0]])
        self.assertEqual([t.split()[1] for _, t in R.worries(d)], ["ipc0"])

    def test_each_limit(self):
        d = full()
        d["diag"] = copy.deepcopy(DIAG)
        d["diag"]["heap"]["largest"] = 4000
        d["diag"]["resets"] = "panic,restart"
        d["diag"]["power"]["vbus_v"] = 15.0
        d["diag"]["power"]["amp_faults"] = 2
        d["diag/net"] = copy.deepcopy(NET)
        d["diag/net"]["wifi"].update(rssi=-80, disconnects=3)
        d["diag/audio"] = dict(AUDIO, underruns=5)
        d["selftest"] = {"pass": False, "checks": [{"name": "heap", "pass": False}]}
        w = R.worries(d)
        text = " | ".join(t for _, t in w)
        for want in ("self-test failed: heap", "largest free block is 4000", "VBUS 15.0 V", "faulted 2",
                     "weak Wi-Fi: -80", "dropped 3", "5 audio underruns", "panic"):
            self.assertIn(want, text)
        levels = [l for l, _ in w]
        self.assertEqual(levels, sorted(levels, key=lambda l: l != "bad"))

    def test_watchdog_names(self):
        d = full()
        d["diag"] = copy.deepcopy(DIAG)
        for rs, bad in (("task_watchdog,restart", True), ("interrupt_watchdog", True), ("brownout", True),
                        ("restart,power_on,deep_sleep", False)):
            d["diag"]["resets"] = rs
            got = [t for l, t in R.worries(d) if l == "bad"]
            self.assertEqual(bool(got), bad, rs)
        d["diag"]["resets"] = "task_watchdog,restart,restart"
        self.assertIn("in the last 3 boots", " ".join(t for _, t in R.worries(d)))

    def test_tasks_error(self):
        d = full()
        d["diag/tasks"] = {"error": "busy", "status": 503}
        self.assertIn(("watch", "no task list: 503 busy"), R.worries(d))

    def test_old_image_says_so(self):
        self.assertIn("no `diag`", R.worries(old())[0][1])


class Outliers(unittest.TestCase):
    def test_mad(self):
        o = R.outliers({"a": -45, "b": -47, "c": -44, "d": -46, "e": -75})
        self.assertEqual([(b, v) for b, v, _ in o], [("e", -75)])

    def test_flat_spread_uses_percent(self):
        self.assertEqual([b for b, _, _ in R.outliers({"a": 0, "b": 0, "c": 0})], [])
        self.assertEqual([b for b, _, _ in R.outliers({"a": 20.0, "b": 20.0, "c": 20.0, "d": 12.0})], ["d"])

    def test_small_relative_spread_is_not_an_outlier(self):
        # VBUS 19.76..19.95 with one at 19.70: MAD fires, but 0.3 % is noise
        o = R.outliers({"a": 19.90, "b": 19.91, "c": 19.90, "d": 19.92, "e": 19.70})
        self.assertEqual(o, [])

    def test_zero_median_counts(self):
        self.assertEqual([b for b, _, _ in R.outliers({"a": 0, "b": 0, "c": 0, "d": 2})], ["d"])

    def test_needs_three(self):
        self.assertEqual(R.outliers({"a": 1, "b": 100}), [])


class Writers(unittest.TestCase):
    def test_report_full(self):
        md = R.report("aabbccdd7434", {}, full())
        for want in ("# Hi-Fi Lite 3 (aabbccdd7434)", "healthy, with notes", "| pd_contract | pass |",
                     "largest block 15872", "NVS `nvs`: 101 of 504", "no sensor wired", "channel 11",
                     "2319.1 kbit/s", "19375 ms of it writing flash", "| hifi_power | 1008 ⚠ |"):
            self.assertIn(want, md)

    def test_report_old(self):
        md = R.report("aabbccdd743c", {"name": "Lite 2"}, old())
        self.assertIn("Heap (state): 40000 B free", md)
        self.assertNotIn("## Tasks", md)

    def test_readme_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            raw = Path(t) / "raw"
            raw.mkdir()
            a, b = full(), old()
            a["_name"], b["_name"] = "Lite 3", "Lite 2"
            (raw / "aabbccdd7434-2026-11-16.json").write_text(json.dumps(dict(a, _name="stale")))
            (raw / "aabbccdd7434-2026-11-17.json").write_text(json.dumps(a))
            (raw / "aabbccdd743c-2026-11-17.json").write_text(json.dumps(b))
            latest = R.latest_raw(Path(t))
            self.assertEqual([n for _, n, _ in latest], ["Lite 2", "Lite 3"])
            md = R.readme(latest, "Lite 3 runs the diag image.")
            self.assertIn("## Reading it\n\nLite 3 runs the diag image.", md)
            self.assertIn("[Lite 3](aabbccdd7434-2026-11-17.md)", md)
            self.assertIn("| heap free (B) | 40000 | 31995 | 31995 to 40000 |", md)
            self.assertIn("Lite 2: watch: no `diag`", md)

    def test_notes_fill_from_same_run(self):
        a, b = full(), old()
        latest = [("x", "Hi-Fi Lite 2", b), ("y", "Hi-Fi Lite 3", a)]
        warned = []
        txt = R.fill("{{Lite 3:heap_free}} B, {{Lite 3:heap_free|kb}} KB; low {{min:heap_free}} on "
                     "{{argmin:heap_free}}; others {{max!Lite 3:heap_free}}; {{Lite 9:rssi}} {{Lite 2:nope}}",
                     latest, warned.append)
        self.assertEqual(txt, "31995 B, 31 KB; low 31995 on Hi-Fi Lite 3; others 40000; **?** **?**")
        self.assertEqual(len(warned), 2)
        self.assertEqual(R.fill("{{argmin:heap_min>uptime_s|h}} h", latest), "1.9 h")
        self.assertEqual(R.fill("{{argmax:heap_min>uptime_s}}", latest), "12")
        self.assertEqual(R.fill("{{argmin:heap_min>uptime_s|min}} min", latest), "113 min")
        md = R.readme(latest, "Heap {{Lite 3:heap_free}}.")
        self.assertIn("Heap 31995.", md)


class Boards(unittest.TestCase):
    def test_lease_list(self):
        b = R.parse_leases("You: a5ba2, lane x\n"
                           "aabbccdd7434 Lite 3   owner hifi-lite-characterize: free; yours to flash\n"
                           "aabbccdd73e0 Lite 4   owner hifi-lite-security: leased to aeb53 (s) while run e7 is open:"
                           " run e7: s; not yours to flash\n")
        self.assertEqual(b["aabbccdd7434"], {"name": "Lite 3", "owner": "hifi-lite-characterize", "mine": True})
        self.assertEqual((b["aabbccdd73e0"]["name"], b["aabbccdd73e0"]["mine"]), ("Lite 4", False))
        self.assertEqual(len(b), 2)


if __name__ == "__main__":
    unittest.main()
