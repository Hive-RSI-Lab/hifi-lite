#!/usr/bin/env python3
"""Scan one of our hi-fi Lites from the LAN side and judge what answers against the listener allow-list.

  scan_board.py run --from aabbccdd742c --target aabbccdd73e0 [--tcp 1-65535] [--udp LIST] [--rate 300]
                    [--listen 20] [--no-recheck] [--save]
  scan_board.py judge FILE.json [--ap-on]     # judge a saved result again

The lab host can't reach the boards' 192.168.2.x LAN, so the scan runs on a board: the lab image's
`lanscan` experiment (firmware/components/hifi/esp/lab_lanscan.c), which only touches one host on its own /24
whose ARP answer is the named peer's MAC. --from must run a lab image with lanscan in it (e.g. aeb53-lanscan2),
held under your lease; the target can run anything.

Targets come only from the bench's own diag files (~/.h2/serial/diag/<dev>.json): a hi-fi Lite, with the IP
and MAC it last reported. Nothing else can be named, so the tool can't scan anything but our boards.

What passes (the image's listener allow-list, core/hifi_listen.c, the same in product and lab images):
  TCP open      only 80 (the API and web page) and 5000 (AirPlay)
  UDP           5353 (mDNS) and 68 (DHCP client); 67 and 53 only while the board's setup AP is on;
                49152 and up as a client's reply port
  heard         what the target sent us or to a multicast group during the scan must come from one of those
                UDP ports (mDNS answers from 5353, a client query from an ephemeral port); an SSDP (1900)
                or LLMNR (5355) announcement fails.
A UDP port that neither answers nor gives an ICMP port-unreachable is bound (or the probe was lost): one that
isn't allowed is scanned again on its own (with any silent TCP ports), and fails only if it is silent twice.

Exit 0 pass, 1 a port or announcement outside the allow-list, 2 the scan didn't finish (or MAC mismatch).
It drives the board through ~/modules/dash/tools/serialtab (send --dev), so the board switches and leases hold.
"""
import argparse, json, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

SERIALTAB = Path.home() / "modules/dash/tools/serialtab"
DIAG = Path.home() / ".h2/serial/diag"
LOGS = Path.home() / ".h2/serial/remote"
DATA = Path(__file__).resolve().parent.parent / "lab/data"

TCP_ALLOWED = {80, 5000}
EPHEMERAL_LO = 49152


def udp_allowed(port, ap_on):
    if port in (5353, 68):
        return True
    if port in (67, 53):
        return ap_on
    return port >= EPHEMERAL_LO


def board(dev, diag_dir=DIAG):
    """{dev, name, ip, mac, ap_on} for one of our Lites from its diag file; SystemExit when it isn't one."""
    if not re.fullmatch(r"[0-9a-f]{12}", dev or ""):
        raise SystemExit(f"{dev!r}: a device is its 12 hex digit MAC, e.g. aabbccdd73e0")
    f = Path(diag_dir) / f"{dev}.json"
    if not f.exists():
        raise SystemExit(f"{dev}: no diag file in {diag_dir}: only boards the bench knows can be scanned")
    d = json.loads(f.read_text())
    if d.get("board") != "hi-fi Lite":
        raise SystemExit(f"{dev}: not a hi-fi Lite ({d.get('board')!r})")
    info = (d.get("pages") or {}).get("info") or {}
    net = ((d.get("pages") or {}).get("state") or {}).get("net") or {}
    mac = (info.get("mac") or "").replace(":", "").lower()
    if mac != dev:
        raise SystemExit(f"{dev}: its diag reports MAC {mac or '(none)'}, not its own name")
    ip = d.get("ip") or net.get("ip")
    if not ip or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
        raise SystemExit(f"{dev}: no IP in its diag")
    return {"dev": dev, "name": d.get("name") or dev, "ip": ip, "mac": dev,
            "ap_on": net.get("mode") not in (None, "sta"), "fw": info.get("fw"), "seen": d.get("seen")}


def pair(src, dst, diag_dir=DIAG):
    a, b = board(src, diag_dir), board(dst, diag_dir)
    if src == dst:
        raise SystemExit("a board can't scan itself")
    if a["ip"].rsplit(".", 1)[0] != b["ip"].rsplit(".", 1)[0]:
        raise SystemExit(f"{a['name']} ({a['ip']}) and {b['name']} ({b['ip']}) aren't on the same /24")
    return a, b


def heard_parse(s):
    """'5353>224.0.0.251:5353 x3' -> (5353, '224.0.0.251', 5353, 3)"""
    m = re.fullmatch(r"(\d+)>([\d.]+):(\d+) x(\d+)", s)
    if not m:
        return None
    return int(m[1]), m[2], int(m[3]), int(m[4])


UNLISTED = "TCP ports gave no answer at all (filtered or lost)"


def judge(res, ap_on=False):
    """The verdict on one lanscan result: {"verdict": pass|fail|incomplete, "bad": [...], "suspect": {tcp, udp},
    "notes": [...]}. suspect ports are silent ones a recheck should look at again."""
    bad, notes = [], []
    tcp, udp = res.get("tcp") or {}, res.get("udp") or {}
    out = {"verdict": "pass", "bad": bad, "suspect": {"tcp": [], "udp": []}, "notes": notes}
    if res.get("target_mac"):
        out["verdict"] = "incomplete"
        bad.append(f"the target's IP answers ARP as {res['target_mac']}, not the named board: not scanned")
        return out
    if res.get("error") or res.get("phase") != "done":
        out["verdict"] = "incomplete"
        bad.append(f"the scan stopped in phase {res.get('phase')!r}: {res.get('error') or 'no error given'}")
    for p in tcp.get("open") or []:
        if p not in TCP_ALLOWED:
            bad.append(f"TCP {p} open")
    if tcp.get("open_more"):
        bad.append(f"{tcp['open_more']} more TCP ports open than listed")
    for p in udp.get("answered") or []:
        if not udp_allowed(p, ap_on):
            bad.append(f"UDP {p} answered")
    if udp.get("answered_more"):
        bad.append(f"{udp['answered_more']} more UDP ports answered than listed")
    out["suspect"]["tcp"] = list(tcp.get("silent") or [])
    if tcp.get("silent_more"):
        out["unlisted_tcp"] = tcp["silent_more"]
        bad.append(f"{len(tcp['silent']) + tcp['silent_more']} {UNLISTED}")
    out["suspect"]["udp"] = [p for p in udp.get("silent") or [] if not udp_allowed(p, ap_on)]
    allowed_silent = [p for p in udp.get("silent") or [] if udp_allowed(p, ap_on)]
    if allowed_silent:
        notes.append("UDP bound (silent, allowed): " + ",".join(map(str, allowed_silent)))
    if udp.get("silent_more"):
        bad.append(f"{len(udp['silent']) + udp['silent_more']} UDP ports silent: more than a recheck can list")
    for h in res.get("heard") or []:
        x = heard_parse(h)
        if not x:
            bad.append(f"heard entry unreadable: {h!r}")
        elif not udp_allowed(x[0], ap_on):
            bad.append(f"sent from UDP {x[0]} to {x[1]}:{x[2]} ({x[3]}x)")
        elif x[2] in (1900, 5355) and x[0] >= EPHEMERAL_LO:
            notes.append(f"a client query to {x[1]}:{x[2]} from {x[0]} ({x[3]}x)")
    if res.get("heard_lost"):
        bad.append(f"{res['heard_lost']} more senders heard than the board could list")
    if out["suspect"]["tcp"] or out["suspect"]["udp"]:
        notes.append(f"silent, to recheck: tcp {out['suspect']['tcp']} udp {out['suspect']['udp']}")
    if bad and out["verdict"] == "pass":
        out["verdict"] = "fail"
    return out


def merge_recheck(first, again):
    """A port silent in the first scan fails only if the recheck also found it silent (or open)."""
    v = dict(first)
    bad = list(first["bad"])
    notes = first["notes"] + [f"recheck: {again['verdict']}"]
    still_any = False
    for proto in ("tcp", "udp"):
        still = set(again["suspect"][proto]) & set(first["suspect"][proto])
        still_any = still_any or bool(still)
        for p in sorted(still):
            bad.append(f"{proto.upper()} {p} silent twice: " + ("filtered" if proto == "tcp" else "bound, not allowed"))
    bad += [b for b in again["bad"] if b not in bad and "stopped in phase" not in b]
    unknown = 0
    if first.get("unlisted_tcp") and not still_any and again["verdict"] != "incomplete":
        # every listed silent port answered on the recheck, so the silence was lost packets; the ports past the
        # list weren't rechecked, so they are unknown, not bad
        unknown = first["unlisted_tcp"]
        bad = [b for b in bad if not b.endswith(UNLISTED)]
        notes.append(f"the {len(first['suspect']['tcp'])} listed silent TCP ports all answered on the recheck; "
                     f"{unknown} more weren't listed, so they are unchecked")
    v["bad"] = bad
    v["suspect"] = {"tcp": [], "udp": []}
    v["notes"] = notes
    if again["verdict"] == "incomplete":
        v["verdict"] = "incomplete"
        v["bad"].append("the recheck didn't finish")
    elif bad:
        v["verdict"] = "fail" if first["verdict"] != "incomplete" else "incomplete"
    elif unknown or first["verdict"] == "incomplete":
        v["verdict"] = "incomplete"
    else:
        v["verdict"] = "pass"
    return v


def estimate_s(tcp_n, udp_n, rate, listen):
    # the first pass, plus about one more for the retries of the silent ports and the drain waits
    return int(2 * (tcp_n + udp_n) / rate + listen + 15)


def count_ports(spec):
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        n += int(b or a) - int(a) + 1
    return n


def api(dev, op, body=None, wait=25):
    """Send `api OP [json]` to a remote board and return the next `API {...}` reply it logged (dict), or None."""
    cmd = f"api {op}" if body is None else f"api {op} " + json.dumps(json.dumps(body, separators=(",", ":")))
    log = LOGS / f"{dev}.log"
    start = log.stat().st_size if log.exists() else 0
    r = subprocess.run([str(SERIALTAB), "send", "--dev", dev, cmd], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{dev}: {cmd}: {r.stderr.strip() or r.stdout.strip()}")
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.5)
        with open(log, "rb") as f:
            f.seek(start)
            new = f.read().decode(errors="replace").splitlines()
        seen_cmd = False
        for ln in new:
            if f"> {cmd}" in ln:
                seen_cmd = True
            elif seen_cmd and " API " in ln:
                try:
                    return json.loads(ln.split(" API ", 1)[1])
                except ValueError:
                    return {"unparsed": ln}
    return None


def scan(a, b, tcp, udp, rate, listen):
    body = {"op": "start", "exp": "lanscan", "target": b["ip"], "peer": b["mac"], "tcp": tcp, "rate": rate,
            "listen_s": listen}
    if udp:
        body["udp"] = udp
    est = estimate_s(count_ports(tcp), count_ports(udp) if udp else 1100, rate, listen)
    body["max_s"] = min(3600, est * 2 + 60)
    r = api(a["dev"], "lab", body)
    if not r or r.get("error") or not r.get("running"):
        raise SystemExit(f"{a['name']}: lanscan didn't start: {r}")
    print(f"{a['name']} scanning {b['name']} {b['ip']}: tcp {tcp}, about {est} s", flush=True)
    end = time.time() + body["max_s"] + 60
    last = None
    while time.time() < end:
        time.sleep(15)
        r = api(a["dev"], "lab", {"op": "result", "exp": "lanscan"})
        if not r:
            continue
        res = r.get("result") or {}
        if res.get("phase") != last:
            last = res.get("phase")
            print(f"  {int(r.get('elapsed_s') or 0)} s: {last}, {res.get('sent')} sent", flush=True)
        if not r.get("running"):
            return res
    api(a["dev"], "lab", {"op": "stop", "exp": "lanscan"})
    return {"phase": "timeout", "error": "no end seen; stopped"}


def summary(b, v, res):
    tcp, udp = res.get("tcp") or {}, res.get("udp") or {}
    s = (f"{b['name']} {b['ip']}: {v['verdict'].upper()}; TCP {tcp.get('ports')} probed, open {tcp.get('open')}, "
         f"{tcp.get('closed')} closed; UDP {udp.get('ports')} probed, answered {udp.get('answered')}, "
         f"silent {udp.get('silent')}; heard {res.get('heard')}; {res.get('s')} s, board heap_min {res.get('heap_min')}")
    for x in v["bad"]:
        s += f"\n  BAD {x}"
    for x in v["notes"]:
        s += f"\n  note {x}"
    return s


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--from", dest="src", required=True, help="the scanning board (a lab image with lanscan)")
    r.add_argument("--target", required=True, help="the board scanned")
    r.add_argument("--tcp", default="1-65535")
    r.add_argument("--udp", help="UDP ports (default: the board's list of about 1100 common ones)")
    r.add_argument("--rate", type=int, default=300, help="probes a second, 20-1000")
    r.add_argument("--listen", type=int, default=20, help="seconds to listen for announcements afterwards")
    r.add_argument("--no-recheck", action="store_true")
    r.add_argument("--save", action="store_true", help=f"keep the result in {DATA}")
    j = sub.add_parser("judge")
    j.add_argument("file")
    j.add_argument("--ap-on", action="store_true")
    o = p.parse_args(argv)

    if o.cmd == "judge":
        d = json.loads(Path(o.file).read_text())
        res = d.get("result", d)
        v = judge(res, o.ap_on or bool((d.get("target") or {}).get("ap_on")))
        print(summary(d.get("target") or {"name": "?", "ip": res.get("target")}, v, res))
        return {"pass": 0, "fail": 1}.get(v["verdict"], 2)

    a, b = pair(o.src, o.target)
    t0 = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    res = scan(a, b, o.tcp, o.udp, o.rate, o.listen)
    v = judge(res, b["ap_on"])
    again = None
    if v["verdict"] != "incomplete" and not o.no_recheck and (v["suspect"]["tcp"] or v["suspect"]["udp"]):
        tcp2 = ",".join(map(str, v["suspect"]["tcp"])) or "80"
        udp2 = ",".join(map(str, v["suspect"]["udp"])) or "5353"
        print(f"rechecking the silent ones: tcp {tcp2} udp {udp2}", flush=True)
        again = scan(a, b, tcp2, udp2, min(o.rate, 100), 5)
        v = merge_recheck(v, judge(again, b["ap_on"]))
    print(summary(b, v, res))
    if o.save:
        DATA.mkdir(parents=True, exist_ok=True)
        f = DATA / f"lanscan-{b['dev']}-{t0.replace(':', '')[:15]}.json"
        f.write_text(json.dumps({"t": t0, "from": a, "target": b, "args": vars(o), "result": res,
                                 "recheck": again, "verdict": v}, indent=1) + "\n")
        print(f"saved {f}")
    return {"pass": 0, "fail": 1}.get(v["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
