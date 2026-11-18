#!/usr/bin/env python3
"""Soak a board for hours under load and log its health, to chart drift.

Every cycle (default 2 min) the board plays the next test tone in a rotation
(1 kHz sine, pink noise, a 20 Hz to 20 kHz sweep, a 100 Hz sine). Every few
cycles it skips: the tone stops, the next starts at once, and a `next`
button press goes to whatever streams. Each cycle reads `diag`, `diag/net`
and `diag/audio`, and every 5th also `diag/tasks` (all pages). Every reading
goes to lab/data/soak-<start>.jsonl as one line; `--chart FILE` draws heap,
stacks, rails, RSSI, underruns and drops from such a file.

The speakers are on the board, so the amp is held off (`api amp
{"enable":false}`) and the tones run with `force` through the whole digital
path: the decoder, the governor, I2S and DMA, with PVDD up. By day (08 to 22
local) one cycle in every --loud-every plays with the amp on at --loud-dbfs,
to log the rails under amp load. The amp is always let run again at the end,
on Ctrl-C or a kill (SIGTERM).

A cycle whose uptime went back logs `rebooted`, the reset reason and, for a
software restart, who called it (`restart_cause`); `--summary` lists them.

There's no temperature sensor on the Lite (amp temp_c is null) and no safe
forced Wi-Fi reconnect over the bench (rejoining needs the network password),
so the natural Wi-Fi and link drops are counted instead. Each cycle also logs
when the bench endpoint (the user unit BENCH_UNIT) last started. A link drop
in a cycle where the endpoint restarted, with no Wi-Fi drop to go with it, is
the endpoint's and not the board's: `--chart` and `--summary` leave it out
and list it under bench_restarts. A hand-written `<log>.notes.jsonl` beside
the log does the same for anything else (see notes()).

  soak.py --dev aabbccdd7434 --hours 12
  soak.py --chart lab/data/soak-2026-11-17T0750.jsonl
"""
import argparse
import datetime as dt
import json
import os
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import diag_report as R  # noqa: E402

DATA = HERE.parent / "lab" / "data"
BENCH_UNIT = os.environ.get("SOAK_BENCH_UNIT", "h2-bench-dev")
TONES = [
    {"type": "sine", "freq_hz": 1000},
    {"type": "noise"},
    {"type": "sweep", "start_hz": 20, "end_hz": 20000},
    {"type": "sine", "freq_hz": 100},
]


def tone_body(i, seconds, dbfs, amp_on):
    t = dict(TONES[i % len(TONES)], level_dbfs=dbfs, duration_s=seconds)
    if not amp_on:
        t["force"] = True  # plays with the amp held off
    return t


def loud_cycle(n, now, every, day=(8, 22)):
    """Whether cycle n plays out loud: by day, one cycle in `every`."""
    return bool(every) and day[0] <= now.hour < day[1] and n % every == 0


def tasks_all(bench):
    """Every page of diag/tasks, as one reply (paged as diag_report.gather does)."""
    t = bench.ask("diag/tasks")
    if not R.ok(t):
        return t
    rows = list(t.get("rows", []))
    while t.get("next", t["n"]) < t["n"] and len(rows) < t["n"]:
        t2 = bench.ask(f"diag/tasks/{t['next']}")
        if not R.ok(t2):
            break
        rows += t2.get("rows", [])
        t = dict(t2, idle_pct=t.get("idle_pct"))
    return dict(t, rows=rows)


def sample(bench, n, tasks_every):
    s = {}
    for cmd in ("diag", "diag/net", "diag/audio"):
        s[cmd] = bench.ask(cmd)
    if tasks_every and n % tasks_every == 0:
        s["diag/tasks"] = tasks_all(bench)
    return s


def restart_cause(bench, diag):
    """Who restarted the board, when it was a software restart (firmware from
    hifi-receiver #222: `<note> by <task> at 0x<pc> after <n> s`). diag leaves
    it out when its page is full; state/sys always carries it."""
    diag = diag or {}
    if diag.get("reset_reason") != "restart":
        return None
    if isinstance(diag.get("restart_cause"), str):
        return diag["restart_cause"]
    r = bench.ask("state/sys")
    c = r.get("restart_cause") if isinstance(r, dict) else None
    if c is None and isinstance(r, dict) and isinstance(r.get("sys"), dict):
        c = r["sys"].get("restart_cause")
    return c if isinstance(c, str) else "not reported (firmware before #222)"


def bench_since(unit=BENCH_UNIT):
    """When the bench endpoint's unit last started (ISO), or None."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestampMonotonic"],
                           capture_output=True, text=True, timeout=10)
        mono = int(r.stdout.strip().partition("=")[2] or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not mono:
        return None
    t = time.time() - (time.monotonic() - mono / 1e6)
    return dt.datetime.fromtimestamp(round(t)).astimezone().isoformat(timespec="seconds")


def flat(s):
    """The figures charted, from one sample (the same names as diag_report.metrics)."""
    m = R.metrics(s)
    a = s.get("diag/audio") or {}
    if R.ok(a) and a.get("played_s") is not None:
        m["played_s"] = a["played_s"]
    t = s.get("diag/tasks") or {}
    if R.ok(t) and t.get("rows"):
        m["stacks"] = {r[0]: r[1] for r in t["rows"]}
    m.pop("stack_tightest", None)
    w = (s.get("diag/net") or {}).get("wifi") or {}
    if w.get("last_reason") is not None:
        m["wifi_reason"] = w["last_reason"]
    return m


def run(a):
    bench = R.Bench(a.dev, a.lane, timeout=30)
    start = dt.datetime.now()
    path = Path(a.out) if a.out else DATA / f"soak-{start:%Y-%m-%dT%H%M}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"soak on {a.dev} for {a.hours} h, logging to {path}", file=sys.stderr)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    amp_on = None
    last_up = None
    n = 0

    def log(rec):
        rec["at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def set_amp(on):
        nonlocal amp_on
        if amp_on is not on:
            r = bench.ask("amp", {"enable": on})
            amp_on = on if R.ok(r) else None
            log({"event": "amp", "enable": on, "reply": r})

    log({"event": "start", "dev": a.dev, "hours": a.hours, "cycle_s": a.cycle, "dbfs": a.dbfs,
         "loud_dbfs": a.loud_dbfs, "loud_every": a.loud_every})
    try:
        end = time.time() + a.hours * 3600
        while time.time() < end and not stop["now"]:
            t0 = time.time()
            loud = loud_cycle(n, dt.datetime.now(), a.loud_every)
            set_amp(loud)
            skip = a.skip_every and n % a.skip_every == a.skip_every - 1
            secs = max(5, a.cycle - 10)
            body = tone_body(n, secs, a.loud_dbfs if loud else a.dbfs, loud)
            r = bench.ask("tone", body)
            rec = {"n": n, "tone": body["type"], "loud": loud, "tone_ok": R.ok(r)}
            if not R.ok(r):
                rec["tone_reply"] = r
            if skip:
                time.sleep(min(20, secs / 2))
                rec["skip"] = {"stop": R.ok(bench.ask("tone", {"type": "stop"})),
                               "tone": R.ok(bench.ask("tone", tone_body(n + 1, secs - 25, a.dbfs, loud))),
                               "next": R.ok(bench.ask("control", {"action": "next"}))}
            s = sample(bench, n, a.tasks_every)
            rec["errors"] = {k: v.get("_error") or v.get("error") for k, v in s.items() if not R.ok(v)}
            if not rec["errors"]:
                del rec["errors"]
            rec.update(flat(s))
            rec["bench_since"] = bench_since()
            up = rec.get("uptime_s")
            if R.num(up) and last_up is not None and up < last_up:
                rec["rebooted"] = True
                rec["reset_reason"] = (s.get("diag") or {}).get("reset_reason")
                rec["restart_cause"] = restart_cause(bench, s.get("diag"))
            if R.num(up):
                last_up = up
            log(rec)
            print(f"{n}: {rec.get('tone')} heap {rec.get('heap_free')} rssi {rec.get('rssi')} "
                  f"pvdd {rec.get('pvdd_v')} {'skip ' if skip else ''}{'LOUD ' if loud else ''}"
                  f"{'errors ' + str(rec['errors']) if 'errors' in rec else ''}"
                  f"{' REBOOTED (' + str(rec.get('reset_reason')) + (': ' + rec['restart_cause'] if rec.get('restart_cause') else '') + ')' if rec.get('rebooted') else ''}",
                  file=sys.stderr, flush=True)
            n += 1
            while time.time() - t0 < a.cycle and not stop["now"] and time.time() < end:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bench.ask("tone", {"type": "stop"})
        r = bench.ask("amp", {"enable": True})
        log({"event": "end", "cycles": n, "amp_restored": R.ok(r)})
        print(f"done: {n} cycles; amp let run again: {R.ok(r)}", file=sys.stderr)
    return path


def notes_path(path):
    return Path(path).with_suffix(".notes.jsonl")


def notes(path):
    """Hand-written notes beside a soak log (`<log>.notes.jsonl`), one per line:
    {"at": ISO time, "note": "...", "label": "chart tag", "exclude": {"link_drops": 1}}. `exclude`
    takes counts off every sample from `at` on, for drops the board didn't
    cause (the bench endpoint restarting, say)."""
    p = notes_path(path)
    out = []
    if p.exists():
        for ln in p.read_text().splitlines():
            if ln.strip():
                rec = json.loads(ln)
                rec["t"] = dt.datetime.fromisoformat(rec["at"])
                out.append(rec)
    return out


def bench_restarts(samples):
    """Endpoint restarts seen in the samples' bench_since, as notes: the rise
    in link drops over the cycle the restart fell in, less any rise in Wi-Fi
    drops (a board that left Wi-Fi drops its link too), is taken off."""
    out = []
    for prev, s in zip(samples, samples[1:]):
        b, pb = s.get("bench_since"), prev.get("bench_since")
        if not b or not pb or b == pb:
            continue
        def rise(k):  # on the counts as logged, before any exclusion
            v, pv = s.get(k + "_raw", s.get(k)), prev.get(k + "_raw", prev.get(k))
            return v - pv if R.num(v) and R.num(pv) else 0
        by = max(0, rise("link_drops") - max(0, rise("wifi_disconnects")))
        out.append({"at": b, "t": dt.datetime.fromisoformat(b), "note": "bench endpoint restarted", "label": "bench restart",
                    "exclude": {"link_drops": by} if by else {}})
    return out


def series(path):
    """jsonl -> (samples, events), with the notes' exclusions applied (the
    count as logged is kept as KEY_raw)."""
    samples, events = [], []
    for ln in Path(path).read_text().splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln)
        rec["t"] = dt.datetime.fromisoformat(rec["at"])
        (events if "event" in rec else samples).append(rec)
    for n in notes(path) + bench_restarts(samples):
        for k, by in (n.get("exclude") or {}).items():
            for s in samples:
                if s["t"] >= n["t"] and R.num(s.get(k)):
                    s.setdefault(k + "_raw", s[k])
                    s[k] -= by
    return samples, events


def chart(path, png=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    samples, _ = series(path)
    if not samples:
        sys.exit("no samples")
    png = Path(png or Path(path).with_suffix(".png"))
    ink, muted, grid = "#1f2328", "#59636e", "#d1d9e0"
    c1, c2, c3 = "#2f6fdf", "#d4570a", "#1a7f37"
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": grid, "axes.labelcolor": muted,
                         "xtick.color": muted, "ytick.color": muted, "text.color": ink,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(5, 1, figsize=(10, 11), sharex=True, constrained_layout=True)
    t = [s["t"] for s in samples]

    def line(a, key, color, label, scale=1, ls="-"):
        pts = [(s["t"], s[key] / scale) for s in samples if R.num(s.get(key))]
        if pts:
            a.plot(*zip(*pts), color=color, lw=1.4, label=label, ls=ls, zorder=3 if ls != "-" else 2)
        return pts

    a = ax[0]
    line(a, "heap_free", c1, "free", 1024)
    line(a, "heap_min", c2, "lowest since boot", 1024)
    line(a, "heap_largest", c3, "largest block", 1024)
    a.set_ylabel("internal heap (KiB)")
    a.set_title("Internal heap", loc="left", fontsize=10, color=ink)

    a = ax[1]
    stacks = [s for s in samples if s.get("stacks")]
    if stacks:
        last = stacks[-1]["stacks"]
        skip = set(R.LIMITS["system_tasks"])
        worst = sorted((v, k) for k, v in last.items() if k not in skip)[:3]
        for (v, k), c in zip(worst, (c1, c2, c3)):
            pts = [(s["t"], s["stacks"][k]) for s in stacks if R.num(s["stacks"].get(k))]
            a.plot(*zip(*pts), color=c, lw=1.4, marker="o", ms=2.5, label=k)
        a.axhline(512, color=muted, lw=0.8, ls="--")
        a.annotate("512 B: raise the stack", (t[0], 512), xytext=(2, 3), textcoords="offset points",
                   color=muted, fontsize=8)
    a.set_ylabel("stack never used (B)")
    a.set_title("Tightest app stacks", loc="left", fontsize=10, color=ink)

    a = ax[2]
    line(a, "vbus_v", c1, "VBUS")
    line(a, "pvdd_v", c2, "PVDD")
    loud = [s["t"] for s in samples if s.get("loud")]
    for x in loud:
        a.axvline(x, color=c3, lw=0.6, alpha=0.4)
    a.set_ylabel("V")
    a.set_title("Power rails" + (" (green ticks: amp on)" if loud else ""), loc="left", fontsize=10, color=ink)

    a = ax[3]
    line(a, "rssi", c1, "RSSI")
    a.set_ylabel("dBm")
    a.set_title("Wi-Fi signal", loc="left", fontsize=10, color=ink)

    a = ax[4]
    line(a, "wifi_disconnects", c1, "Wi-Fi drops", ls="--")
    line(a, "link_drops", c2, "link drops")
    line(a, "underruns", c3, "underruns")
    boots = [s["t"] for s in samples if s.get("rebooted")]
    for x in boots:
        a.axvline(x, color="#cf222e", lw=1)
    a.set_ylabel("count since boot")
    a.set_title("Drops and underruns" + (" (red: reboot)" if boots else ""), loc="left", fontsize=10, color=ink)
    marks = notes(path) + bench_restarts(samples)
    for n in marks:
        a.axvline(n["t"], color=muted, lw=0.8, ls=":")
        a.annotate(n.get("label") or "note", (n["t"], 0.8),
                   xycoords=("data", "axes fraction"), xytext=(3, -10), textcoords="offset points",
                   fontsize=7, color=muted)
    if any(n.get("exclude") for n in marks):
        a.set_title(a.get_title(loc="left") + " (dotted: a drop not caused by the board, left out)",
                    loc="left", fontsize=10, color=ink)
    tz = samples[0]["t"].tzinfo
    a.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    a.set_xlabel(f"local time (UTC{samples[0]['t']:%z})", fontsize=8, color=muted)

    for a in ax:
        a.grid(axis="y", color=grid, lw=0.6)
        if a.get_legend_handles_labels()[0]:
            a.legend(frameon=False, loc="upper left", ncols=3, fontsize=8)
    fig.suptitle(f"Soak: {samples[0]['t']:%Y-%m-%d %H:%M} to {samples[-1]['t']:%H:%M}, {len(samples)} cycles",
                 x=0.01, ha="left", fontsize=11, color=ink)
    fig.savefig(png, dpi=130)
    print(png)
    return png


def summary(path):
    """Plain figures for a post: first and last of each, and the range."""
    samples, events = series(path)
    out = {"cycles": len(samples),
           "hours": round((samples[-1]["t"] - samples[0]["t"]).total_seconds() / 3600, 2) if samples else 0,
           "reboots": sum(1 for s in samples if s.get("rebooted")),
           "restarts": [{"at": s.get("at"), "reset_reason": s.get("reset_reason"), "restart_cause": s.get("restart_cause")}
                        for s in samples if s.get("rebooted")],
           "tone_failures": sum(1 for s in samples if not s.get("tone_ok")),
           "read_errors": sum(1 for s in samples if s.get("errors")),
           "notes": [{"at": n["at"], "note": n.get("note"), "exclude": n.get("exclude")} for n in notes(path)],
           "bench_restarts": [{"at": n["at"], "link_drops_left_out": (n["exclude"] or {}).get("link_drops", 0)}
                              for n in bench_restarts(samples)]}
    for k in ("heap_free", "heap_min", "heap_largest", "vbus_v", "pvdd_v", "rssi", "wifi_disconnects", "link_drops",
              "underruns", "cpu_busy_pct"):
        v = [s[k] for s in samples if R.num(s.get(k))]
        if v:
            out[k] = {"first": v[0], "last": v[-1], "min": min(v), "max": max(v)}
    st = [s["stacks"] for s in samples if s.get("stacks")]
    if st:
        skip = set(R.LIMITS["system_tasks"])
        out["stack_min"] = {k: min(x.get(k, 1 << 30) for x in st) for k in st[-1] if k not in skip}
        out["stack_min"] = dict(sorted(out["stack_min"].items(), key=lambda kv: kv[1])[:5])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev")
    ap.add_argument("--lane", default=os.environ.get("H2_LANE"))
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--cycle", type=int, default=120, help="seconds per cycle")
    ap.add_argument("--dbfs", type=float, default=-20, help="tone level with the amp held off")
    ap.add_argument("--loud-dbfs", type=float, default=-40, help="tone level on a loud cycle")
    ap.add_argument("--loud-every", type=int, default=15, help="by day, one cycle in N plays out loud; 0: never")
    ap.add_argument("--skip-every", type=int, default=4)
    ap.add_argument("--tasks-every", type=int, default=5)
    ap.add_argument("--out")
    ap.add_argument("--chart", metavar="JSONL")
    ap.add_argument("--summary", metavar="JSONL")
    a = ap.parse_args(argv)
    if a.chart:
        chart(a.chart)
    elif a.summary:
        print(json.dumps(summary(a.summary), indent=1))
    elif a.dev:
        run(a)
    else:
        ap.error("--dev, --chart or --summary")


if __name__ == "__main__":
    main()
