"""``uwbkit doctor``: is every node ranging? Rates, a per-link table and plain-language verdicts.

It asks a running hub (``GET /diag`` and ``/state``) and checks, in order: the hub answers, the
source is receiving lines, every node is online, every expected link is ranging at a healthy rate
with sane distances, the anchors are calibrated, and every tag has a fix of decent quality.

Usage:  uwbkit doctor [--url http://127.0.0.1:1800] [--watch] [--seconds 3] [--json]
Exit code: 0 all good, 1 warnings, 2 failures (so scripts and the bring-up checklist can use it).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from ..client import Kit, KitError

OK, WARN, FAIL = "ok", "WARN", "FAIL"


def expected_links(nodes: dict) -> list:
    """Links that should exist for this node set: every tag to every anchor, or all mesh pairs."""
    anchors = sorted(n for n, v in nodes.items() if v.get("role") == "anchor")
    tags = sorted(n for n, v in nodes.items() if v.get("role") == "tag")
    mesh = sorted(n for n, v in nodes.items() if v.get("role") == "mesh")
    links = [(t, a) for t in tags for a in anchors]
    links += [(mesh[i], mesh[j]) for i in range(len(mesh)) for j in range(i + 1, len(mesh))]
    return links


def find_link(links: dict, a: str, b: str):
    return links.get(f"{a}-{b}") or links.get(f"{b}-{a}")


def examine(diag: dict, state: dict) -> dict:
    """Turn /diag + /state into checks: [{level, what, hint}], plus the tables."""
    checks = []

    def add(level, what, hint=""):
        checks.append({"level": level, "what": what, "hint": hint})

    hello = state.get("hello") or {}
    nominal = float(diag.get("nominal_rate_hz") or hello.get("rate_hz") or 10)
    nodes = diag.get("nodes") or {}
    links = diag.get("links") or {}
    src = diag.get("source") or {}
    sim = bool(diag.get("simulated"))
    add(OK, f"hub is up: source {hello.get('source')}, profile {hello.get('profile')}"
        + ("  [SIMULATED DATA]" if sim else "  [live boards]"))

    # --- source -----------------------------------------------------------------
    ports = src.get("ports") or []
    for sub in src.get("sources") or []:
        ports += sub.get("ports") or []
    if "ports" in src or any("ports" in s for s in src.get("sources") or []):
        if not ports:
            add(FAIL, "no serial port found", "plug a board in (data cable); run `uwbkit identify`")
        for p in ports:
            if not p.get("open"):
                add(FAIL, f"serial port {p.get('port')} is not open: {p.get('error')}",
                    "close other programs using it (Arduino Serial Monitor); on Linux join the dialout group")
            elif not p.get("lines_ok"):
                add(FAIL, f"serial port {p.get('port')} is open but silent",
                    "wrong firmware or baud rate: run `uwbkit identify --port " + str(p.get("port")) + " --reset`")
            elif (p.get("last_line_age_s") or 0) > 3:
                add(WARN, f"serial port {p.get('port')}: no line for {p.get('last_line_age_s')} s", "board reset or stalled?")
    if "senders" in src:
        if not src.get("listening"):
            add(FAIL, f"UDP port not listening: {src.get('error')}", "another program holds the port; pick another")
        elif not src.get("senders"):
            add(FAIL, "no board has sent a UDP datagram yet",
                "on each board: `WIFI <ssid> <password>`, `HUB <this computer's IP> <port>`, `SAVE`; "
                "allow the port through the firewall (Windows asks once)")
    bad = int(src.get("bad_checksum") or 0) + int(src.get("lines_bad") or 0)
    okl = int(src.get("lines_ok") or 0)
    if okl and bad / max(1, okl + bad) > 0.02:
        add(WARN, f"{bad} corrupted lines out of {okl + bad}", "bad USB cable / hub, or Wi-Fi loss; results stay correct (checksums)")

    # --- nodes ------------------------------------------------------------------
    offline = [n for n, v in nodes.items() if not v.get("online")]
    if offline:
        add(FAIL, f"offline: {', '.join(sorted(offline))}",
            "is it powered? is its ID unique (two boards with the same ID look like one)? run `uwbkit identify` on it")
    else:
        add(OK, f"all {len(nodes)} nodes online: {', '.join(sorted(nodes))}")
    for n, v in sorted(nodes.items()):
        bat = v.get("battery")
        if isinstance(bat, (int, float)) and bat < 3.5:
            add(WARN, f"{n} battery {bat:.2f} V", "charge it before the demo")

    # --- links ------------------------------------------------------------------
    missing, slow, far, noisy, nlos = [], [], [], [], []
    for a, b in expected_links(nodes):
        st = find_link(links, a, b)
        name = f"{a}-{b}"
        if not st or (st.get("age_s") is not None and st["age_s"] > 3):
            missing.append(name)
            continue
        if st.get("rate_hz", 0) < 0.5 * nominal:
            slow.append(f"{name} {st.get('rate_hz')} Hz")
        d = st.get("d_mean")
        if d is not None and (d < 0.05 or d > 60):
            far.append(f"{name} {d:.2f} m")
        if st.get("los") is not None and st["los"] < 0.4:
            nlos.append(name)
    total = len(expected_links(nodes))
    if missing:
        add(FAIL if len(missing) > total / 2 else WARN, f"not ranging: {', '.join(missing)}",
            "check antennas point into the room, nothing metal within 10 cm, both ends powered and on the same UWB channel")
    elif total:
        add(OK, f"all {total} expected links are ranging")
    if slow:
        add(WARN, f"slow links (nominal {nominal:g} Hz): {', '.join(slow)}", "too many dropped exchanges: reduce distance / obstructions; with two tags expect half the rate each")
    if far:
        add(FAIL, f"implausible distances: {', '.join(far)}", "antenna delay not calibrated: `uwbkit anchors antdelay` (see BRINGUP.md)")
    if nlos:
        add(WARN, f"probably not line of sight: {', '.join(nlos)}", "a body or wall is in the way: ranges read long; raise the anchors")

    # --- anchors and fixes ------------------------------------------------------
    anchors_msg = state.get("anchors") or {}
    is_mesh = str(hello.get("profile", "")).lower().startswith("mesh")
    if not is_mesh:
        if not anchors_msg.get("anchors"):
            add(FAIL, "anchor coordinates are not set, so no positions are solved",
                "`uwbkit anchors rect <width> <depth>` (tape measure), `uwbkit anchors tape`, or `uwbkit anchors auto`")
        else:
            add(OK, f"anchors set ({anchors_msg.get('calibrated')}): "
                + "  ".join(f"{k}=({v['x']:.2f},{v['y']:.2f},{v['z']:.2f})" for k, v in sorted(anchors_msg["anchors"].items())))
    pos = state.get("pos") or {}
    now = diag.get("t") or time.time()
    want = [n for n, v in nodes.items() if v.get("role") in (("mesh",) if is_mesh else ("tag",))]
    for n in sorted(want):
        p = pos.get(n)
        if not p or now - p.get("t", 0) > 3:
            if is_mesh or anchors_msg.get("anchors"):
                add(FAIL, f"{n}: no position fix", "needs ranges to at least 3 anchors (2-D)")
        elif p.get("q", 0) < 0.3:
            add(WARN, f"{n}: weak fix q={p.get('q')} from {p.get('n')} ranges", "poor geometry or NLOS: spread the anchors, keep the tag inside their outline")
        else:
            add(OK, f"{n}: fix ({p['x']:.2f}, {p['y']:.2f}) q={p.get('q')} n={p.get('n')} src={p.get('src')}")
    worst = FAIL if any(c["level"] == FAIL for c in checks) else WARN if any(c["level"] == WARN for c in checks) else OK
    return {"verdict": worst, "checks": checks, "nodes": nodes, "links": links, "simulated": sim,
            "status": state.get("status") or {}}


def render(rep: dict) -> str:
    out = []
    out.append("NODES")
    out.append(f"  {'id':5s} {'role':7s} {'state':8s} {'age s':>6s} {'chip':8s} {'fw':9s} {'batt V':>6s} {'rate Hz':>7s}")
    for n, v in sorted(rep["nodes"].items()):
        out.append(f"  {n:5s} {str(v.get('role', '')):7s} {'online' if v.get('online') else 'OFFLINE':8s} "
                   f"{_fmt(v.get('age_s'), 6, 1)} {str(v.get('chip', '-'))[:8]:8s} {str(v.get('fw', '-'))[:9]:9s} "
                   f"{_fmt(v.get('battery'), 6, 2)} {_fmt(v.get('rate_hz'), 7, 1)}")
    out.append("LINKS")
    out.append(f"  {'link':8s} {'rate Hz':>7s} {'mean m':>7s} {'std m':>6s} {'rssi':>6s} {'fp':>6s} {'los':>5s} {'age s':>6s} {'count':>7s}")
    for k, v in sorted(rep["links"].items()):
        out.append(f"  {k:8s} {_fmt(v.get('rate_hz'), 7, 1)} {_fmt(v.get('d_mean'), 7, 3)} {_fmt(v.get('d_std'), 6, 3)} "
                   f"{_fmt(v.get('rssi'), 6, 1)} {_fmt(v.get('fp'), 6, 1)} {_fmt(v.get('los'), 5, 2)} "
                   f"{_fmt(v.get('age_s'), 6, 1)} {v.get('count', 0):7d}")
    out.append("CHECKS")
    for c in rep["checks"]:
        mark = {"ok": "[ ok ]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[c["level"]]
        out.append(f"  {mark} {c['what']}")
        if c["hint"] and c["level"] != OK:
            out.append(f"         -> {c['hint']}")
    st = rep["status"]
    out.append(f"VERDICT: {rep['verdict'].upper()}   (stream rate {st.get('rate_hz', '?')} Hz, dropped {st.get('dropped', '?')}"
               f"{', SIMULATED DATA' if rep['simulated'] else ''})")
    return "\n".join(out)


def _fmt(v, width, nd):
    return f"{v:{width}.{nd}f}" if isinstance(v, (int, float)) else " " * (width - 1) + "-"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uwbkit doctor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None, help="hub URL (default $UWBKIT_URL or http://127.0.0.1:1800)")
    ap.add_argument("--seconds", type=float, default=3.0, help="observe this long before judging")
    ap.add_argument("--watch", action="store_true", help="refresh every 2 s until Ctrl-C")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    kit = Kit(a.url)
    try:
        kit.healthz()
    except KitError as e:
        print(f"[FAIL] no hub at {kit.base}: {e}\n"
              "       start one:  uwbkit hub --source serial:auto      (boards on USB)\n"
              "                   uwbkit hub --source udp:0.0.0.0:1801 (boards on Wi-Fi)\n"
              "                   uwbkit hub --source sim              (no boards: simulator)")
        return 2
    try:
        time.sleep(max(0.0, a.seconds))
        while True:
            rep = examine(kit.diag(), kit.state())
            if a.json:
                print(json.dumps(rep, indent=1))
            else:
                if a.watch:
                    print("\033[2J\033[H", end="")
                print(f"uwbkit doctor @ {kit.base}  {time.strftime('%H:%M:%S')}")
                print(render(rep))
            if not a.watch:
                return {OK: 0, WARN: 1, FAIL: 2}[rep["verdict"]]
            time.sleep(2.0)
    except KeyboardInterrupt:
        return 0
    except KitError as e:
        print(f"[FAIL] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
