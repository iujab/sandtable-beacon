"""The incident report of the command and reporting centre: one printable, self-contained page.

    report.build(session, agent_plan) -> dict      the report object (GET /api/report)
    report.to_html(report_obj) -> str               one page, inline CSS, fonts and SVG, no CDN (GET /report.html)

What it holds: the incident header (the building record's address and construction), the fixture and SIM labels,
the timeline of `beacon_event`s, one section per opt-in phone (when first heard, the geometries it went through
with their times, ring -> candidates -> fix, its last status), a small plan of the fire room drawing the final
geometry of each phone EXACTLY as published (a ring is an arc, candidates are hollow circles, a zone is a hatched
room, only a fix gets a point), the accountability table, the command agent's plan and orders, the record checks,
the stated limits and the privacy line. Typed notes are left out; phones appear only by their random id.

The session keeps only the latest state, so history comes from a `Recorder` that the server feeds with every
stream message (`RECORDER.feed(msg)`); with no feed, the report is built from the latest state alone and says so.

Visual language copied from Sandtable's reporting page (`sandtable-live-fireground-twin/web/report.css`,
sibling project, lane L01), with credit: dark on screen, a filed black-on-white document on paper.
"""
from __future__ import annotations

import base64
import copy
import html
import json
import math
import time
from functools import lru_cache
from pathlib import Path

from . import config
from .tools import STANDING_LIMITS, ToolBox, beacons_of, brief_of
from .twin import Twin

SCHEMA = "sandtable.beacon.report/1"
MAX_EVENTS = 400
MAX_EVENT_KEYS = 4 * MAX_EVENTS     # dedup memory: longer than the timeline, so a re-sent old event is still recognised
MAX_GEOMETRIES = 40


# ============================================================================ history
class Recorder:
    """Keeps what the report needs over time: beacon_events, and per phone the sequence of published geometries."""

    def __init__(self):
        self.clear()

    def clear(self) -> None:
        self.t0: float | None = None
        self.events: list[dict] = []
        self._event_keys: dict[tuple, None] = {}      # insertion-ordered set, trimmed to MAX_EVENT_KEYS
        self.phones: dict[str, dict] = {}
        self.last: dict | None = None
        self.plan: dict | None = None
        self.observations = 0

    def feed(self, msg: dict) -> None:
        """Every stream message can go here (the server's broadcaster): it keeps what it needs and ignores the rest."""
        if not isinstance(msg, dict):
            return
        typ = msg.get("type")
        if typ == "beacons":
            self.observe(msg)
        elif typ == "beacon_event":
            self.event(msg)
        elif typ == "reset":
            self.clear()
        elif typ == "agent_plan":
            self.plan = msg

    def _start(self, t: float) -> None:
        if self.t0 is None or t < self.t0:
            self.t0 = t

    def observe(self, b: dict) -> None:
        """One published Beacons object. The same object seen again (the poller, or a report built from the state the
        stream already fed) is not a new observation: producers publish a new dict at every step."""
        if not isinstance(b, dict) or b.get("type", "beacons") != "beacons" or b is self.last:
            return
        try:
            t = float(b.get("t") or time.time())
        except (TypeError, ValueError):
            t = time.time()
        self._start(t)
        self.last = b
        self.observations += 1
        for p in b.get("phones") or []:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            pid = str(p["id"])
            rec = self.phones.get(pid)
            if rec is None:
                rec = self.phones[pid] = {"id": pid, "first_t": t, "geometries": [], "last": None, "last_t": t}
            rep = p.get("report") or {}
            g = {"geometry": p.get("geometry"), "method": p.get("method"), "room_label": _phone_room_label(p),
                 "stale": bool(p.get("stale")), "safe_outside": bool(rep.get("safe_outside"))}
            prev = rec["geometries"][-1] if rec["geometries"] else None
            if prev is None or any(prev[k] != g[k] for k in ("geometry", "method", "stale", "safe_outside")):
                rec["geometries"].append({"t": t, **g})
                del rec["geometries"][:-MAX_GEOMETRIES]
            rec["last"], rec["last_t"] = copy.deepcopy(p), t

    def event(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        try:
            t = float(msg.get("t") or time.time())
        except (TypeError, ValueError):
            t = time.time()
        key = (round(t, 3), msg.get("name"), msg.get("id"), msg.get("text"))
        if key in self._event_keys:
            return
        self._event_keys[key] = None
        while len(self._event_keys) > MAX_EVENT_KEYS:
            del self._event_keys[next(iter(self._event_keys))]
        self._start(t)
        self.events.append({"t": t, "name": msg.get("name"), "id": msg.get("id"), "text": str(msg.get("text") or "")})
        del self.events[:-MAX_EVENTS]

    def pull(self, source) -> None:
        """Without a feed: take the source's latest Beacons object and the event list it keeps, if any."""
        try:
            self.observe(beacons_of(source))
        except Exception:
            pass
        self.pull_events(source)

    def pull_events(self, source) -> None:
        """The beacon_events the source keeps (the server's state has an `events` list), if any."""
        for e in list(getattr(source, "events", None) or []):
            if isinstance(e, dict) and e.get("type", "beacon_event") == "beacon_event":
                self.event(e)


RECORDER = Recorder()          # the server's recorder (see beacon/agent_routes.py)


def _phone_room_label(p: dict) -> str | None:
    if p.get("room_label"):
        return p["room_label"]
    if p.get("zone"):
        return (p["zone"] or {}).get("room_label")
    if p.get("candidates"):
        labs = sorted({c.get("room_label") for c in p["candidates"] if c.get("room_label")})
        return " / ".join(labs) or None
    return None


def _mmss(t: float | None, t0: float | None) -> str | None:
    if t is None or t0 is None:
        return None
    s = max(0, int(round(t - t0)))
    return f"{s // 60:02d}:{s % 60:02d}"


# ============================================================================ the report object
def _resolve(session):
    if callable(session) and not hasattr(session, "beacons"):
        session = session()
    return session


def _twin_of(session) -> Twin:
    tw = getattr(session, "twin", None)
    if isinstance(tw, Twin):
        return tw
    return Twin(config.twin_raw())


def _site_of(session) -> dict:
    for attr in ("site", "site_raw"):
        v = getattr(session, attr, None)
        if isinstance(v, dict) and v:
            return v
    return config.site_raw()


def _labels_of(session) -> dict:
    v = getattr(session, "labels", None)
    return v if isinstance(v, dict) and v else config.labels()


def _final_geometry(p: dict) -> dict:
    """The phone's geometry exactly as published, only the parts that belong to its geometry (x, y only for a fix)."""
    g = p.get("geometry") or "none"
    out = {"geometry": g, "method": p.get("method"), "stale": bool(p.get("stale"))}
    if g == "fix":
        out.update({"x": p.get("x"), "y": p.get("y"), "sigma_m": p.get("sigma_m")})
    elif g == "ring" and p.get("ring"):
        r = p["ring"]
        out["ring"] = {k: r.get(k) for k in ("puck", "cx", "cy", "r_m", "sigma_m", "arcs", "kind")}
    elif g == "candidates":
        out["candidates"] = [{k: c.get(k) for k in ("x", "y", "sigma_m", "weight", "room_label")} for c in p.get("candidates") or []]
    elif g == "zone" and p.get("zone"):
        z = p["zone"]
        out["zone"] = {k: z.get(k) for k in ("room", "room_label", "band", "puck", "prob", "polygon")}
    out["pruned"] = [{"x": q.get("x"), "y": q.get("y"), "reason": q.get("reason"), "by": q.get("by")} for q in p.get("pruned") or []
                     if isinstance(q, dict) and q.get("x") is not None]
    return out


def _plan_frame(twin: Twin, b: dict, margin: float = 3.0) -> dict | None:
    room = b.get("room")
    if room not in twin._rooms:
        rooms = [p.get("room") for p in b.get("phones") or [] if p.get("room") in twin._rooms]
        room = rooms[0] if rooms else None
    if room is None:
        return None
    floor = twin.room(room)["floor"]
    poly = twin.room(room)["polygon"]
    out = twin.floor(floor)["outline"]
    fx0, fy0 = min(q[0] for q in out), min(q[1] for q in out)
    fx1, fy1 = max(q[0] for q in out), max(q[1] for q in out)
    x0 = max(fx0 - 0.5, min(q[0] for q in poly) - margin)
    y0 = max(fy0 - 0.5, min(q[1] for q in poly) - margin)
    x1 = min(fx1 + 0.5, max(q[0] for q in poly) + margin)
    y1 = min(fy1 + 0.5, max(q[1] for q in poly) + margin)

    def near(pts):
        return any(x0 - 1 <= q[0] <= x1 + 1 and y0 - 1 <= q[1] <= y1 + 1 for q in pts) or (
            min(q[0] for q in pts) <= x0 and max(q[0] for q in pts) >= x1 and min(q[1] for q in pts) <= y0 and max(q[1] for q in pts) >= y1)

    rooms = [{"id": r["id"], "label": r.get("label"), "kind": r.get("kind"), "suite": r.get("suite"), "polygon": r["polygon"]}
             for r in twin.data["rooms"] if r["floor"] == floor and near(r["polygon"])]
    walls = [[w["a"], w["b"]] for w in twin.data.get("walls", []) if w["floor"] == floor and near([w["a"], w["b"]])]
    doors = [{"at": d["at"], "width_m": d.get("width_m", 0.9)} for d in twin.data.get("doors", []) if d["floor"] == floor and near([d["at"]])]
    return {"room": room, "room_label": twin.room(room)["label"], "floor": floor, "bbox": [x0, y0, x1, y1],
            "rooms": rooms, "walls": walls, "doors": doors,
            "pucks": [{"id": p["id"], "x": p["x"], "y": p["y"]} for p in b.get("pucks") or [] if p.get("x") is not None]}


def build(session, agent_plan: dict | None = None, recorder: Recorder | None = None) -> dict:
    """The incident report object. `session`: a BeaconSession, the server's state, anything with beacons(), or a Beacons dict."""
    session = _resolve(session)
    rec = recorder if recorder is not None else RECORDER
    b = beacons_of(session) or (rec.last or {})
    if b:
        rec.observe(b)
    if not isinstance(session, dict):
        rec.pull_events(session)            # its Beacons object was observed just above: once, not twice
    twin, site, labels = _twin_of(session), _site_of(session), _labels_of(session)
    plan = agent_plan if agent_plan is not None else rec.plan
    prop = site.get("property") or {}
    floor = b.get("floor") or twin.exercise_floor
    try:
        floor_label = str(twin.floor(floor).get("label") or floor)
    except KeyError:
        floor_label = str(floor)
    simulated, sample = bool(b.get("simulated")), bool(b.get("sample"))
    stamp = "SAMPLE" if sample else "SIMULATED" if simulated else "LIVE"
    t0 = rec.t0
    now_ids = {p.get("id") for p in b.get("phones") or []}

    phones = []
    for pid, r in sorted(rec.phones.items(), key=lambda kv: (len(kv[0]), kv[0])):
        last = r["last"] or {}
        rep = last.get("report") or {}
        phones.append({
            "id": pid, "first_heard": _mmss(r["first_t"], t0), "heard_now": pid in now_ids,
            "sequence": [{"at": _mmss(g["t"], t0), "geometry": g["geometry"], "method": g["method"], "where": g["room_label"],
                          "note": "safe outside" if g["safe_outside"] else ("stale" if g["stale"] else None)} for g in r["geometries"]],
            "last": {"status": rep.get("status"), "people": rep.get("people"), "can_move": rep.get("can_move"),
                     "battery_pct": rep.get("battery_pct"), "floor_reported": rep.get("floor_reported"), "home_suite": rep.get("home_suite"),
                     "safe_outside": bool(rep.get("safe_outside")), "platform": rep.get("platform"), "model": rep.get("model"),
                     "text": last.get("text"), "confidence": last.get("confidence"),
                     "assumptions": [a.get("text") for a in last.get("assumptions") or [] if isinstance(a, dict) and a.get("text")]},
            "final": _final_geometry(last)})

    acc = b.get("accountability") or {}
    keep = ("suite", "room_label", "tenant", "industry", "vacant", "life_hazard", "expected", "app_registered", "safe_outside",
            "phones_opt_in", "people_reported", "located", "cold_devices", "cold_range", "unaccounted", "state", "priority", "why")
    accountability = {"source": acc.get("source"), "suites": [{k: s.get(k) for k in keep} for s in acc.get("suites") or []],
                      "totals": acc.get("totals"), "priorities": acc.get("priorities"), "next_puck": acc.get("next_puck"),
                      "assumptions": acc.get("assumptions"), "text": acc.get("text")} if acc else None

    detected = []
    if acc:
        try:
            detected = ToolBox(brief_of(b), twin, site).disagreements()
        except Exception:
            detected = [{"suite": c.get("suite"), "evidence": [c.get("text")]} for c in acc.get("record_checks") or []]
    limits = list(dict.fromkeys([str(x) for x in b.get("limits") or []] + list(STANDING_LIMITS)))
    priv = (b.get("privacy") or {}).get("text") or "Opt-in phones only; no identities kept for devices heard without the app."
    events = [{"at": _mmss(e["t"], t0), "name": e["name"], "id": e["id"], "text": e["text"]} for e in sorted(rec.events, key=lambda e: e["t"])]
    return {
        "type": "beacon_report", "schema": SCHEMA, "generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "mode": {"stamp": stamp, "simulated": simulated, "sample": sample, "source": b.get("source"), "rig_scale": (b.get("rig") or {}).get("scale", 1.0)},
        "header": {"address": prop.get("address"), "city": ", ".join(x for x in (prop.get("city"), prop.get("state")) if x),
                   "name": prop.get("name"), "property_type": prop.get("property_type"), "construction": prop.get("construction"),
                   "year_built": prop.get("year_built"), "stories": prop.get("stories"), "rba_sf": prop.get("rba_sf"),
                   "floor": floor_label, "fire_room": b.get("room_label"), "pucks": len(b.get("pucks") or []),
                   "phones_opt_in": len(phones), "property_manager": prop.get("property_manager")},
        "labels": {"record": (labels.get("costar") or {}).get("text"), "twin": (labels.get("matterport") or {}).get("text"),
                   "data": {"SAMPLE": "SAMPLE: the hand-made sample of the feed, not a measurement.",
                            "SIMULATED": "SIMULATED: phones, pucks and devices come from the simulator's stated noise model, not from a measurement.",
                            "LIVE": "LIVE: opt-in phones reporting through the app."}[stamp]},
        "history": {"source": ("recorded from the stream (events and geometries)" if rec.events else
                               "geometries polled from the latest state; no event timeline was fed to the report" if rec.observations > 1 else
                               "latest state only (no history was recorded)"),
                    "observations": rec.observations, "events": len(rec.events)},
        "timeline": events,
        "phones": phones,
        "plan": _plan_frame(twin, b),
        "accountability": accountability,
        "agent": plan,
        "record_checks": {"detected": detected, "filed": list((plan or {}).get("record_checks") or [])},
        "limits": limits,
        "privacy": priv + " Typed notes are not included in this report; phones appear only by their random id.",
    }


# ============================================================================ HTML
def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


@lru_cache(maxsize=1)
def _font_css() -> str:
    """The vendored fonts as data: URLs (read and encoded once per process; about a third of to_html's time otherwise)."""
    fonts =[("Barlow Condensed", 700, "barlow-condensed-700"), ("Barlow Condensed", 600, "barlow-condensed-600"),
             ("Barlow", 400, "barlow-400"), ("Barlow", 600, "barlow-600"), ("JetBrains Mono", 400, "jetbrains-mono-400")]
    out = []
    for fam, w, name in fonts:
        p = Path(config.WEB_DIR) / "vendor" / "fonts" / f"{name}.woff2"
        if p.is_file():
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            out.append(f'@font-face {{ font-family: "{fam}"; font-weight: {w}; src: url(data:font/woff2;base64,{data}) format("woff2"); }}')
    return "\n".join(out)


CSS = """
:root {
  --bg: #0b0e12; --sheet: #0f1318; --panel: #141a21; --line: #242c36; --line-2: #313b47;
  --text: #e8ecf0; --soft: #c4ccd5; --muted: #8d99a6; --faint: #5d6875;
  --brand: #ffc247; --alarm: #ff3b30; --alarm-soft: #ffb4ae; --ok: #3fcf8e; --warn: #ff9f0a;
  --person: #ff4fa3; --person-soft: #ffc4e2; --rit: #4fd1ff;
  --cond: "Barlow Condensed", "Arial Narrow", "Helvetica Neue", Arial, sans-serif;
  --sans: "Barlow", "Helvetica Neue", "Segoe UI", Arial, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace;
  --p-suite: #1a2129; --p-core: #222b36; --p-corr: #2a3441; --p-stair: #173326; --p-wall: #9aa7b5; --p-label: #c3ccd6; --p-sub: #7d8b99;
  --p-fire: rgba(255,59,48,.10); --p-ink: #e8ecf0; --p-halo: #0b0e12;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body { margin: 0; background: var(--bg); color: var(--text); font: 400 15px/1.45 var(--sans); }
.sheet { max-width: 1180px; margin: 22px auto 60px; padding: 0 22px; }
.head { border: 1px solid var(--line); border-top: 3px solid var(--brand); border-radius: 8px; background: var(--sheet); padding: 18px 22px 0; }
.head-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.prod { font: 700 15px/1 var(--cond); letter-spacing: .14em; text-transform: uppercase; color: var(--soft); display: flex; align-items: center; gap: 9px; }
.mark { width: 22px; height: 22px; flex: none; border: 2px solid var(--brand); border-radius: 4px; position: relative; display: inline-block; }
.mark::before, .mark::after { content: ""; position: absolute; background: var(--brand); }
.mark::before { left: 3px; right: 3px; top: 8px; height: 2px; }
.mark::after { left: 7px; width: 5px; height: 5px; top: 2px; border-radius: 50%; box-shadow: 0 0 8px var(--brand); }
.doc-kind { font: 600 15px/1 var(--cond); letter-spacing: .06em; color: var(--muted); padding-left: 10px; border-left: 1px solid var(--line-2); }
.marks { margin-left: auto; display: flex; gap: 6px; }
.stamp { font: 700 13px/1 var(--cond); letter-spacing: .16em; text-transform: uppercase; padding: 6px 9px 5px; border: 2px solid var(--warn); color: var(--warn); border-radius: 3px; }
.stamp.SAMPLE { background: var(--warn); color: #1a1405; }
.stamp.LIVE { border-color: var(--ok); color: var(--ok); }
.addr { font: 700 40px/1.02 var(--cond); letter-spacing: .01em; margin: 16px 0 2px; }
.addr-sub { color: var(--soft); font-size: 15px; }
.meta { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: 16px -22px 0; border-top: 1px solid var(--line); }
.meta > div { padding: 10px 22px 11px; border-right: 1px solid var(--line); min-width: 0; }
.meta > div:last-child { border-right: 0; }
.meta dt { font: 600 12px/1.2 var(--cond); letter-spacing: .08em; color: var(--muted); text-transform: uppercase; }
.meta dd { margin: 3px 0 0; font: 600 17px/1.2 var(--sans); overflow-wrap: anywhere; }
.honesty { margin: 0 -22px; padding: 11px 22px 13px; border-top: 1px solid var(--line); background: rgba(255,159,10,.05); border-radius: 0 0 8px 8px; font-size: 13.5px; color: var(--soft); display: grid; grid-template-columns: 128px 1fr; gap: 4px 14px; }
.honesty .hk { font: 700 13px/1.4 var(--cond); letter-spacing: .06em; color: var(--warn); text-transform: uppercase; }
.honesty ul { margin: 0; padding: 0; list-style: none; }
.blk { display: grid; grid-template-columns: 52px minmax(0, 1fr); gap: 0 14px; margin-top: 30px; }
.blk-no { font: 700 30px/1 var(--cond); color: var(--line-2); text-align: right; padding-top: 1px; }
.blk-h { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; border-bottom: 1px solid var(--line-2); padding-bottom: 6px; margin-bottom: 12px; }
.blk-h h2 { margin: 0; font: 700 25px/1.05 var(--cond); letter-spacing: .02em; }
.blk-h .aside { color: var(--muted); font-size: 13.5px; }
.none { color: var(--muted); font-style: italic; margin: 4px 0; }
.summary { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 22px; align-items: start; }
.summary p { margin: 0 0 8px; font-size: 17px; line-height: 1.55; max-width: 68ch; }
.figs { display: grid; grid-template-columns: 1fr 1fr; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.fig { padding: 9px 12px 10px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); background: var(--sheet); }
.fig:nth-child(2n) { border-right: 0; }
.fig .n { font: 700 30px/1 var(--cond); }
.fig .l { font-size: 12.5px; color: var(--muted); margin-top: 3px; line-height: 1.3; }
.fig.range { grid-column: 1 / -1; border-right: 0; border-bottom: 0; }
.fig.range .n { color: var(--warn); }
.src { display: inline-block; font: 600 12px/1 var(--cond); letter-spacing: .08em; padding: 4px 7px 3px; border-radius: 3px; border: 1px solid var(--line-2); color: var(--soft); vertical-align: 2px; text-transform: uppercase; }
.src.replay { border-color: #3c5a7a; color: #9cc8ff; } .src.live { border-color: #1f5f44; color: var(--ok); } .src.fallback { border-color: #6b4a12; color: var(--warn); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.tbl th { font: 600 12.5px/1.15 var(--cond); letter-spacing: .06em; color: var(--muted); text-align: left; vertical-align: bottom; padding: 6px 8px; border-bottom: 1px solid var(--line-2); text-transform: uppercase; }
.tbl td { padding: 8px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.tbl .num { text-align: right; font-variant-numeric: tabular-nums; }
.tbl .t { font: 400 12.5px var(--mono); white-space: nowrap; color: var(--soft); }
.id { font: 700 19px/1 var(--cond); letter-spacing: .03em; display: block; }
.by { font-size: 12.5px; color: var(--muted); display: block; margin-top: 3px; }
.st { display: inline-block; font: 700 12px/1 var(--cond); letter-spacing: .08em; text-transform: uppercase; padding: 3px 6px 2px; border-radius: 3px; border: 1px solid var(--line-2); color: var(--soft); white-space: nowrap; }
.st.trapped, .st.injured { color: #fff; background: #8f1d16; border-color: var(--alarm); }
.st.sheltering { color: var(--warn); border-color: #6b4a12; }
.st.ok, .st.out { color: var(--ok); border-color: #1f5f44; }
.seq { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; }
.seq li { font-size: 12.5px; white-space: nowrap; }
.seq li + li::before { content: "\\2192"; color: var(--faint); margin-right: 6px; }
.seq .g { font: 700 13px var(--cond); letter-spacing: .05em; text-transform: uppercase; }
.seq .g.fix { color: var(--ok); } .seq .g.ring, .seq .g.candidates { color: var(--person-soft); } .seq .g.zone { color: var(--warn); }
.seq .tm { font: 400 11.5px var(--mono); color: var(--muted); margin-right: 4px; }
.plan-wrap { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 22px; align-items: start; }
.plan-fig { margin: 0; background: #0a0d11; border: 1px solid var(--line); border-radius: 6px; padding: 6px; }
.plan-fig svg { display: block; width: 100%; height: auto; max-height: 640px; }
.plan-fig figcaption { font-size: 12.5px; color: var(--muted); padding: 6px 6px 2px; }
.key ul { list-style: none; margin: 0; padding: 0; }
.key li { display: grid; grid-template-columns: 30px 1fr; gap: 8px; align-items: start; font-size: 13px; padding: 3px 0; }
.key li svg { width: 30px; height: 18px; display: block; margin-top: 1px; }
.key .notdrawn { color: var(--muted); font-size: 12.5px; margin-top: 8px; }
.pl-r { stroke: none; } .pl-r.suite { fill: var(--p-suite); } .pl-r.corridor { fill: var(--p-corr); } .pl-r.stair { fill: var(--p-stair); }
.pl-r.lobby, .pl-r.elevator, .pl-r.restroom, .pl-r.mechanical { fill: var(--p-core); }
.pl-r.fire { fill: var(--p-fire); stroke: var(--alarm); stroke-width: .08; }
.pl-wall { stroke: var(--p-wall); stroke-width: .14; stroke-linecap: square; }
.pl-door { stroke: var(--p-suite); stroke-width: .3; }
.pl-sn { font: 700 .44px var(--cond); fill: var(--p-label); text-anchor: start; letter-spacing: .02em; }
.pl-puck { fill: var(--p-ink); stroke: var(--p-halo); stroke-width: .06; }
.pl-puck-t { font: 400 .36px var(--mono); fill: var(--p-ink); paint-order: stroke; stroke: var(--p-halo); stroke-width: .14px; }
.ph-pt { fill: var(--person); }
.ph-sigma { fill: none; stroke: var(--person); stroke-width: .06; }
.ph-ring { fill: none; stroke: var(--person); stroke-opacity: .22; stroke-linecap: butt; }
.ph-ring-line { fill: none; stroke: var(--person); stroke-width: .07; }
.ph-cand { fill: none; stroke: var(--person); stroke-width: .1; }
.ph-cand-link { stroke: var(--person); stroke-width: .05; stroke-dasharray: .25 .25; opacity: .7; }
.ph-ghost { stroke: var(--p-sub); stroke-width: .08; }
.ph-zone { fill: url(#hz); stroke: var(--person); stroke-width: .08; }
.ph-t { font: 700 .4px var(--cond); fill: var(--person-soft); paint-order: stroke; stroke: var(--p-halo); stroke-width: .18px; }
.pl-scale line { stroke: var(--p-ink); stroke-width: .08; } .pl-scale text { font: 500 .5px var(--sans); fill: var(--p-ink); }
.tl { list-style: none; margin: 0; padding: 0; columns: 2; column-gap: 30px; }
.tl li { display: grid; grid-template-columns: 52px 98px 1fr; gap: 8px; padding: 4px 0; font-size: 13.5px; break-inside: avoid; border-bottom: 1px dashed var(--line); }
.tl .t { font: 400 12.5px/1.6 var(--mono); color: var(--soft); }
.tl .kind { font: 600 12px/1.6 var(--cond); letter-spacing: .06em; color: var(--muted); text-transform: uppercase; }
.tl li.k-phone_fix .kind, .tl li.k-phone_new .kind { color: var(--person-soft); }
.acc .suite { font: 700 18px/1 var(--cond); }
.acc .lh { font-size: 12px; color: var(--alarm-soft); display: block; margin-top: 2px; }
.acc .basis { display: block; font-size: 11.5px; color: var(--muted); line-height: 1.25; margin-top: 2px; }
.acc .vac { color: var(--faint); }
.pri { font: 700 18px/1 var(--cond); } .pri.p1 { color: var(--alarm); } .pri.p2 { color: var(--warn); }
.agent { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }
.agent h4 { font: 600 13.5px var(--cond); letter-spacing: .06em; color: var(--muted); margin: 10px 0 4px; text-transform: uppercase; }
.agent ol, .agent ul { margin: 0; padding-left: 18px; font-size: 14px; }
.agent li { margin: 0 0 6px; }
.orders .to { font: 700 15px var(--cond); letter-spacing: .04em; margin-right: 6px; }
.orders .pr { font: 600 11px var(--cond); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin-right: 6px; }
.orders .pr.emergency { color: var(--alarm); } .orders .pr.urgent { color: var(--warn); }
.limits { margin: 0; padding-left: 20px; columns: 2; column-gap: 30px; }
.limits li { margin: 0 0 5px; break-inside: avoid; }
.priv { margin: 12px 0 0; padding: 10px 12px; border-left: 3px solid var(--rit); color: var(--soft); font-size: 13.5px; }
.foot { margin-top: 36px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 4px 18px; }
@media screen and (max-width: 980px) {
  .summary, .plan-wrap, .agent { grid-template-columns: 1fr; }
  .meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .meta > div { border-bottom: 1px solid var(--line); }
  .tl, .limits { columns: 1; }
  .blk { grid-template-columns: 1fr; } .blk-no { text-align: left; font-size: 20px; }
  .scroll { overflow-x: auto; }
  .addr { font-size: 32px; }
}
@media print {
  :root { --bg: #fff; --sheet: #fff; --panel: #fff; --line: #c9c9c9; --line-2: #8a8a8a; --text: #000; --soft: #1a1a1a; --muted: #4a4a4a; --faint: #777;
    --brand: #000; --alarm: #c00000; --alarm-soft: #a00000; --ok: #000; --warn: #000; --person: #a0005a; --person-soft: #a0005a; --rit: #00618a;
    --p-suite: #fff; --p-core: #ececec; --p-corr: #f6f6f6; --p-stair: #e2efe7; --p-wall: #000; --p-label: #000; --p-sub: #444; --p-fire: rgba(192,0,0,.06); --p-ink: #000; --p-halo: #fff; }
  html, body { background: #fff; color: #000; font-size: 9.6pt; line-height: 1.38; }
  .sheet { max-width: none; margin: 0; padding: 0; }
  .head { border: 0; border-top: 2.5pt solid #000; border-radius: 0; padding: 8pt 0 0; }
  .addr { font-size: 25pt; } .honesty { margin: 0; background: #fff; grid-template-columns: 70pt 1fr; font-size: 8.6pt; }
  .meta { margin: 9pt 0 0; } .meta > div { padding: 5pt 8pt 6pt; } .meta dd { font-size: 11pt; }
  .stamp, .stamp.SAMPLE { background: #fff; border-color: #000; color: #000; }
  .mark { border-color: #000; } .mark::before, .mark::after { background: #000; box-shadow: none; }
  .blk { margin-top: 13pt; grid-template-columns: 22pt minmax(0, 1fr); gap: 0 8pt; } .blk-no { color: #000; font-size: 15pt; }
  .blk-h { border-bottom: 1pt solid #000; break-after: avoid; } .blk-h h2 { font-size: 14.5pt; }
  .tbl { font-size: 8.5pt; } .tbl th { color: #000; border-bottom: 1pt solid #000; } .tbl tr { break-inside: avoid; }
  .st { color: #000 !important; background: #fff !important; border-color: #000 !important; }
  .plan-blk { break-inside: avoid; } .plan-fig { background: #fff; border-radius: 0; } .plan-fig svg { max-height: 120mm; }
  .src { border-color: #000 !important; color: #000 !important; } .foot { display: none; }
}
"""


def _svg_plan(rep: dict) -> str:
    """The fire room and its surroundings, each phone's final geometry exactly as published. Never a point for a ring."""
    P = rep.get("plan")
    if not P:
        return ""
    x0, y0, x1, y1 = P["bbox"]
    W, H = x1 - x0, y1 - y0
    X = lambda x: f"{float(x) - x0:.2f}"
    Y = lambda y: f"{y1 - float(y):.2f}"
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.2f} {H:.2f}" role="img" '
         f'aria-label="Plan of {_e(P["room_label"])} with the final geometry of each phone">',
         '<defs><pattern id="hz" width="0.5" height="0.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<line x1="0" y1="0" x2="0" y2="0.5" stroke="#ff4fa3" stroke-width="0.08" stroke-opacity="0.6"/></pattern></defs>']
    for r in P["rooms"]:
        cls = f'pl-r {r["kind"]}' + (" fire" if r["id"] == P["room"] else "")
        pts = " ".join(f"{X(q[0])},{Y(q[1])}" for q in r["polygon"])
        o.append(f'<polygon class="{_e(cls)}" points="{pts}"/>')
    for p in rep.get("phones") or []:                                    # zones under the walls
        f = p.get("final") or {}
        if f.get("geometry") == "zone" and (f.get("zone") or {}).get("polygon"):
            pts = " ".join(f"{X(q[0])},{Y(q[1])}" for q in f["zone"]["polygon"])
            o.append(f'<polygon class="ph-zone" data-phone="{_e(p["id"])}" data-geometry="zone" points="{pts}"/>')
    for a, b in P["walls"]:
        o.append(f'<line class="pl-wall" x1="{X(a[0])}" y1="{Y(a[1])}" x2="{X(b[0])}" y2="{Y(b[1])}"/>')
    for d in P["doors"]:                                                 # a door cuts its wall: draw the opening along the wall
        at, w = d["at"], float(d.get("width_m") or 0.9)
        best = None
        for a, b in P["walls"]:
            L = math.dist(a, b)
            if L < 1e-6:
                continue
            k = max(0.0, min(1.0, ((at[0] - a[0]) * (b[0] - a[0]) + (at[1] - a[1]) * (b[1] - a[1])) / (L * L)))
            dd = math.dist(at, (a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1])))
            if best is None or dd < best[0]:
                best = (dd, ((b[0] - a[0]) / L, (b[1] - a[1]) / L))
        if best and best[0] < 0.2:
            ux, uy = best[1]
            o.append(f'<line class="pl-door" x1="{X(at[0] - ux * w / 2)}" y1="{Y(at[1] - uy * w / 2)}" x2="{X(at[0] + ux * w / 2)}" y2="{Y(at[1] + uy * w / 2)}"/>')
    for r in P["rooms"]:
        if r["kind"] == "suite":
            lx = max(min(q[0] for q in r["polygon"]), x0) + 0.35
            ly = min(max(q[1] for q in r["polygon"]), y1) - 0.75
            if x0 <= lx < x1 - 2 and y0 + 0.5 < ly <= y1:
                o.append(f'<text class="pl-sn" x="{X(lx)}" y="{Y(ly)}">{_e(r["label"])}</text>')
    for p in rep.get("phones") or []:
        f = p.get("final") or {}
        g, pid = f.get("geometry"), _e(p["id"])
        if g == "ring" and f.get("ring"):
            r = f["ring"]
            cx, cy, rad = float(r["cx"]), float(r["cy"]), float(r["r_m"])
            band = max(0.08, 2 * float(r.get("sigma_m") or 0.05))
            longest = None
            for a0, a1 in r.get("arcs") or [[0, 360]]:
                a0, a1 = float(a0), float(a1)
                a1 = a1 + 360.0 if a1 < a0 else a1
                n = max(2, int((a1 - a0) / 3) + 1)
                pts = " ".join(f"{X(cx + rad * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))))},"
                               f"{Y(cy + rad * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1))))}" for i in range(n))
                o.append(f'<polyline class="ph-ring" data-phone="{pid}" data-geometry="ring" stroke-width="{band:.2f}" points="{pts}"/>')
                o.append(f'<polyline class="ph-ring-line" data-phone="{pid}" data-geometry="ring" points="{pts}"/>')
                if longest is None or a1 - a0 > longest[1] - longest[0]:
                    longest = (a0, a1)
            am = math.radians((longest[0] + longest[1]) / 2)
            lx = min(max(cx + (rad + 0.45) * math.cos(am), x0 + 1.4), x1 - 1.4)
            ly = min(max(cy + (rad + 0.45) * math.sin(am), y0 + 0.6), y1 - 0.4)
            o.append(f'<text class="ph-t" x="{X(lx)}" y="{Y(ly)}" text-anchor="middle">{pid} ring {rad:.1f} m</text>')
        elif g == "candidates" and f.get("candidates"):
            cs = f["candidates"]
            for a, b in zip(cs, cs[1:]):
                o.append(f'<line class="ph-cand-link" x1="{X(a["x"])}" y1="{Y(a["y"])}" x2="{X(b["x"])}" y2="{Y(b["y"])}"/>')
            for i, c in enumerate(cs, start=1):
                rr = max(0.25, float(c.get("sigma_m") or 0.3))
                o.append(f'<circle class="ph-cand" data-phone="{pid}" data-geometry="candidates" cx="{X(c["x"])}" cy="{Y(c["y"])}" r="{rr:.2f}"/>')
                o.append(f'<text class="ph-t" x="{X(float(c["x"]) + rr + 0.15)}" y="{Y(float(c["y"]) - 0.2)}">{pid} {i}/{len(cs)}'
                         + (f' ({float(c["weight"]):.1f})' if c.get("weight") is not None else "") + "</text>")
        elif g == "fix" and f.get("x") is not None:
            s = max(0.15, float(f.get("sigma_m") or 0.3))
            o.append(f'<circle class="ph-sigma" data-phone="{pid}" data-geometry="fix" cx="{X(f["x"])}" cy="{Y(f["y"])}" r="{s:.2f}"/>')
            o.append(f'<circle class="ph-pt" data-phone="{pid}" data-geometry="fix" cx="{X(f["x"])}" cy="{Y(f["y"])}" r="0.14"/>')
            o.append(f'<text class="ph-t" x="{X(float(f["x"]) + s + 0.15)}" y="{Y(float(f["y"]) - 0.2)}">{pid}</text>')
        elif g == "zone" and f.get("zone"):
            z = f["zone"]
            poly = z.get("polygon") or []
            if poly:
                cx = sum(q[0] for q in poly) / len(poly)
                cy = sum(q[1] for q in poly) / len(poly)
                o.append(f'<text class="ph-t" x="{X(cx)}" y="{Y(cy + 1.2)}" text-anchor="middle">{pid} zone ({_e(z.get("band"))})</text>')
        for q in f.get("pruned") or []:
            if g in ("fix", "candidates") and q.get("x") is not None:
                gx, gy = float(q["x"]), float(q["y"])
                o.append(f'<path class="ph-ghost" data-phone="{pid}" data-geometry="pruned" d="M{X(gx - .25)} {Y(gy - .25)} L{X(gx + .25)} {Y(gy + .25)} '
                         f'M{X(gx - .25)} {Y(gy + .25)} L{X(gx + .25)} {Y(gy - .25)}"/>')
    for pk in P["pucks"]:
        o.append(f'<rect class="pl-puck" x="{float(X(pk["x"])) - .15:.2f}" y="{float(Y(pk["y"])) - .15:.2f}" width="0.3" height="0.3"/>')
        o.append(f'<text class="pl-puck-t" x="{float(X(pk["x"])) + .25:.2f}" y="{float(Y(pk["y"])) + .4:.2f}">{_e(pk["id"])}</text>')
    o.append(f'<g class="pl-scale"><line x1="0.6" y1="{H - 0.6:.2f}" x2="2.6" y2="{H - 0.6:.2f}"/>'
             f'<line x1="0.6" y1="{H - 0.75:.2f}" x2="0.6" y2="{H - 0.45:.2f}"/><line x1="2.6" y1="{H - 0.75:.2f}" x2="2.6" y2="{H - 0.45:.2f}"/>'
             f'<text x="0.6" y="{H - 0.9:.2f}">2 m</text></g>')
    o.append("</svg>")
    return "".join(o)


def _key() -> str:
    def sw(inner):
        return f'<svg viewBox="0 0 30 18" xmlns="http://www.w3.org/2000/svg">{inner}</svg>'
    items = [
        (sw('<circle cx="15" cy="9" r="6" fill="none" stroke="#ff4fa3" stroke-width="1"/><circle cx="15" cy="9" r="2.4" fill="#ff4fa3"/>'),
         "<b>Fix</b>: one point and its 1-sigma circle. The only geometry drawn as a point."),
        (sw('<circle cx="9" cy="9" r="4" fill="none" stroke="#ff4fa3" stroke-width="1.4"/><circle cx="22" cy="9" r="4" fill="none" stroke="#ff4fa3" stroke-width="1.4"/>'),
         "<b>Candidates</b>: separate spots, any may be right (weights in brackets)."),
        (sw('<path d="M3 15 Q15 0 27 15" fill="none" stroke="#ff4fa3" stroke-opacity=".3" stroke-width="5"/><path d="M3 15 Q15 0 27 15" fill="none" stroke="#ff4fa3" stroke-width="1"/>'),
         "<b>Ring</b>: a distance from one puck, drawn on the arc the twin leaves possible. Not a position."),
        (sw('<rect x="3" y="3" width="24" height="12" fill="none" stroke="#ff4fa3"/><path d="M5 15 L15 3 M13 15 L23 3" stroke="#ff4fa3" stroke-width=".8"/>'),
         "<b>Zone</b>: the room, from Bluetooth signal strength."),
        (sw('<path d="M11 5 L19 13 M11 13 L19 5" stroke="#7d8b99" stroke-width="1.4"/>'), "<b>Pruned</b>: a spot the twin or a statement removed."),
        (sw('<rect x="12" y="6" width="6" height="6" fill="#e8ecf0"/>'), "<b>Puck</b> (position as placed)."),
    ]
    return "<ul>" + "".join(f"<li>{s}<span>{t}</span></li>" for s, t in items) + "</ul>"


def _backend_badge(backend: str | None) -> str:
    cls = {"replay": "replay", "claude-cli": "live", "anthropic": "live", "reflex-fallback": "fallback"}.get(backend or "", "")
    return f'<span class="src {cls}">{_e(backend or "none")}</span>'


def to_html(rep: dict) -> str:
    """The report as one printable, self-contained HTML page."""
    h, mode, acc = rep.get("header") or {}, rep.get("mode") or {}, rep.get("accountability") or {}
    stamp = mode.get("stamp") or "SIMULATED"
    plan = rep.get("agent")
    tot = (acc.get("totals") or {}) if acc else {}
    sub = " · ".join(_e(x) for x in (h.get("city"), h.get("property_type"), h.get("construction"),
                                     f"built {h['year_built']}" if h.get("year_built") else None,
                                     f"{h['stories']} floors" if h.get("stories") else None) if x)
    meta = [("Fire floor", h.get("floor")), ("Fire room", h.get("fire_room") or "none yet"), ("Pucks", h.get("pucks")),
            ("Opt-in phones", h.get("phones_opt_in")), ("Report generated", rep.get("generated"))]
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<title>Beacon incident report · {_e(h.get("address"))}</title>',
           f"<style>{_font_css()}\n{CSS}</style></head><body><main class=\"sheet\">"]
    out.append('<header class="head"><div class="head-top"><span class="prod"><span class="mark"></span>Sandtable Beacon</span>'
               '<span class="doc-kind">Incident report: occupants\' phones</span>'
               f'<span class="marks"><span class="stamp {_e(stamp)}">{_e(stamp)}</span></span></div>'
               f'<div class="addr">{_e(h.get("address") or "Address not in the record")}</div><div class="addr-sub">{sub}</div>'
               '<dl class="meta">' + "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in meta) + "</dl>"
               '<div class="honesty"><span class="hk">Labels</span><ul>'
               + "".join(f"<li>{_e(x)}</li>" for x in ((rep.get("labels") or {}).get("data"), (rep.get("labels") or {}).get("record"),
                                                       (rep.get("labels") or {}).get("twin"),
                                                       f"History: {(rep.get('history') or {}).get('source')}.") if x)
               + "</ul></div></header>")

    # 1. summary + plan of the fire room
    un = [tot.get("unaccounted_low"), tot.get("unaccounted_high")]
    summary = (plan or {}).get("summary") or acc.get("text") or "No command-agent plan and no accountability table yet."
    figs = (f'<div class="figs"><div class="fig"><div class="n">{_e(tot.get("people_reported", 0))}</div><div class="l">people reported by {_e(tot.get("phones_opt_in", 0))} phones inside</div></div>'
            f'<div class="fig"><div class="n">{_e(tot.get("located", 0))}</div><div class="l">phones located to a point (fix)</div></div>'
            f'<div class="fig"><div class="n">{_e(tot.get("safe_outside", 0))}</div><div class="l">reported safe outside</div></div>'
            f'<div class="fig"><div class="n">{_e(tot.get("expected_people", "?"))}</div><div class="l">expected on the floor by the record (estimate, up to {_e(tot.get("expected_high", "?"))})</div></div>'
            f'<div class="fig range"><div class="n">{_e(un[0])} to {_e(un[1])}</div><div class="l">people unaccounted for: a range, because head counts are estimates and devices are not people</div></div></div>') if tot else ""
    not_drawn = [p["id"] for p in rep.get("phones") or [] if (p.get("final") or {}).get("geometry") not in ("fix", "candidates", "ring", "zone")]
    out.append(f'<section class="blk"><div class="blk-no">1</div><div><div class="blk-h"><h2>Summary</h2>'
               f'<span class="aside">{_backend_badge((plan or {}).get("backend")) if plan else "no command-agent plan"}</span></div>'
               f'<div class="summary"><div><p>{_e(summary)}</p></div>{figs}</div></div></section>')
    svg = _svg_plan(rep)
    out.append('<section class="blk plan-blk"><div class="blk-no">2</div><div><div class="blk-h"><h2>Fire room: final geometry of each phone</h2>'
               f'<span class="aside">as published, {_e(stamp.lower())}</span></div>'
               + (f'<div class="plan-wrap"><figure class="plan-fig">{svg}<figcaption>{_e((rep.get("plan") or {}).get("room_label"))} and surroundings, '
                  f'floor {_e(h.get("floor"))}. Twin: synthetic interior fixture.</figcaption></figure><div class="key">{_key()}'
                  + (f'<p class="notdrawn">Not drawn (nothing spatial, or safe outside): {_e(", ".join(not_drawn))}</p>' if not_drawn else "")
                  + "</div></div>" if svg else '<p class="none">No fire room yet: no puck has been placed.</p>')
               + "</div></section>")

    # 3. timeline
    ev = rep.get("timeline") or []
    out.append('<section class="blk"><div class="blk-no">3</div><div><div class="blk-h"><h2>Timeline</h2>'
               '<span class="aside">beacon events, minutes:seconds from the first one</span></div>'
               + ('<ul class="tl">' + "".join(f'<li class="k-{_e(e["name"])}"><span class="t">{_e(e["at"])}</span><span class="kind">{_e(str(e["name"] or "").replace("_", " "))}</span>'
                                              f'<span>{_e(e["text"])}</span></li>' for e in ev) + "</ul>"
                  if ev else '<p class="none">No beacon events were fed to this report.</p>')
               + "</div></section>")

    # 4. phones
    rows = []
    for p in rep.get("phones") or []:
        L = p.get("last") or {}
        st = "out" if L.get("safe_outside") else (L.get("status") or "unknown")
        who = (f'{_e(L.get("people"))} {"person" if L.get("people") == 1 else "people"}' if L.get("people") is not None else "people not reported")
        mv = "cannot move" if L.get("can_move") is False else "can move" if L.get("can_move") else ""
        steps = list(p.get("sequence") or [])

        def li(s):
            return (f'<li><span class="tm">{_e(s["at"])}</span><span class="g {_e(s["geometry"])}">{_e(s["geometry"])}</span>'
                    + (f' <span class="by" style="display:inline">{_e(s["where"])}</span>' if s.get("where") else "")
                    + (f' <span class="by" style="display:inline">({_e(s["note"])})</span>' if s.get("note") else "") + "</li>")
        if len(steps) > 7:                                     # a geometry that flips back and forth: first, a count, last
            mid = steps[2:-3]
            kinds = " / ".join(sorted({s["geometry"] for s in mid if s.get("geometry")}))
            seq = "".join(li(s) for s in steps[:2]) + (f'<li><span class="by" style="display:inline">{len(mid)} more changes ({_e(kinds)}) '
                                                       f'from {_e(mid[0]["at"])} to {_e(mid[-1]["at"])}</span></li>') + "".join(li(s) for s in steps[-3:])
        else:
            seq = "".join(li(s) for s in steps)
        assum = "".join(f'<span class="by">Rests on: {_e(a)}</span>' for a in L.get("assumptions") or [])
        rows.append(f'<tr><td><span class="id">{_e(p["id"])}</span><span class="by">first heard {_e(p.get("first_heard"))}'
                    f'{"" if p.get("heard_now") else ", no longer heard"}</span></td>'
                    f'<td><span class="st {_e(st)}">{_e("safe outside" if st == "out" else st)}</span><span class="by">{who}{", " + mv if mv else ""}'
                    + (f', battery {_e(L.get("battery_pct"))}%' if L.get("battery_pct") is not None else "")
                    + (f', registered Suite {_e(L.get("home_suite"))}' if L.get("home_suite") else "") + "</span></td>"
                    f'<td><ul class="seq">{seq}</ul><span class="by">{_e(L.get("text"))}</span>{assum}</td></tr>')
    out.append('<section class="blk"><div class="blk-no">4</div><div><div class="blk-h"><h2>Opt-in phones</h2>'
               '<span class="aside">one row per phone: when first heard, the geometries it went through, its last status</span></div>'
               + ('<div class="scroll"><table class="tbl"><thead><tr><th>Phone</th><th>Last report</th><th>Geometry over time, last line as published</th></tr></thead><tbody>'
                  + "".join(rows) + "</tbody></table></div>" if rows else '<p class="none">No opt-in phone was heard.</p>')
               + "</div></section>")

    # 5. accountability
    if acc and acc.get("suites"):
        trs = []
        for s in acc["suites"]:
            e = s.get("expected") or {}
            u = s.get("unaccounted") or {}
            pr = s.get("priority")
            trs.append(f'<tr class="{"vac" if s.get("vacant") else ""}"><td><span class="suite">{_e(s.get("suite"))}</span></td>'
                       f'<td>{_e(s.get("industry") or ("vacant on record" if s.get("vacant") else ""))}'
                       + (f'<span class="lh">{_e(s.get("life_hazard"))}</span>' if s.get("life_hazard") else "") + "</td>"
                       f'<td class="num">{_e(e.get("people"))}<span class="basis">{_e(e.get("low"))} to {_e(e.get("high"))}; {_e(e.get("basis"))}</span></td>'
                       f'<td class="num">{_e(s.get("phones_opt_in"))}</td><td class="num">{_e(s.get("people_reported"))}</td><td class="num">{_e(s.get("located"))}</td>'
                       f'<td class="num">{_e(s.get("safe_outside"))}</td><td class="num">{_e(s.get("cold_devices"))}</td>'
                       f'<td class="num">{_e(u.get("low"))} to {_e(u.get("high"))}</td><td>{_e(str(s.get("state") or "").replace("-", " "))}</td>'
                       f'<td><span class="pri p{_e(pr)}">{_e(pr if pr is not None else "")}</span><span class="by">{_e(s.get("why"))}</span></td></tr>')
        out.append('<section class="blk"><div class="blk-no">5</div><div><div class="blk-h"><h2>Accountability: the building record against the phones</h2>'
                   f'<span class="aside">{_e((acc.get("source") or {}).get("text"))}</span></div><div class="scroll"><table class="tbl acc"><thead><tr>'
                   '<th>Suite</th><th>Tenant (kind of business)</th><th class="num">Expected (estimate)</th><th class="num">Phones</th><th class="num">Reported</th>'
                   '<th class="num">Located</th><th class="num">Out</th><th class="num">Devices</th><th class="num">Unaccounted</th><th>State</th><th>Priority</th></tr></thead><tbody>'
                   + "".join(trs) + "</tbody></table></div>"
                   '<p class="by">Expected people are estimates: the record\'s head count when it has one, otherwise floor area divided by the building-code '
                   'load factor for the use, times a stated share present. Devices are devices heard without the app, not people.</p></div></section>')

    # 6. command agent
    if plan:
        orders = "".join(f'<li><span class="to">{_e(o.get("to"))}</span><span class="pr {_e(o.get("priority"))}">{_e(o.get("priority"))}</span>{_e(o.get("text"))}</li>'
                         for o in plan.get("orders") or [])
        pris = "".join(f'<li><b>Suite {_e(q.get("suite"))}</b>, {_e(q.get("action"))}' + (f' ({_e(q.get("target"))})' if q.get("target") else "")
                       + f': {_e(q.get("why"))}</li>' for q in plan.get("priorities") or [])
        np = plan.get("next_puck")
        np_text = f'Suite {_e(np.get("suite"))}: {_e(np.get("why"))}' if np else "none"
        lim = "".join(f"<li>{_e(x)}</li>" for x in plan.get("limits_acknowledged") or [])
        out.append('<section class="blk"><div class="blk-no">6</div><div><div class="blk-h"><h2>Command agent: plan and orders</h2>'
                   f'<span class="aside">{_backend_badge(plan.get("backend"))} run {_e(plan.get("run"))}</span></div><div class="agent"><div>'
                   f'<h4>Radio orders</h4><ol class="orders">{orders or "<li>none</li>"}</ol></div><div>'
                   f'<h4>Search priorities</h4><ol>{pris or "<li>none</li>"}</ol>'
                   f'<h4>Next puck</h4><p>{np_text}</p>'
                   f'<h4>Limits the agent acknowledged</h4><ul>{lim or "<li>none stated</li>"}</ul></div></div></div></section>')

    # 7. record checks
    det = (rep.get("record_checks") or {}).get("detected") or []
    filed = (rep.get("record_checks") or {}).get("filed") or []
    out.append('<section class="blk"><div class="blk-no">7</div><div><div class="blk-h"><h2>Record checks</h2>'
               '<span class="aside">where the building record and the phones disagree</span></div>'
               + ("<ul>" + "".join(f'<li><b>Suite {_e(d.get("suite"))}</b>: {_e("; ".join(x for x in d.get("evidence") or [] if x))}</li>' for d in det) + "</ul>"
                  if det else '<p class="none">No disagreement detected.</p>')
               + ("<h4 style=\"font:600 13.5px var(--cond);letter-spacing:.06em;color:var(--muted);text-transform:uppercase\">Filed by the command agent</h4><ul>"
                  + "".join(f'<li><b>Suite {_e(c.get("suite"))}</b>: {_e(c.get("text"))} <span class="by" style="display:inline">({_e(c.get("status"))})</span></li>' for c in filed)
                  + "</ul>" if filed else "")
               + "</div></section>")

    # 8. limits and privacy
    out.append('<section class="blk"><div class="blk-no">8</div><div><div class="blk-h"><h2>Limits and privacy</h2></div>'
               '<ul class="limits">' + "".join(f"<li>{_e(x)}</li>" for x in rep.get("limits") or []) + "</ul>"
               f'<p class="priv">{_e(rep.get("privacy"))}</p></div></section>')
    out.append(f'<footer class="foot"><span>Sandtable Beacon · incident report · {_e(stamp)}</span><span>{_e(rep.get("schema"))}</span>'
               "<span>Not a life-safety product.</span></footer></main></body></html>")
    return "".join(out)


def write_html(rep: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_html(rep), encoding="utf-8")
    return p


def dumps(rep: dict) -> str:
    return json.dumps(rep, indent=1, default=str)
