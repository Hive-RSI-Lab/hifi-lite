#!/usr/bin/env python3
"""Host tests for scan_board.py: which boards it may scan and how it judges a lanscan result against the
listener allow-list. Run: python3 tools/test_scan_board.py"""
import json
import tempfile
import unittest
from pathlib import Path

import scan_board as sb


def diag(dev, ip, board="hi-fi Lite", mode="sta", mac=None):
    m = mac if mac is not None else ":".join(dev[i:i + 2] for i in range(0, 12, 2))
    return {"dev": dev, "name": f"Lite {dev[-2:]}", "board": board, "ip": ip,
            "pages": {"info": {"mac": m, "fw": "0.1.0"}, "state": {"net": {"mode": mode, "ip": ip}}}}


def done(**kw):
    r = {"target": "192.168.2.222", "phase": "done", "rate": 300, "s": 460, "sent": 133000, "heap_min": 9000,
         "tcp": {"ports": 65535, "open": [80, 5000], "closed": 65533, "silent": []},
         "udp": {"ports": 1100, "answered": [5353], "closed": 1097, "silent": [68, 5353]},
         "heard": ["5353>224.0.0.251:5353 x2", "5353>192.168.2.219:49200 x1"]}
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = {**r[k], **v}
        else:
            r[k] = v
    return r


class Boards(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aeb53-scantest-"))
        for dev, ip in (("aabbccdd742c", "192.168.2.219"), ("aabbccdd73e0", "192.168.2.222")):
            (self.d / f"{dev}.json").write_text(json.dumps(diag(dev, ip)))

    def test_pair_from_diag(self):
        a, b = sb.pair("aabbccdd742c", "aabbccdd73e0", self.d)
        self.assertEqual(b["ip"], "192.168.2.222")
        self.assertEqual(b["mac"], "aabbccdd73e0")
        self.assertFalse(b["ap_on"])

    def test_unknown_device_refused(self):
        with self.assertRaises(SystemExit):
            sb.board("aabbccddeeff", self.d)

    def test_not_a_mac_refused(self):
        for bad in ("192.168.2.1", "../aabbccdd742c", "", None):
            with self.assertRaises(SystemExit):
                sb.board(bad, self.d)

    def test_other_board_kinds_refused(self):
        (self.d / "112233445566.json").write_text(json.dumps(diag("112233445566", "192.168.2.50", board="voice")))
        with self.assertRaises(SystemExit):
            sb.board("112233445566", self.d)

    def test_mac_must_match_name(self):
        (self.d / "112233445566.json").write_text(json.dumps(diag("112233445566", "192.168.2.50", mac="aa:bb:cc:dd:ee:ff")))
        with self.assertRaises(SystemExit):
            sb.board("112233445566", self.d)

    def test_self_and_other_subnet_refused(self):
        with self.assertRaises(SystemExit):
            sb.pair("aabbccdd742c", "aabbccdd742c", self.d)
        (self.d / "aabbccdd7400.json").write_text(json.dumps(diag("aabbccdd7400", "192.168.3.9")))
        with self.assertRaises(SystemExit):
            sb.pair("aabbccdd742c", "aabbccdd7400", self.d)

    def test_ap_mode_seen(self):
        (self.d / "aabbccdd7400.json").write_text(json.dumps(diag("aabbccdd7400", "192.168.2.223", mode="apsta")))
        self.assertTrue(sb.board("aabbccdd7400", self.d)["ap_on"])


class Judge(unittest.TestCase):
    def test_clean_board_passes(self):
        v = sb.judge(done())
        self.assertEqual(v["verdict"], "pass", v)
        self.assertEqual(v["suspect"], {"tcp": [], "udp": []})

    def test_extra_tcp_fails(self):
        v = sb.judge(done(tcp={"open": [23, 80, 5000]}))
        self.assertEqual(v["verdict"], "fail")
        self.assertIn("TCP 23 open", v["bad"])

    def test_more_open_than_listed_fails(self):
        self.assertEqual(sb.judge(done(tcp={"open_more": 3}))["verdict"], "fail")

    def test_udp_answer_outside_allowlist_fails(self):
        v = sb.judge(done(udp={"answered": [1900]}))
        self.assertEqual(v["verdict"], "fail")

    def test_ap_ports_only_with_ap(self):
        r = done(udp={"answered": [53, 67]})
        self.assertEqual(sb.judge(r, ap_on=False)["verdict"], "fail")
        self.assertEqual(sb.judge(r, ap_on=True)["verdict"], "pass")

    def test_silent_udp_is_suspect_not_bad(self):
        v = sb.judge(done(udp={"silent": [68, 5353, 3483]}))
        self.assertEqual(v["verdict"], "pass")
        self.assertEqual(v["suspect"]["udp"], [3483])

    def test_silent_tcp_is_suspect(self):
        v = sb.judge(done(tcp={"silent": [8080]}))
        self.assertEqual(v["suspect"]["tcp"], [8080])

    def test_too_many_silent_fails(self):
        self.assertEqual(sb.judge(done(tcp={"silent": [1] * 8, "silent_more": 400}))["verdict"], "fail")
        self.assertEqual(sb.judge(done(udp={"silent": [1] * 16, "silent_more": 1}))["verdict"], "fail")

    def test_ssdp_or_llmnr_announcement_fails(self):
        for h in ("1900>239.255.255.250:1900 x4", "5355>224.0.0.252:5355 x1"):
            v = sb.judge(done(heard=[h]))
            self.assertEqual(v["verdict"], "fail", h)

    def test_client_query_is_a_note(self):
        v = sb.judge(done(heard=["50000>239.255.255.250:1900 x1"]))
        self.assertEqual(v["verdict"], "pass")
        self.assertTrue(any("client query" in n for n in v["notes"]))

    def test_heard_overflow_and_garbage_fail(self):
        self.assertEqual(sb.judge(done(heard_lost=2))["verdict"], "fail")
        self.assertEqual(sb.judge(done(heard=["nonsense"]))["verdict"], "fail")

    def test_unfinished_or_wrong_mac_incomplete(self):
        self.assertEqual(sb.judge(done(phase="tcp"))["verdict"], "incomplete")
        self.assertEqual(sb.judge(done(phase="failed", error="ARP: no answer"))["verdict"], "incomplete")
        self.assertEqual(sb.judge(done(target_mac="aabbccddeeff"))["verdict"], "incomplete")


class Recheck(unittest.TestCase):
    def test_silent_twice_fails(self):
        first = sb.judge(done(udp={"silent": [3483]}))
        again = sb.judge(done(tcp={"ports": 1, "open": [80], "closed": 0}, udp={"ports": 1, "silent": [3483]}, heard=[]))
        v = sb.merge_recheck(first, again)
        self.assertEqual(v["verdict"], "fail")
        self.assertTrue(any("UDP 3483 silent twice" in b for b in v["bad"]))

    def test_lost_once_passes(self):
        first = sb.judge(done(tcp={"silent": [8080]}, udp={"silent": [3483]}))
        again = sb.judge(done(tcp={"ports": 1, "open": [], "closed": 1}, udp={"ports": 1, "silent": []}, heard=[]))
        v = sb.merge_recheck(first, again)
        self.assertEqual(v["verdict"], "pass", v)

    def test_unlisted_silent_ports_unchecked_not_bad(self):
        first = sb.judge(done(tcp={"silent": [845, 846], "silent_more": 85}))
        self.assertEqual(first["verdict"], "fail")
        again = sb.judge(done(tcp={"ports": 2, "open": [], "closed": 2}, udp={"ports": 0, "silent": []}, heard=[]))
        v = sb.merge_recheck(first, again)
        self.assertEqual(v["verdict"], "incomplete", v)
        self.assertEqual(v["bad"], [])
        self.assertTrue(any("85 more" in n for n in v["notes"]))

    def test_unlisted_with_one_still_silent_fails(self):
        first = sb.judge(done(tcp={"silent": [845, 846], "silent_more": 85}))
        again = sb.judge(done(tcp={"ports": 2, "open": [], "closed": 1, "silent": [846]}, udp={"ports": 0, "silent": []},
                              heard=[]))
        v = sb.merge_recheck(first, again)
        self.assertEqual(v["verdict"], "fail")
        self.assertTrue(any(b.endswith(sb.UNLISTED) for b in v["bad"]))

    def test_recheck_clears_other_bad_too(self):
        first = sb.judge(done(tcp={"open": [23, 80, 5000], "silent": [845]}))
        again = sb.judge(done(tcp={"ports": 1, "open": [], "closed": 1}, udp={"ports": 0, "silent": []}, heard=[]))
        self.assertEqual(sb.merge_recheck(first, again)["verdict"], "fail")

    def test_recheck_unfinished(self):
        first = sb.judge(done(udp={"silent": [3483]}))
        v = sb.merge_recheck(first, sb.judge(done(phase="stopped")))
        self.assertEqual(v["verdict"], "incomplete")


class Sizing(unittest.TestCase):
    def test_count_and_estimate(self):
        self.assertEqual(sb.count_ports("1-65535"), 65535)
        self.assertEqual(sb.count_ports("80,5000-5010"), 12)
        self.assertEqual(sb.estimate_s(65535, 1100, 300, 20), 2 * 66635 // 300 + 35)


if __name__ == "__main__":
    unittest.main()
