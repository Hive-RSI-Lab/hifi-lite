#!/usr/bin/env python3
"""Audio measurements on a remote Lite, through the codec's loopback taps.

The board plays test tones (the test API's `tone`) and records its own
speaker outputs (`capture`, source `loopback`: the ES8388's ADC on dividers
from the speaker legs, docs/test-api.md in the firmware repo). No cable and
no flash: it runs on any image with `capture`, on a board your lane holds.

  audio_measure.py run --dev aabbccdd743c        measure, raw replies to reports/raw/audio-<dev>-<date>.jsonl
  audio_measure.py report RAW.jsonl [...]         chart per board (reports/audio-<dev>-<date>.png) and the table rows

What each figure is and its limits:
- Frequency response: a stepped sine at -20 dBFS, the board's AC RMS over a
  16384-frame capture (0.34 s, so 6.8 cycles at 20 Hz), relative to 1 kHz.
- THD+N: the bench logs a reply line up to 1000 characters, so a capture's
  samples come through only for 128 frames (2.7 ms). A sine fit at the known
  frequency (the tone and the ADC share the codec's clock) takes DC and the
  fundamental out; the rest is THD+N, over 0 to 24 kHz (no 20 kHz filter), as
  the median of several snippets. The fit needs a cycle or more, so 1 kHz and up.
- Noise: digital silence with the amp on, the board's AC RMS over 16384
  frames; and the same with the amp held off. On Lite 2 the amp-off reading
  came out higher than amp-on (the speaker legs seem to float when the amp is
  off), so it is kept as a figure but is not the measurement's floor. Volts
  RMS are rms_fs times the tap's `v_per_fs` (volts at the speaker leg for an
  ADC full scale, a datasheet figure until calibrated): rms_fs is already an
  RMS, so no sqrt(2).
- Channel modes: each mode (stereo, left, right, mono) is set with
  `channel`, and the board is left in the mode it was in. Isolation in left
  and right is the muted side's whole 1 kHz capture RMS against the playing
  side's, so it includes the muted side's noise floor (a lower bound).
- A raw file can hold several runs (each starts with a meta row); `report`
  takes each mode from the latest run that measured it.
"""
import argparse
import base64
import datetime as dt
import json
import math
import os
import signal
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
sys.path.insert(0, str(Path.home() / "modules" / "dash"))

FR_FULL = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
           2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000]
FR_OCT = [20, 31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000, 20000]
FR_LEVEL = -20
THD_LEVELS = [-30, -20, -12, -6]   # 1 kHz, stereo
THD_FREQS = [1000, 3000]           # -20 dBFS, stereo; 1 kHz -20 in every mode
SNIPPET = 128                      # frames whose samples fit in the bench's logged line
SNIPS = 6
LONG = 16384
MODES = ["stereo", "left", "right", "mono"]
SETTLE_S = 0.4


# ---------------------------------------------------------------- the board

class Board:
    def __init__(self, dev, lane, by):
        import serial_bridge as sb
        self.sb, self.dev, self.lane, self.by = sb, dev, lane, by
        self.log = Path(os.environ.get("H2_SERIAL_DIR") or Path.home() / ".h2" / "serial") / "remote" / f"{dev}.log"

    def api(self, op, body=None, timeout=20):
        """One `api` line through the bench; its reply as a dict ({"error": ...} for a non-2xx)."""
        cmd = f"api {op}" + (" " + json.dumps(json.dumps(body, separators=(",", ":"))) if body is not None else "")
        pos = self.log.stat().st_size if self.log.exists() else 0
        r = self.sb.remote_ctl({"op": "send", "dev": self.dev, "cmd": cmd, "by": self.by, "lane": self.lane}, 15)
        if not r or not r.get("ok"): raise RuntimeError(f"{cmd}: {(r or {}).get('why') or 'the bench did not take it'}")
        echo, unsent = f"> {cmd}   ({self.by})", f"'s line {r.get('id')} not sent"
        end, seen, buf = time.time() + timeout, False, ""
        while time.time() < end:
            time.sleep(0.2)
            buf, pos = self._read(buf, pos)
            lines = buf.split("\n"); buf = lines.pop()
            for ln in lines:
                text = ln[20:] if len(ln) > 19 and ln[19] == " " else ln   # "2026-11-17T12:53:18 " then the line
                if text == echo: seen = True
                elif text.startswith("--- ") and r.get("id") and unsent in text: raise RuntimeError(f"{cmd}: {text}")
                elif seen and text.startswith("API "):
                    return parse_reply(text[4:])
                elif seen and text.startswith("--- no answer"): raise RuntimeError(f"{cmd}: {text}")
        raise RuntimeError(f"{cmd}: no reply in {timeout} s")

    def _read(self, buf, pos):
        """What the log gained since pos. The bench moves a log past 1 MiB to
        .log.1 and starts it afresh: the rest of .1 comes first, then the new file from 0."""
        size = self.log.stat().st_size if self.log.exists() else 0
        if size < pos:
            old = self.log.with_name(self.log.name + ".1")
            if old.exists():
                with open(old, errors="replace") as f: f.seek(pos); buf += f.read()
            pos = 0
        if self.log.exists():
            with open(self.log, errors="replace") as f: f.seek(pos); buf += f.read(); pos = f.tell()
        return buf, pos


def parse_reply(text):
    """A reply line as a dict. The bench cuts a logged line at 1000 characters,
    so a long capture loses its samples; the stats come before them and are
    kept (with "cut": true and no "pcm")."""
    try: return json.loads(text)
    except ValueError: pass
    cut = text.find(',"pcm":')
    if cut > 0:
        try: return {**json.loads(text[:cut] + "}"), "cut": True}
        except ValueError: pass
    return {"error": "cut", "line": text}


def run(a):
    by = os.environ.get("H2_NAME", "agent")
    lane = a.lane or os.environ.get("H2_LANE") or ""
    b = Board(a.dev, lane, by)
    day = dt.date.today().isoformat()
    out = Path(a.out or LAB / "reports" / "raw" / f"audio-{a.dev}-{day}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    f = open(out, "a")

    playing = {}
    amp_off = False

    def rec(kind, **ctx):
        frames = ctx.pop("frames")
        try:
            rep = b.api("capture", {"source": "loopback", "frames": frames})
        except RuntimeError as e:
            # The bench drops a reply when the board's link redials (Lite 2,
            # 2026-11-17, uptime unbroken). Once: play the tone again (a real
            # restart would have stopped it) and capture again.
            if "dialled in again" not in str(e): raise
            # A restart would also have put back the saved mode and the amp:
            # set the mode and the amp state again, then the tone.
            print(f"  {kind} {ctx}: the board redialled, once more", file=sys.stderr)
            time.sleep(2)
            if "mode" in ctx: b.api("channel", {"channel": ctx["mode"]})
            if amp_off: b.api("amp", {"enable": False}); time.sleep(0.5)
            if playing: tone(**playing)
            rep = b.api("capture", {"source": "loopback", "frames": frames})
        if "error" in rep: print(f"  {kind} {ctx}: {rep}", file=sys.stderr)
        row = {"t": time.time(), "dev": a.dev, "kind": kind, **ctx, "reply": rep}
        f.write(json.dumps(row) + "\n"); f.flush()
        return rep

    def tone(**body):
        rep = b.api("tone", {"duration_s": 60, **body})
        if not rep.get("ok"): raise RuntimeError(f"tone {body}: {rep}")
        playing.clear(); playing.update(body)
        time.sleep(SETTLE_S)

    st = b.api("state/audio")
    was = "stereo" if "error" in st else (st.get("audio") or {}).get("channel") or "stereo"
    info = b.api("info")
    f.write(json.dumps({"t": time.time(), "dev": a.dev, "kind": "meta", "info": info, "audio": st.get("audio")}) + "\n")
    modes = a.modes.split(",")
    try:
        for m in modes:
            b.api("channel", {"channel": m}); time.sleep(0.5)
            # The first capture after a mode change caught the mixers and the
            # ADC's DC filter still moving on Lite 2 (DC -0.03 FS, the muted
            # side not yet muted), so one throwaway capture first.
            tone(type="sine", freq_hz=1000, level_dbfs=FR_LEVEL, channel="both")
            rec("settle", mode=m, freq=1000, level=FR_LEVEL, frames=LONG)
            freqs = FR_FULL if m == "stereo" else FR_OCT
            print(f"{m}: response at {len(freqs)} frequencies", flush=True)
            for hz in freqs:
                tone(type="sine", freq_hz=hz, level_dbfs=FR_LEVEL, channel="both")
                rec("fr", mode=m, freq=hz, level=FR_LEVEL, frames=LONG)
            pts = [(1000, FR_LEVEL)] + ([(1000, l) for l in THD_LEVELS if l != FR_LEVEL] + [(hz, FR_LEVEL) for hz in THD_FREQS if hz != 1000] if m == "stereo" else [])
            print(f"{m}: THD+N at {len(pts)} points", flush=True)
            for hz, lvl in pts:
                tone(type="sine", freq_hz=hz, level_dbfs=lvl, channel="both")
                rec("long", mode=m, freq=hz, level=lvl, frames=LONG)
                for i in range(SNIPS): rec("thd", mode=m, freq=hz, level=lvl, frames=SNIPPET, i=i)
            print(f"{m}: noise", flush=True)
            tone(type="silence")
            for i in range(3): rec("noise", mode=m, amp=True, frames=LONG, i=i)
            if m == "stereo":
                amp_off = True   # set first: a failed or lost disable may still have taken
                b.api("amp", {"enable": False}); time.sleep(0.5)
                tone(type="silence")
                for i in range(3): rec("noise", mode=m, amp=False, frames=LONG, i=i)
                b.api("amp", {"enable": True}); amp_off = False
    finally:
        # Put the board back however the run ended: each step on its own, once
        # more after a redial, and a stop now waits for it to finish.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        steps = [("tone", {"type": "stop"})] + ([("amp", {"enable": True})] if amp_off else []) + [("channel", {"channel": was})]
        for op, body in steps:
            for attempt in (0, 1):
                try: b.api(op, body); break
                except RuntimeError as e:
                    if attempt or "dialled in again" not in str(e):
                        print(f"  cleanup {op} {body}: {e}", file=sys.stderr); break
                    time.sleep(2)
        f.close()
    print(out)


# ---------------------------------------------------------------- the numbers

def pcm_of(rep, ch):
    raw = base64.b64decode(rep["pcm"] + "=" * (-len(rep["pcm"]) % 4))
    n = len(raw) // 4
    v = struct.unpack(f"<{2 * n}h", raw[:4 * n])
    return [x / 32768.0 for x in v[ch::2]]


def solve(A, y):
    """Least squares by the normal equations (small, well-conditioned fits)."""
    k = len(A[0])
    M = [[sum(r[i] * r[j] for r in A) for j in range(k)] for i in range(k)]
    v = [sum(r[i] * yy for r, yy in zip(A, y)) for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(M[r][c]))
        M[c], M[p], v[c], v[p] = M[p], M[c], v[p], v[c]
        for r in range(k):
            if r != c and M[c][c]:
                q = M[r][c] / M[c][c]
                M[r] = [a - q * b for a, b in zip(M[r], M[c])]; v[r] -= q * v[c]
    return [v[i] / M[i][i] if M[i][i] else 0.0 for i in range(k)]


def thdn(x, freq, rate, harmonics=0, trend=2):
    """(fundamental RMS, residual RMS) after fitting DC + the fundamental (+ harmonics 2..h+1 below Nyquist, to get N alone).

    Each capture starts the ADC's DC filter afresh, so a snippet rides on a
    slow DC drift (dc_fs 0.02 over 128 frames, 0.0004 over 16384); `trend`
    polynomial terms take that out, which also removes anything below about
    1/snippet (375 Hz), so the figure is THD+N from about 400 Hz to 24 kHz."""
    w = 2 * math.pi * freq / rate
    n = len(x)
    cols = lambda i: [1.0] + [(2 * i / n - 1) ** k for k in range(1, trend + 1)] + [g(h * w * i) for h in range(1, harmonics + 2) if h * freq < rate / 2 for g in (math.cos, math.sin)]
    A = [cols(i) for i in range(len(x))]
    c = solve(A, x)
    res = [xx - sum(ci * ai for ci, ai in zip(c, row)) for xx, row in zip(x, A)]
    dof = max(1, len(x) - len(c))
    return math.hypot(c[1 + trend], c[2 + trend]) / math.sqrt(2), math.sqrt(sum(r * r for r in res) / dof)


def median(v):
    v = sorted(v); n = len(v)
    return (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2) if n else float("nan")


def db(x): return 20 * math.log10(x) if x > 0 else -200.0


DC_SETTLING = 0.01   # a response capture with more DC than this was still settling (settled ones read under 0.004)


def analyse(rows):
    """{mode: {...}} from one board's rows. A file can hold several runs (each
    starts with a meta row, a re-run may cover only some modes): each mode is
    taken from the latest run that measured it, and never mixed across runs."""
    run, last, tagged = 0, {}, []
    for r in rows:
        if r["kind"] == "meta": run += 1
        tagged.append((run, r))
        if r.get("mode") and r["kind"] != "settle": last[r["mode"]] = run
    rows = [r for n, r in tagged if r["kind"] == "meta" or last.get(r.get("mode")) == n]
    meta = next((r for r in reversed(rows) if r["kind"] == "meta"), {})
    out = {"dev": rows[0]["dev"], "meta": meta, "modes": {}}
    vpfs = None
    for r in rows:
        if r["kind"] in ("meta", "settle") or "error" in r["reply"]: continue
        rep = r["reply"]; vpfs = vpfs or rep.get("v_per_fs", [None])[0]
        if r["kind"] == "fr" and max(abs(s["dc_fs"]) for s in rep["stats"]) > DC_SETTLING:
            out.setdefault("dropped", []).append({k: r.get(k) for k in ("mode", "freq", "level")})
            continue
        m = out["modes"].setdefault(r["mode"], {"fr": {}, "thd": {}, "long": {}, "noise_on": [], "noise_off": []})
        rms = [s["rms_fs"] for s in rep["stats"]]
        if r["kind"] == "fr": m["fr"][r["freq"]] = rms
        elif r["kind"] == "long": m["long"][(r["freq"], r["level"])] = rms
        elif r["kind"] == "noise": m["noise_on" if r["amp"] else "noise_off"].append(rms)
        elif r["kind"] == "thd" and rep.get("pcm"):
            for ch in (0, 1):
                x = pcm_of(rep, ch)
                if len(x) < SNIPPET // 2: continue
                f1, n = thdn(x, r["freq"], rep.get("rate", 48000))
                _, n_only = thdn(x, r["freq"], rep.get("rate", 48000), harmonics=6)
                m["thd"].setdefault((r["freq"], r["level"]), [[], []])[ch].append((f1, n, n_only))
    out["v_per_fs"] = vpfs
    for name, m in out["modes"].items():
        ref = m["fr"].get(1000)
        m["fr_db"] = {hz: [db(v[c] / ref[c]) if ref and ref[c] > 1e-4 else None for c in (0, 1)] for hz, v in sorted(m["fr"].items())}
        m["thd_pct"] = {}
        for k, chans in m["thd"].items():
            m["thd_pct"][k] = []
            for c in chans:
                good = [(f1, n, no) for f1, n, no in c if f1 > 1e-4]
                m["thd_pct"][k].append({"thdn": 100 * median([n / f1 for f1, n, _ in good]) if good else None,
                                        "n_only": 100 * median([no / f1 for f1, _, no in good]) if good else None,
                                        "fund_fs": median([f1 for f1, _, _ in good]) if good else None})
        m["noise_on_fs"] = [median([v[c] for v in m["noise_on"]]) for c in (0, 1)] if m["noise_on"] else None
        m["noise_off_fs"] = [median([v[c] for v in m["noise_off"]]) for c in (0, 1)] if m["noise_off"] else None
    return out


def band(fr_db, lo=None, hi=None):
    """(min, max) dB over the points between lo and hi Hz, both channels that play."""
    v = [d for hz, ds in fr_db.items() if (lo is None or hz >= lo) and (hi is None or hz <= hi) for d in ds if d is not None]
    return (min(v), max(v)) if v else (None, None)


def chart(an, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    colors = {"stereo": "#2a6fdb", "left": "#d9822b", "right": "#3a9d5d", "mono": "#8a5cc2"}
    for name, m in an["modes"].items():
        for c, ls in ((0, "-"), (1, "--")):
            pts = [(hz, d[c]) for hz, d in m["fr_db"].items() if d[c] is not None and not (name == "left" and c == 1) and not (name == "right" and c == 0)]
            if pts: ax[0].semilogx(*zip(*pts), ls, color=colors.get(name), label=f"{name} {'LR'[c]}", marker="." if name == "stereo" else None)
    ax[0].set(xlabel="Hz", ylabel="dB re 1 kHz", title="Frequency response at -20 dBFS (loopback)", ylim=(-6, 3))
    ax[0].grid(True, which="both", alpha=.3); ax[0].legend(fontsize=8, ncol=2)
    st = an["modes"].get("stereo", {})
    lv = sorted(l for (hz, l) in st.get("thd_pct", {}) if hz == 1000)
    for c, ls in ((0, "-o"), (1, "--s")):
        y = [st["thd_pct"][(1000, l)][c]["thdn"] for l in lv]
        ax[1].plot(lv, y, ls, color="#2a6fdb", label=f"THD+N 1 kHz {'LR'[c]}")
        fl = [100 * st["noise_on_fs"][c] / (st["thd_pct"][(1000, l)][c]["fund_fs"] or 1) for l in lv] if st.get("noise_on_fs") else None
        if fl and c == 0: ax[1].plot(lv, fl, ":", color="grey", label="idle noise alone (amp on, silence)")
    ax[1].set_yscale("log"); ax[1].set(xlabel="output level (dBFS)", ylabel="THD+N (%)", title="THD+N at 1 kHz, 0-24 kHz (stereo)")
    ax[1].grid(True, which="both", alpha=.3); ax[1].legend(fontsize=8)
    name = (an["meta"].get("info") or {}).get("name") or an["dev"]
    fig.suptitle(f"{name} ({an['dev']}): audio through the loopback taps")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def summary(an):
    """One line of figures per mode, for the thread and the table."""
    vp = an["v_per_fs"] or 0
    lines = []
    for name in MODES:
        m = an["modes"].get(name)
        if not m: continue
        plays = [0] if name == "left" else [1] if name == "right" else [0, 1]
        fr = {hz: [d[c] if c in plays else None for c in (0, 1)] for hz, d in m["fr_db"].items()}
        lo, hi = band(fr, 20, 20000)
        lo2, hi2 = band(fr, 40, 16000)
        t = m["thd_pct"].get((1000, FR_LEVEL))
        nz = m["noise_on_fs"]
        iso = None
        if name in ("left", "right"):
            q = 1 - plays[0]
            ref = m["fr"].get(1000)
            iso = db(ref[q] / ref[plays[0]]) if ref else None
        lines.append({"mode": name, "fr_20_20k": (lo, hi), "fr_40_16k": (lo2, hi2),
                      "thdn_1k_m20": [t[c]["thdn"] if c in plays else None for c in (0, 1)] if t else None,
                      "noise_uv": [1e6 * nz[c] * vp for c in (0, 1)] if nz else None,
                      "noise_dbfs_out": None, "iso_db": iso})
    st = an["modes"].get("stereo") or {}
    return {"modes": lines, "thd_vs_level": {l: st.get("thd_pct", {}).get((1000, l)) for l in THD_LEVELS},
            "thd_3k": st.get("thd_pct", {}).get((3000, FR_LEVEL)),
            "noise_off_uv": [1e6 * st["noise_off_fs"][c] * vp for c in (0, 1)] if st.get("noise_off_fs") else None,
            "noise_on_uv": [1e6 * st["noise_on_fs"][c] * vp for c in (0, 1)] if st.get("noise_on_fs") else None,
            "full_1k_m20_fs": st.get("long", {}).get((1000, FR_LEVEL)), "dropped": an.get("dropped", [])}


def report(a):
    for p in a.raw:
        rows = [json.loads(l) for l in open(p) if l.strip()]
        an = analyse(rows)
        png = Path(a.outdir or LAB / "reports") / (Path(p).stem + ".png")
        chart(an, png)
        s = summary(an)
        print(png)
        print(json.dumps(s, indent=1, default=lambda o: round(o, 4) if isinstance(o, float) else str(o)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run"); r.add_argument("--dev", required=True); r.add_argument("--lane"); r.add_argument("--out")
    r.add_argument("--modes", default=",".join(MODES))
    p = sp.add_parser("report"); p.add_argument("raw", nargs="+"); p.add_argument("--outdir")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # a stop still stops the tone and puts the mode back
    run(a) if a.cmd == "run" else report(a)


if __name__ == "__main__":
    main()
