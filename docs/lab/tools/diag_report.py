#!/usr/bin/env python3
"""Self-test reports for boards reached over serialtab.

Gathers what a board says about itself (the test API's `info`, `state`,
`selftest` and `diag` calls, docs/test-api.md in the firmware repo) from one
or all remote boards and writes

  reports/<board>-<date>.md        one board: performance and health
  reports/raw/<board>-<date>.json  the replies it was built from
  reports/README.md                every board's latest, side by side: spread,
                                   outliers, anything worrying

Only read-only calls go to a board that isn't leased to your lane: a remote
bench takes bodiless lines from such a lane, so the self-test (a POST) and the
throughput test run only on a board you hold (or with --selftest/--echo).
Otherwise the newest self-test in the board's log is reported, with its time.
A board whose image has no `diag` (404) gets a report from `state` alone.

Generic: nothing here is Lite-only except the thresholds in LIMITS and the
names in the default --lane; another board family passes its own.

  diag_report.py --all                 every board serialtab knows
  diag_report.py --dev aabbccdd7434    one board
  diag_report.py --readme-only         rebuild README.md from reports/raw
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE.parent / "reports"
LOGDIR = Path(os.environ.get("SERIALTAB_REMOTE_DIR", Path.home() / ".h2/serial/remote"))
ME = os.environ.get("H2_NAME", "")

# What counts as worrying. Each: (path in the gathered data, test, words).
LIMITS = {
    "stack_free_min": 1024,  # bytes: a task with less headroom is flagged (and given more)
    # ESP-IDF's own tasks run in small fixed stacks sized by its config: only
    # flagged under this many bytes
    "system_tasks": ("IDLE0", "IDLE1", "ipc0", "ipc1", "esp_timer", "Tmr Svc"),
    "system_stack_free_min": 256,
    "heap_largest_min": 8192,  # internal RAM's largest block, bytes
    "heap_min_min": 16384,  # the least internal heap ever free since boot
    "psram_free_min": 512 * 1024,
    "rssi_weak": -70,  # dBm
    "cpu_busy": 50.0,  # % of a core, any task but IDLE
    "nvs_used_pct": 75.0,
    "rail_tolerance": 0.05,  # VBUS within 5 % of the PD contract
    "pvdd_drop_v": 0.5,  # PVDD more than this below VBUS
    "echo_kbps_min": 500.0,
}
# reset reasons that mean something went wrong (docs/test-api.md, Diagnostics)
CRASHES = {"panic", "interrupt_watchdog", "task_watchdog", "watchdog", "brownout"}

# ------------------------------------------------------------------ the log


def parse_reply(lines, cmd, who=None):
    """The reply to the last `> api <cmd>` in lines (oldest first) sent by
    who (any sender if None): (time, dict or raw str), ("", None) if not there,
    or (time, {"_error": ...}) when the bench says there was no answer."""
    want = f"> api {cmd}"
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        m = re.match(r"^(\S+) (> api \S+)(?: (.*?))?\s{3}\(([^)]*)\)\s*$", ln)
        if not m or m.group(2) != want:
            continue
        if who and m.group(4) != who:
            continue
        for nxt in lines[i + 1:]:
            t = nxt.split(" ", 1)[0]
            if nxt[len(t) + 1:].startswith("API "):
                body = nxt[len(t) + 5:].strip()
                try:
                    return t, json.loads(body)
                except ValueError:
                    return t, body
            if f"--- no answer to {cmd}" in nxt:
                return t, {"_error": nxt.split("--- ", 1)[1]}
        return m.group(1), None
    return "", None


class Bench:
    """Sends a line to a remote board and reads its reply from the log."""

    def __init__(self, dev, lane=None, timeout=25):
        self.dev, self.lane, self.timeout = dev, lane, timeout
        self.log = LOGDIR / f"{dev}.log"

    def lines(self):
        try:
            return self.log.read_text(errors="replace").splitlines()
        except FileNotFoundError:
            return []

    def ask(self, cmd, body=None):
        line = f"api {cmd}" + (f" {json.dumps(json.dumps(body, separators=(',', ':')))}" if body is not None else "")
        start = len(self.lines())
        argv = ["serialtab", "send", "--dev", self.dev, line]
        if self.lane:
            argv[2:2] = ["--lane", self.lane]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode or "queued" not in r.stdout:
            return {"_error": (r.stdout + r.stderr).strip() or "not sent"}
        end = time.time() + self.timeout
        while time.time() < end:
            time.sleep(1)
            t, reply = parse_reply(self.lines()[start:], cmd, ME or None)
            if reply is not None:
                return reply
        return {"_error": f"no reply in {self.timeout} s"}

    def last(self, cmd):
        """The newest reply to cmd in the log, from anyone: (time, reply)."""
        return parse_reply(self.lines(), cmd)


def parse_leases(text):
    """serialtab's lease list -> {id: {name, owner, mine}}; mine: yours to flash."""
    out = {}
    for ln in text.splitlines():
        m = re.match(r"^([0-9a-f]{12})\s+(.+?)\s+owner\s+(\S+):\s+(.*)$", ln)
        if m:
            out[m.group(1)] = {"name": m.group(2), "owner": m.group(3), "mine": bool(re.search(r"(?<!not )yours to flash", m.group(4)))}
    return out


def boards():
    r = subprocess.run(["serialtab", "lease", "list"], capture_output=True, text=True)
    return parse_leases(r.stdout)


# ------------------------------------------------------------------ gathering


def ok(x):
    return isinstance(x, dict) and "_error" not in x and "error" not in x


def gather(b, selftest=False, echo=False):
    """Every reply a report needs, as one dict."""
    d = {"at": dt.datetime.now().isoformat(timespec="seconds")}
    for cmd in ("info", "state", "diag", "diag/tasks", "diag/net", "diag/audio", "diag/ota"):
        d[cmd] = b.ask(cmd)
        if cmd == "diag" and isinstance(d[cmd], dict) and d[cmd].get("status") == 404:
            break  # an image without diagnostics
    t = d.get("diag/tasks")
    if ok(t):
        rows = list(t.get("rows", []))
        prev = t.get("from", 0)
        while t.get("next", t["n"]) < t["n"] and len(rows) < t["n"] and t["next"] > prev:
            prev = t["next"]
            t = b.ask(f"diag/tasks/{t['next']}")
            if not ok(t):
                d["diag/tasks"]["page_error"] = t
                break
            rows += t.get("rows", [])
        d["diag/tasks"]["rows"] = rows
    if selftest:
        d["selftest"] = b.ask("selftest", {})
        d["selftest_at"] = d["at"]
    else:
        d["selftest_at"], d["selftest"] = b.last("selftest")
    if echo:
        d["diag/echo"] = b.ask("diag/echo", {"bytes": 1048576})
    return d


# ------------------------------------------------------------------ checks


def num(x):
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def g(d, *path):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def metrics(d):
    """The figures the comparison uses, from either diag or state."""
    m = {}
    diag, st, net = d.get("diag"), d.get("state"), d.get("diag/net")
    if ok(diag):
        m["uptime_s"] = diag.get("uptime_s")
        m["heap_free"] = g(diag, "heap", "free")
        m["heap_min"] = g(diag, "heap", "min")
        m["heap_largest"] = g(diag, "heap", "largest")
        m["psram_free"] = g(diag, "psram", "free")
        m["boot_app_ms"] = g(diag, "boot_ms", "app")
        m["boot_ip_ms"] = g(diag, "boot_ms", "ip")
        m["boot_link_ms"] = g(diag, "boot_ms", "link")
        m["reset_reason"] = diag.get("reset_reason")
        m["resets"] = diag.get("resets")
        m["nvs_used_pct"] = g(diag, "nvs", "nvs", "used_pct")
        if ok(st) and num(g(st, "sys", "heap_free")):
            # state counts byte-addressable internal RAM; diag did too only
            # from the review fix (an older image's diag adds IRAM), so the
            # one measure every image gives is state's
            m["heap_free"] = g(st, "sys", "heap_free")
            m["heap_min"] = g(st, "sys", "heap_min")
        p = diag.get("power") or {}
        m.update(vbus_v=p.get("vbus_v"), pvdd_v=p.get("pvdd_v"), contract_v=p.get("contract_v"),
                 contract_a=p.get("contract_a"), amp_faults=p.get("amp_faults"), amp_temp_c=p.get("amp_temp_c"))
    elif ok(st):
        m["uptime_s"] = st.get("uptime_s")
        m["heap_free"] = g(st, "sys", "heap_free")
        m["heap_min"] = g(st, "sys", "heap_min")
        m["psram_free"] = g(st, "sys", "psram_free")
        m["reset_reason"] = g(st, "sys", "reset_reason")
        m.update(vbus_v=g(st, "power", "vbus_v"), pvdd_v=g(st, "power", "pvdd_v"),
                 contract_v=g(st, "power", "contract", "v"), contract_a=g(st, "power", "contract", "i_max_a"),
                 amp_faults=g(st, "amp", "faults"), amp_temp_c=g(st, "amp", "temp_c"))
    if ok(net):
        m["rssi"] = g(net, "wifi", "rssi")
        m["channel"] = g(net, "wifi", "channel")
        m["wifi_disconnects"] = g(net, "wifi", "disconnects")
        m["link_drops"] = g(net, "link", "drops")
    elif ok(st):
        m["rssi"] = g(st, "net", "rssi")
    a = d.get("diag/audio")
    if ok(a) and a.get("underruns") is not None:
        m["underruns"] = a.get("underruns")
        m["latency_ms"] = a.get("latency_ms")
    o = d.get("diag/ota")
    if ok(o) and o.get("version"):
        m["ota_write_ms"] = o.get("write_ms")
        m["ota_total_ms"] = o.get("total_ms")
    e = d.get("diag/echo")
    if ok(e):
        m["echo_kbps"] = e.get("kbps")
    t = d.get("diag/tasks")
    if ok(t) and t.get("rows"):
        rows = [r for r in t["rows"] if r[0] not in LIMITS["system_tasks"]] or t["rows"]
        tight = min(rows, key=lambda r: r[1])
        m["stack_tightest"] = f"{tight[0]} {tight[1]} B"
        m["stack_min"] = tight[1]
        idle = [x for x in (t.get("idle_pct") or []) if num(x)]
        if idle:
            m["cpu_busy_pct"] = round(100 - min(idle), 1)
    s = d.get("selftest")
    if isinstance(s, dict) and "pass" in s:
        m["selftest"] = "pass" if s["pass"] else "FAIL"
    if num(m.get("pvdd_v")) and num(m.get("vbus_v")):
        m["pvdd_over_vbus"] = round(m["pvdd_v"] - m["vbus_v"], 2)
    return {k: v for k, v in m.items() if v is not None}


def worries(d, L=LIMITS):
    """Plain-words findings, worst first: (level, text); level 'bad' or 'watch'."""
    out = []
    m = metrics(d)
    s = d.get("selftest")
    if isinstance(s, dict) and s.get("pass") is False:
        bad = [c["name"] for c in s.get("checks", []) if not c.get("pass")]
        out.append(("bad", "self-test failed: " + ", ".join(bad)))
    if not ok(d.get("diag")):
        out.append(("watch", "no `diag` from this image: health from `state` only"))
    t = d.get("diag/tasks")
    if ok(d.get("diag")) and isinstance(t, dict) and not ok(t):
        out.append(("watch", f"no task list: {t.get('status', '')} {t.get('error') or t.get('_error') or ''}".rstrip()))
    elif ok(t) and t.get("page_error"):
        e = t["page_error"]
        out.append(("watch", f"task list cut short: {e.get('status', '')} {e.get('error') or e.get('_error') or ''}".rstrip()))
    if ok(t):
        for r in t.get("rows", []):
            lim = L["system_stack_free_min"] if r[0] in L["system_tasks"] else L["stack_free_min"]
            if r[1] < lim:
                out.append(("watch", f"task {r[0]} has {r[1]} B of stack never used (under {lim})"))
            if num(r[4]) and r[4] > L["cpu_busy"] and not r[0].startswith("IDLE"):
                out.append(("watch", f"task {r[0]} uses {r[4]} % of a core"))
    if num(m.get("heap_largest")) and m["heap_largest"] < L["heap_largest_min"]:
        out.append(("bad", f"internal RAM's largest free block is {m['heap_largest']} B"))
    if num(m.get("heap_min")) and m["heap_min"] < L["heap_min_min"]:
        out.append(("watch", f"internal heap fell to {m['heap_min']} B free since boot"))
    if num(m.get("psram_free")) and m["psram_free"] < L["psram_free_min"]:
        out.append(("watch", f"PSRAM down to {m['psram_free'] // 1024} KB free"))
    if num(m.get("rssi")) and m["rssi"] < L["rssi_weak"]:
        out.append(("watch", f"weak Wi-Fi: {m['rssi']} dBm"))
    if num(m.get("wifi_disconnects")) and m["wifi_disconnects"]:
        out.append(("watch", f"Wi-Fi dropped {m['wifi_disconnects']} times since boot"))
    if num(m.get("underruns")) and m["underruns"]:
        out.append(("watch", f"{m['underruns']} audio underruns since boot"))
    if num(m.get("amp_faults")) and m["amp_faults"]:
        out.append(("bad", f"the amplifier faulted {m['amp_faults']} times since boot"))
    if num(m.get("nvs_used_pct")) and m["nvs_used_pct"] > L["nvs_used_pct"]:
        out.append(("watch", f"NVS {m['nvs_used_pct']} % used"))
    if num(m.get("vbus_v")) and num(m.get("contract_v")) and m["contract_v"]:
        if abs(m["vbus_v"] - m["contract_v"]) > L["rail_tolerance"] * m["contract_v"]:
            out.append(("bad", f"VBUS {m['vbus_v']} V against a {m['contract_v']} V contract"))
    if num(m.get("vbus_v")) and num(m.get("pvdd_v")) and m["vbus_v"] - m["pvdd_v"] > L["pvdd_drop_v"]:
        out.append(("watch", f"PVDD {m['pvdd_v']} V is {m['vbus_v'] - m['pvdd_v']:.2f} V under VBUS"))
    if num(m.get("echo_kbps")) and m["echo_kbps"] < L["echo_kbps_min"]:
        out.append(("watch", f"uplink only {m['echo_kbps']} kbit/s"))
    rs = m.get("resets") or ""
    hist = [x.strip() for x in rs.split(",") if x.strip()]
    if CRASHES & set(hist):
        out.append(("bad", f"crash or brown-out in the last {len(hist)} boots (newest first): {rs}"))
    out.sort(key=lambda x: x[0] != "bad")
    return out


# ------------------------------------------------------------------ writing


def fmt(x, unit=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x}{unit}"


def report(dev, info, d):
    """One board's report, as Markdown."""
    m = metrics(d)
    i = d.get("info") if ok(d.get("info")) else {}
    name = i.get("name") or info.get("name") or dev
    w = worries(d)
    L = [f"# {name} ({dev}): self-test report, {d['at'][:10]}", ""]
    L.append(f"Gathered {d['at']} over the remote bench. Firmware {i.get('fw', '?')} "
             f"({i.get('upstream', '?')}), {i.get('flash_mb', '?')} MB flash, {i.get('psram_mb', '?')} MB PSRAM.")
    L.append("")
    verdict = "worrying" if any(l == "bad" for l, _ in w) else ("healthy, with notes" if w else "healthy")
    L += [f"**Verdict: {verdict}.**", ""]
    for lvl, text in w:
        L.append(f"- {'**' + lvl + '**' if lvl == 'bad' else lvl}: {text}")
    if w:
        L.append("")
    s = d.get("selftest")
    L += ["## Self-test", ""]
    if isinstance(s, dict) and "checks" in s:
        L.append(f"{'Passed' if s.get('pass') else 'FAILED'} ({d.get('selftest_at') or 'time unknown'}).")
        L += ["", "| check | result | value |", "|---|---|---|"]
        for c in s["checks"]:
            L.append(f"| {c.get('name')} | {'pass' if c.get('pass') else '**FAIL**'} | {c.get('value', '')} |")
    else:
        L.append("No self-test reply found.")
    L.append("")
    diag = d.get("diag")
    L += ["## System", ""]
    L.append(f"- Up {fmt(m.get('uptime_s'), ' s')}; last reset: {fmt(m.get('reset_reason'))}"
             + (f"; history (newest first): {m['resets']}" if m.get("resets") else "")
             + (f"; boot {diag.get('boots')}" if ok(diag) else ""))
    if ok(diag):
        L.append(f"- Boot: the app at {fmt(m.get('boot_app_ms'), ' ms')}, an IP at {fmt(m.get('boot_ip_ms'), ' ms')}, "
                 f"the bench link at {fmt(m.get('boot_link_ms'), ' ms')}")
        h, p = diag.get("heap") or {}, diag.get("psram") or {}
        L.append(f"- Internal heap: {fmt(h.get('free'))} B free of {fmt(h.get('total'))}, least ever {fmt(h.get('min'))}, "
                 f"largest block {fmt(h.get('largest'))}")
        L.append(f"- PSRAM: {fmt(p.get('free'))} B free of {fmt(p.get('total'))}, least ever {fmt(p.get('min'))}, "
                 f"largest block {fmt(p.get('largest'))}")
        for k, v in (diag.get("nvs") or {}).items():
            L.append(f"- Flash wear, NVS `{k}`: {v.get('used')} of {v.get('total')} entries used ({v.get('used_pct')} %), "
                     f"{v.get('namespaces')} namespaces")
    else:
        L.append(f"- Heap (state): {fmt(m.get('heap_free'), ' B')} free, least ever {fmt(m.get('heap_min'), ' B')}; "
                 f"PSRAM {fmt(m.get('psram_free'), ' B')} free")
    L.append("")
    L += ["## Power", ""]
    L.append(f"- PD contract {fmt(m.get('contract_v'), ' V')} at {fmt(m.get('contract_a'), ' A')}; "
             f"VBUS {fmt(m.get('vbus_v'), ' V')}, PVDD {fmt(m.get('pvdd_v'), ' V')}")
    L.append(f"- Amplifier faults since boot: {fmt(m.get('amp_faults'))}; temperature: "
             + (fmt(m.get("amp_temp_c"), " °C") if num(m.get("amp_temp_c")) else "no sensor wired"))
    L.append("")
    net = d.get("diag/net")
    L += ["## Network", ""]
    if ok(net):
        wf, lk = net.get("wifi", {}), net.get("link", {})
        L.append(f"- Wi-Fi: {fmt(wf.get('rssi'), ' dBm')}, channel {fmt(wf.get('channel'))}, {fmt(wf.get('phy'))}, "
                 f"AP {fmt(wf.get('bssid'))}; {wf.get('connects')} connects, {wf.get('disconnects')} drops"
                 + (f" (last reason {wf['last_reason']})" if wf.get("last_reason") else "") + f"; up {wf.get('up_s')} s")
        L.append(f"- Bench link: {'up' if lk.get('up') else 'down'} {lk.get('up_s')} s, {lk.get('drops')} drops, "
                 f"{lk.get('tx_bytes')} B sent, {lk.get('rx_bytes')} B received")
    else:
        L.append(f"- Wi-Fi (state): {fmt(m.get('rssi'), ' dBm')}")
    e = d.get("diag/echo")
    if ok(e):
        L.append(f"- Uplink throughput to the bench: {e.get('bytes')} B in {e.get('ms')} ms, {e.get('kbps')} kbit/s")
    L.append("")
    a, o = d.get("diag/audio"), d.get("diag/ota")
    if ok(a) or ok(o):
        L += ["## Audio and updates", ""]
        if ok(a) and a.get("underruns") is not None:
            L.append(f"- Output: {a.get('rate')} Hz, {a.get('underruns')} underruns, latency {a.get('latency_ms')} ms "
                     f"({a.get('buffer_ms')} ms buffered, {a.get('dma_ms')} ms in I2S DMA), {a.get('played_s')} s played")
        if ok(o):
            if o.get("version"):
                L.append(f"- Last update: {o.get('bytes')} B in {o.get('total_ms')} ms ({o.get('kbps')} kbit/s), "
                         f"{o.get('write_ms')} ms of it writing flash ({o.get('write_kbps')} kbit/s), at boot {o.get('boot')}")
            else:
                L.append("- Last update: none recorded since the counter was added")
        L.append("")
    t = d.get("diag/tasks")
    if ok(t) and t.get("rows"):
        L += ["## Tasks", "",
              f"CPU over a {t.get('sample_ms')} ms sample; idle core 0 {fmt(idle(t, 0), ' %')}, "
              f"core 1 {fmt(idle(t, 1), ' %')}. Stack free is the least ever free (high-water mark).", "",
              "| task | stack free (B) | core | prio | CPU % |", "|---|---:|---:|---:|---:|"]
        for r in t["rows"]:
            lim = LIMITS["system_stack_free_min"] if r[0] in LIMITS["system_tasks"] else LIMITS["stack_free_min"]
            flag = " ⚠" if r[1] < lim else ""
            L.append(f"| {r[0]} | {r[1]}{flag} | {fmt(r[2])} | {r[3]} | {fmt(r[4])} |")
        L.append("")
    L.append(f"Raw replies: `raw/{dev}-{d['at'][:10]}.json`.")
    return "\n".join(L) + "\n"


def idle(t, core):
    xs = t.get("idle_pct") or []
    return xs[core] if core < len(xs) else None


COLS = [  # (metric, heading, unit, higher is better: for outliers' wording)
    ("selftest", "self-test", "", None),
    ("uptime_s", "up (s)", "", None),
    ("heap_free", "heap free (B)", "", True),
    ("heap_min", "heap min (B)", "", True),
    ("heap_largest", "largest block (B)", "", True),
    ("psram_free", "PSRAM free (B)", "", True),
    ("stack_min", "tightest app stack (B)", "", True),
    ("cpu_busy_pct", "busiest core (%)", "", False),
    ("rssi", "RSSI (dBm)", "", True),
    ("wifi_disconnects", "Wi-Fi drops", "", False),
    ("link_drops", "link drops", "", False),
    ("echo_kbps", "uplink (kbit/s)", "", True),
    ("boot_link_ms", "boot to link (ms)", "", False),
    ("ota_write_ms", "OTA flash write (ms)", "", False),
    ("underruns", "underruns", "", False),
    ("latency_ms", "latency at gather (ms)", "", False),
    ("contract_a", "PD contract (A)", "", None),
    ("vbus_v", "VBUS (V)", "", None),
    ("pvdd_v", "PVDD (V)", "", None),
    ("amp_faults", "amp faults", "", False),
    ("nvs_used_pct", "NVS used (%)", "", False),
]


def outliers(vals):
    """{board: value} -> boards more than 3 scaled MADs and 5 % (or 20 % when
    the MAD is 0) from the median; with a median of 0 (a count most boards
    read 0), any board off it. Needs at least 3 boards."""
    xs = [v for v in vals.values() if num(v)]
    if len(xs) < 3:
        return []
    med = statistics.median(xs)
    mad = statistics.median([abs(x - med) for x in xs]) * 1.4826
    out = []
    for b, v in vals.items():
        if not num(v):
            continue
        dev = abs(v - med)
        if not med:
            far = dev > 0
        elif mad:
            far = dev > 3 * mad and dev > 0.05 * abs(med)
        else:
            far = dev > 0.2 * abs(med)
        if far:
            out.append((b, v, med))
    return out


FILLS = {  # {{...|name}} conversions for the notes
    "kb": lambda v: f"{v / 1024:.0f}",
    "kb1": lambda v: f"{v / 1024:.1f}",
    "h": lambda v: f"{v / 3600:.1f}",
    "min": lambda v: f"{v / 60:.0f}",
    "mbit": lambda v: f"{v / 1000:.1f}",
    "s": lambda v: f"{v / 1000:.1f}",
    "pct": lambda v: f"{v:.0f}",
}


def fill(notes, latest, warn=None):
    """Put figures from the same raw files as the table into the notes, so
    the text can't drift from it. `{{SEL:KEY|CONV}}`: SEL is a board (its
    name or the end of it, "Lite 3"), or min, max, argmin, argmax over the
    boards that have KEY, optionally `!Lite 3` to leave a board out. KEY is a
    metrics() key; CONV is one of FILLS. `KEY>OTHER` with min, max, argmin or
    argmax picks the board by KEY and gives its OTHER (`argmin:heap_min>uptime_s`
    is the uptime of the board with the least heap_min). A figure that isn't there shows as
    **?** and is reported through warn."""
    ms = [(name, metrics(d)) for _, name, d in latest]

    def pick(sel):
        sel, _, skip = sel.partition("!")
        skip = skip.strip()
        rows = [(n, m) for n, m in ms if not (skip and (n == skip or n.endswith(" " + skip)))]
        return sel.strip(), rows

    def one(mo):
        body = mo.group(1)
        conv = None
        if "|" in body:
            body, conv = (x.strip() for x in body.rsplit("|", 1))
        sel, _, key = body.rpartition(":")
        sel, rows = pick(sel)
        key, _, other = (x.strip() for x in key.partition(">"))
        val = None
        if sel in ("min", "max", "argmin", "argmax"):
            have = [(m[key], n, m) for n, m in rows if num(m.get(key))]
            if have:
                v, n, m = (min if sel.endswith("min") else max)(have, key=lambda x: x[0])
                val = m.get(other) if other else n if sel.startswith("arg") else v
        else:
            for n, m in rows:
                if n == sel or n.endswith(" " + sel):
                    val = m.get(key)
                    break
        if val is None or (conv and conv not in FILLS):
            if warn:
                warn(f"notes: no figure for {{{{{mo.group(1)}}}}}")
            return "**?**"
        if conv and num(val):
            return FILLS[conv](val)
        return str(val)

    return re.sub(r"\{\{([^{}]+)\}\}", one, notes)


def readme(latest, notes=""):
    """latest: [(dev, name, data)] -> README.md comparing them; notes: a
    hand-written reading of it (reports/NOTES.md), put first."""
    L = ["# Self-test reports", "",
         "One report per board per day, from `tools/diag_report.py` (on-board `api diag`, `state` and the self-test, "
         "over the remote bench). This page compares each board's latest. Rebuild it with "
         "`tools/diag_report.py --readme-only`.", ""]
    if notes.strip():
        L += ["## Reading it", "", fill(notes, latest, lambda w: print(w, file=sys.stderr)).strip(), ""]
    names = {dev: name for dev, name, _ in latest}
    ms = {dev: metrics(d) for dev, _, d in latest}
    cols = [c for c in COLS if any(c[0] in m for m in ms.values())]
    L += ["## Side by side", ""]
    L.append("| | " + " | ".join(f"[{names[dev]}]({dev}-{d['at'][:10]}.md)" for dev, _, d in latest) + " | spread |")
    L.append("|---|" + "---:|" * (len(latest) + 1))
    for key, head, unit, _ in cols:
        vals = [ms[dev].get(key) for dev, _, _ in latest]
        nums = [v for v in vals if num(v)]
        spread = f"{min(nums)} to {max(nums)}" if len(nums) > 1 else ""
        L.append(f"| {head} | " + " | ".join(fmt(v) for v in vals) + f" | {spread} |")
    L.append("")
    L += ["## Outliers", ""]
    found = False
    for key, head, unit, better in cols:
        if key in ("uptime_s", "selftest"):
            continue
        for dev, v, med in outliers({dev: ms[dev].get(key) for dev, _, _ in latest}):
            word = ""
            if better is not None:
                word = " (better)" if (v > med) == better else " (worse)"
            L.append(f"- {names[dev]}: {head} {v} against a median of {med}{word}")
            found = True
    if not found:
        L.append("- None: every figure is within the others' spread.")
    L.append("")
    L += ["## Worrying", ""]
    any_w = False
    for dev, name, d in latest:
        for lvl, text in worries(d):
            L.append(f"- {name}: {'**' + lvl + '**' if lvl == 'bad' else lvl}: {text}")
            any_w = True
    if not any_w:
        L.append("- Nothing.")
    L.append("")
    return "\n".join(L)


def latest_raw(reports=REPORTS):
    """Each board's newest raw file: [(dev, name, data)]."""
    best = {}
    for f in sorted((reports / "raw").glob("*.json")):
        dev = f.stem.rsplit("-", 3)[0]
        best[dev] = f
    out = []
    for dev, f in sorted(best.items()):
        d = json.loads(f.read_text())
        name = d.get("_name") or g(d, "info", "name") or dev
        out.append((dev, name, d))
    out.sort(key=lambda x: x[1])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", action="append", default=[], help="a board's id (repeatable)")
    ap.add_argument("--all", action="store_true", help="every board serialtab knows")
    ap.add_argument("--lane", default=os.environ.get("H2_LANE"), help="your lab lane ($H2_LANE)")
    ap.add_argument("--selftest", action="store_true", help="run the self-test even on a board you don't hold")
    ap.add_argument("--echo", action="store_true", help="time 1 MB up the bench link even on a board you don't hold")
    ap.add_argument("--reports", default=str(REPORTS), help="where reports go")
    ap.add_argument("--readme-only", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.reports)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    if not a.readme_only:
        known = boards()
        devs = list(known) if a.all else a.dev
        if not devs:
            ap.error("--dev or --all")
        for dev in devs:
            info = known.get(dev, {})
            mine = info.get("mine", False)
            print(f"{dev} {info.get('name', '')}: gathering", file=sys.stderr)
            d = gather(Bench(dev, a.lane), selftest=a.selftest or mine, echo=a.echo or mine)
            d["_name"] = (d["info"].get("name") if ok(d.get("info")) else None) or info.get("name") or dev
            day = d["at"][:10]
            (out / "raw" / f"{dev}-{day}.json").write_text(json.dumps(d, indent=1) + "\n")
            (out / f"{dev}-{day}.md").write_text(report(dev, info, d))
            print(f"  wrote {out / f'{dev}-{day}.md'}", file=sys.stderr)
    nf = out / "NOTES.md"
    (out / "README.md").write_text(readme(latest_raw(out), nf.read_text() if nf.exists() else ""))
    print(f"wrote {out / 'README.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
