"""PhoneTrack: smoothing, staleness, bearing freshness, hysteresis, report merge, motion."""
import json
import math
from pathlib import Path

import pytest

from beacon import locate, models
from beacon.track import BEARING_FRESH_S, LINK_STALE_S, PhoneTrack
from beacon.twin import Twin

ROOT = Path(__file__).resolve().parents[1]
FIRE_ROOM = "r310"
N = models.DEFAULT_NOISE


@pytest.fixture(scope="module")
def twin():
    return Twin(json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text()))


@pytest.fixture(scope="module")
def ctx(twin):
    return locate.Context(twin)


@pytest.fixture(scope="module")
def scene(twin):
    poly = twin.room(FIRE_ROOM)["polygon"]
    x0, y0 = min(p[0] for p in poly), min(p[1] for p in poly)
    dx, dy = next(d for d in twin.data["doors"] if FIRE_ROOM in d["rooms"])["at"]
    pucks = [{"id": f"P{i + 1}", "x": dx + ox, "y": dy + oy} for i, (ox, oy) in enumerate([(-1.2, -0.3), (-3.1, 0.7), (-2.3, -2.6)])]
    return {"pucks": pucks, "phone": (x0 + 2.4, y0 + 2.1)}


def true_d(twin, p, phone):
    d = math.dist((p["x"], p["y"]), phone)
    return models.uwb_expected(d, twin.walls_crossed((p["x"], p["y"]), phone), N)[0]


def feed_uwb(tr, twin, pucks, phone, t0, t1, hz=5.0, eid="a3f19c02", bump=None):
    t = t0
    k = 0
    while t <= t1 + 1e-9:
        for p in pucks:
            d = true_d(twin, p, phone) + 0.03 * math.sin(7 * k + len(p["id"]))
            if bump and bump(p, k):
                d += 2.5
            tr.add({"kind": "uwb", "t": t, "puck": p["id"], "eid": eid, "d_m": d, "los": 0.9})
        t += 1.0 / hz
        k += 1


def test_median_kills_outliers(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("a3f19c02")
    feed_uwb(tr, twin, P, phone, 0.0, 1.2, bump=lambda p, k: p["id"] == "P1" and k in (2, 4))
    ranges, bearing = tr.inputs(1.2)
    r1 = next(r for r in ranges if r["puck"] == "P1")
    assert r1["kind"] == "uwb" and r1["n"] >= 5
    assert abs(r1["d_m"] - true_d(twin, P[0], phone)) < 0.1
    assert r1["los"] == pytest.approx(0.9)
    assert bearing is None
    e = tr.estimate(ctx, P, 1.2)
    assert e["geometry"] == "fix" and math.dist((e["x"], e["y"]), phone) < 0.3
    assert {r["puck"] for r in e["ranges"]} == {"P1", "P2", "P3"}
    assert all(r["sigma_m"] >= N["uwb"]["link_bias_sigma_m"] for r in e["ranges"])


def test_stale_links_are_dropped(twin, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("e1")
    feed_uwb(tr, twin, P, phone, 0.0, 1.0, eid="e1")
    feed_uwb(tr, twin, P[:1], phone, 1.2, 8.0, eid="e1")
    ranges, _ = tr.inputs(1.0 + LINK_STALE_S + 0.5)
    assert [r["puck"] for r in ranges] == ["P1"]


def test_bluetooth_averaging_and_uwb_preference(twin, scene):
    P = scene["pucks"]
    tr = PhoneTrack("e2")
    for k, v in enumerate([-70.0, -74.0, -72.0, -71.0, -73.0]):
        tr.add({"kind": "sos", "t": 0.5 * k, "puck": "P2", "eid": "e2", "rssi": v, "report": {"status": "trapped", "people": 2}})
    tr.add({"kind": "uwb", "t": 2.0, "puck": "P1", "eid": "e2", "d_m": 4.0})
    tr.add({"kind": "sos", "t": 2.0, "puck": "P1", "eid": "e2", "rssi": -60.0})
    ranges, _ = tr.inputs(2.1)
    by = {r["puck"]: r for r in ranges}
    assert by["P2"]["kind"] == "ble" and by["P2"]["rssi"] == pytest.approx(-72.0) and by["P2"]["n"] == 5
    assert by["P1"]["kind"] == "uwb"                    # one range per puck: UWB wins
    assert tr.report["status"] == "trapped" and tr.report["people"] == 2


def test_bearing_only_while_fresh(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    g = twin.data["georef"]["bearing_deg"]
    tr = PhoneTrack("e3")
    want = math.degrees(math.atan2(phone[1] - P[0]["y"], phone[0] - P[0]["x"])) % 360
    az = models.bldg_to_compass_deg(want, g)
    tr.add({"kind": "bearing", "t": 10.0, "eid": "e3", "puck": "P1", "to_puck_compass_deg": az, "sigma_deg": 10.0})
    feed_uwb(tr, twin, P[:1], phone, 9.0, 10.0, eid="e3")
    _, b = tr.inputs(10.5)
    assert b is None                                    # no georeference known to the tracker yet
    _, b = tr.inputs(10.5, georef_bearing_deg=g)
    assert b is not None and b["puck"] == "P1" and abs((b["from_puck_deg"] - want + 180) % 360 - 180) < 0.2
    e = tr.estimate(ctx, P, 10.5)                       # the context carries the georeference
    assert e["bearing"] is not None and e["method"] == "uwb-twr+bearing"
    _, b = tr.inputs(10.0 + BEARING_FRESH_S + 0.1, georef_bearing_deg=g)
    assert b is None


def test_lost_links_drop_the_geometry_at_once(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("e4")
    feed_uwb(tr, twin, P, phone, 0.0, 1.0, eid="e4")
    e = tr.estimate(ctx, P, 1.0)
    assert e["geometry"] == "fix" and not e["debug"]["held"]
    # P2 and P3 go quiet; only P1 keeps ranging: the evidence changed, so the fix goes at once (no dot held over)
    feed_uwb(tr, twin, P[:1], phone, 1.2, 7.0, eid="e4")
    e = tr.estimate(ctx, P, 7.0)
    assert e["geometry"] == "ring" and not e["debug"]["held"]
    assert e["x"] is None and e["ring"]["puck"] == "P1"
    # and back: the returning links are new evidence, a single fix does not replace the ring (HOLD_N)
    feed_uwb(tr, twin, P, phone, 7.4, 8.0, eid="e4")
    e = tr.estimate(ctx, P, 8.0)
    assert e["geometry"] == "ring" and e["debug"]["held"]
    e = tr.estimate(ctx, P, 8.0)
    assert e["geometry"] == "fix"


def test_report_merge_rotation_and_motion(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("09be11d4")
    tr.add({"kind": "report", "t": 1.0, "eid": "09be11d4", "prev_eid": None,
            "report": {"status": "trapped", "people": 2, "can_move": False, "still": True, "still_s": 30}})
    tr.add({"kind": "report", "t": 3.0, "eid": "a3f19c02", "prev_eid": "09be11d4", "report": {"battery_pct": 41}})
    assert tr.eid == "a3f19c02" and tr.eids == ["09be11d4", "a3f19c02"]
    assert tr.report["people"] == 2 and tr.report["battery_pct"] == 41 and tr.report["status"] == "trapped"
    assert tr.moving is False
    tr.add({"kind": "report", "t": 4.0, "eid": "a3f19c02", "report": {"still": False}})
    assert tr.moving is True
    # without an accelerometer report, range changes decide
    tr2 = PhoneTrack("e5")
    for k in range(20):
        t = k * 0.2
        tr2.add({"kind": "uwb", "t": t, "puck": "P1", "eid": "e5", "d_m": 3.0 + 0.5 * t})
        tr2.inputs(t)
    assert tr2.moving is True
    tr3 = PhoneTrack("e6")
    feed_uwb(tr3, twin, P, phone, 0.0, 3.0, eid="e6")
    for k in range(13):
        tr3.inputs(k * 0.25)
    assert tr3.moving is False
    e = tr3.estimate(ctx, P, 3.0)
    assert set(e["track"]) == {"first_seen_s", "last_seen_s", "updates", "moving", "speed"}
    assert e["track"]["updates"] > 0 and e["track"]["first_seen_s"] == pytest.approx(3.0)


def test_no_links_and_removed_pucks(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("e7")
    e = tr.estimate(ctx, P, 0.0)
    assert e["geometry"] == "none"
    feed_uwb(tr, twin, P, phone, 0.0, 1.0, eid="e7")
    e = tr.estimate(ctx, P[:1], 1.0)                    # P2, P3 were reset: their links are ignored, not an error
    assert {r["puck"] for r in e["ranges"]} == {"P1"}
    first = tr.estimate(ctx, P, 1.0)["geometry"]
    kept = tr.estimate(ctx, P, 30.0)                    # everything stale: the last geometry is kept
    assert kept["geometry"] == first and kept["debug"]["held"]
    with pytest.raises(ValueError):
        tr.add({"kind": "cold", "t": 1.0, "puck": "P1"})


# ---- round 2 -----------------------------------------------------------------------------------------------------

from beacon import track as track_mod  # noqa: E402


def test_forget_puck_drops_ranges_measured_before_the_move(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("f1")
    feed_uwb(tr, twin, P, phone, 0.0, 2.0, eid="f1")
    assert tr.estimate(ctx, P, 2.0)["geometry"] == "fix"
    moved = [dict(p) for p in P]
    moved[1] = dict(moved[1], x=moved[1]["x"] - 2.0)              # P2 is picked up and set down 2 m away
    tr.forget_puck("P2", 2.0)
    # a range to P2 measured before the move but delivered late is ignored
    tr.add({"kind": "uwb", "t": 1.95, "puck": "P2", "eid": "f1", "d_m": true_d(twin, P[1], phone)})
    ranges, _ = tr.inputs(2.1)
    assert "P2" not in {r["puck"] for r in ranges}
    e = tr.estimate(ctx, moved, 2.1)
    assert not e["debug"]["held"]                                 # nothing computed with the old position is held over
    assert e["geometry"] != "fix" or math.dist((e["x"], e["y"]), phone) < 0.5
    # ranges measured after the move join again
    feed_uwb(tr, twin, moved, phone, 2.6, 4.0, eid="f1")
    e = tr.estimate(ctx, moved, 4.0)
    e = tr.estimate(ctx, moved, 4.0)
    assert e["geometry"] == "fix" and math.dist((e["x"], e["y"]), phone) < 0.5


def test_forget_puck_with_a_phone_clock_running_behind(twin, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("f2")
    feed_uwb(tr, twin, P, phone, 100.0, 101.0, eid="f2")         # the phone's clock: 100 s
    tr.forget_puck("P2", 160.0)                                  # the server's clock: 60 s ahead of the phone
    feed_uwb(tr, twin, P[1:2], phone, 101.6, 102.6, eid="f2")    # fresh ranges, phone timestamps
    ranges, _ = tr.inputs(102.6)
    assert "P2" in {r["puck"] for r in ranges}                   # not dropped for the whole clock offset


def test_a_moved_puck_is_detected_without_forget_puck(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("f3")
    feed_uwb(tr, twin, P, phone, 0.0, 2.0, eid="f3")
    assert tr.estimate(ctx, P, 2.0)["geometry"] == "fix"
    moved = [dict(p) for p in P]
    moved[2] = dict(moved[2], x=moved[2]["x"] - 1.5, y=moved[2]["y"] - 1.0)
    e = tr.estimate(ctx, moved, 2.2)                             # old P3 ranges with the new P3 position: never a fix metres off
    assert "P3" not in {r["puck"] for r in e["ranges"]}
    assert e["geometry"] != "fix" or math.dist((e["x"], e["y"]), phone) < 0.5


def test_a_thin_link_never_joins_solid_ones(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("f4")
    feed_uwb(tr, twin, P[:1], phone, 0.0, 1.0, eid="f4")
    tr.add({"kind": "uwb", "t": 1.0, "puck": "P3", "eid": "f4", "d_m": true_d(twin, P[2], phone)})
    e = tr.estimate(ctx, P, 1.0)
    assert e["geometry"] == "ring" and e["ring"]["puck"] == "P1"     # P3's first reading does not make a position
    feed_uwb(tr, twin, P, phone, 1.2, 2.0, eid="f4")                 # three readings make P2 and P3 solid
    for t in (2.0, 2.0, 2.0):
        e = tr.estimate(ctx, P, t)
    assert e["geometry"] == "fix"
    # live: an iPhone reports each board once per turn, so no link is ever solid; then every link is used
    tr2 = PhoneTrack("f5")
    tr2.add({"kind": "report", "t": 0.0, "eid": "f5", "report": {"still": True, "still_s": 40}})
    for p in P[:2]:
        tr2.add({"kind": "uwb", "t": 1.0, "puck": p["id"], "eid": "f5", "d_m": true_d(twin, p, phone)})
    e = tr2.estimate(ctx, P, 1.1)
    assert e["debug"]["n_uwb_pucks"] == 2                           # both single readings reach the locator


def test_a_range_from_before_the_phone_walked_away_is_left_out(twin, ctx, scene):
    """The watchdog's scene: the phone walks out of P2's reach; P2's last range (still within link_stale_s) must not
    be combined with P1's fresh one."""
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("w1")
    tr.link_stale_s = 25.0
    feed_uwb(tr, twin, P[:2], phone, 0.0, 3.0, eid="w1")
    assert tr.estimate(ctx, P, 3.0)["geometry"] in ("candidates", "fix")
    tr.add({"kind": "report", "t": 3.0, "eid": "w1", "report": {"still": False}})
    # walking toward P1's side at 1 m/s; P2 hears nothing more
    t, x, y = 3.2, phone[0], phone[1]
    shown = []
    while t <= 8.0:
        x, y = x + 0.2, y + 0.1
        tr.add({"kind": "uwb", "t": t, "puck": "P1", "eid": "w1", "d_m": true_d(twin, P[0], (x, y))})
        if round(t * 5) % 1 == 0:
            e = tr.estimate(ctx, P, t)
            shown.append(e)
        t = round(t + 0.2, 3)
    late = [e for e in shown if any(q["puck"] == "P2" for q in e["debug"].get("left_out", []))]
    assert late, "P2 was never left out"
    tail = shown[-10:]
    assert all(e["geometry"] == "ring" for e in tail)
    assert all({r["puck"] for r in e["ranges"] if r["kind"] == "uwb"} >= {"P1"} for e in tail)


def test_a_still_phone_taking_turns_keeps_its_fix(twin, ctx, scene):
    """Live: each board serves the phone in turn (4 s of ranges, then the other board); the phone is still, so an
    older board's last reading is licensed and the fix stays."""
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("w2")
    tr.link_stale_s = 25.0
    tr.add({"kind": "report", "t": 0.0, "eid": "w2", "report": {"status": "trapped", "still": True, "still_s": 60}})
    geoms = []
    t = 0.0
    for turn in range(9):
        p = P[turn % 3]
        for k in range(20):                                    # 4 s at 5 Hz from one board
            tr.add({"kind": "uwb", "t": round(t, 2), "puck": p["id"], "eid": "w2", "d_m": true_d(twin, p, phone) + 0.02 * math.sin(k)})
            t += 0.2
        t += 1.5                                               # connection set-up gap
        tr.add({"kind": "report", "t": round(t, 2), "eid": "w2", "report": {"still": True, "still_s": 60 + t}})
        if turn >= 2:
            for _ in range(3):
                geoms.append(tr.estimate(ctx, P, round(t, 2))["geometry"])
    assert geoms[-1] == "fix" and geoms.count("fix") >= len(geoms) - 3


def _scripted(monkeypatch, geoms):
    """Replace the locator with a script of geometries (the tracker's hysteresis is what is tested)."""
    it = iter(geoms)

    def fake(ctx, pucks, ranges, bearing=None, prior=None, noise=None, want_heat=False):
        g = next(it)
        fix = g == "fix"
        return {"method": "uwb-twr", "geometry": g, "x": 1.0 if fix else None, "y": 1.0 if fix else None,
                "sigma_m": 0.3 if fix else None, "cov": [0.09, 0.0, 0.09] if fix else None, "confidence": 0.8,
                "pruned": [], "candidates": [{"x": 1, "y": 1, "sigma_m": 0.3, "weight": 0.5}] * 2 if g == "candidates" else [],
                "ring": {"puck": "P1"} if g == "ring" else None, "zone": None, "debug": {}}
    monkeypatch.setattr(track_mod.locate, "estimate", fake)


def test_hysteresis_withdraws_a_claim_faster_than_it_makes_one(twin, ctx, scene, monkeypatch):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("h1")
    feed_uwb(tr, twin, P, phone, 0.0, 30.0, eid="h1")
    seq = ["candidates", "fix", "fix", "fix", "candidates", "ring", "fix", "candidates", "fix", "ring", "candidates", "candidates"]
    _scripted(monkeypatch, seq)
    shown = [tr.estimate(ctx, P, 30.0)["geometry"] for _ in seq]
    assert shown[0] == "candidates"
    assert shown[1:3] == ["candidates", "candidates"]              # a stronger claim waits for HOLD_UP_N estimates
    assert shown[3] == "fix"
    assert shown[4] == "fix" and shown[5] == "ring"                # two weaker estimates (any weaker ones) withdraw it
    # alternating evidence never puts the dot back
    assert "fix" not in shown[6:]


def test_hysteresis_after_new_evidence_uses_hold_n(twin, ctx, scene, monkeypatch):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("h2")
    feed_uwb(tr, twin, P[:2], phone, 0.0, 30.0, eid="h2")
    _scripted(monkeypatch, ["candidates", "fix", "fix"])
    assert tr.estimate(ctx, P, 30.0)["geometry"] == "candidates"
    feed_uwb(tr, twin, P[2:], phone, 29.0, 30.0, eid="h2")      # a third puck joins: the evidence changed
    assert tr.estimate(ctx, P, 30.0)["geometry"] == "candidates"
    assert tr.estimate(ctx, P, 30.0)["geometry"] == "fix"       # HOLD_N, not HOLD_UP_N


def test_pruned_reason_text_is_sticky_while_it_applies(twin, ctx, scene, monkeypatch):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("s1")
    feed_uwb(tr, twin, P, phone, 0.0, 10.0, eid="s1")
    outs = [("A", ["A", "B"]), ("B", ["B", "A"]), ("B", ["B"])]
    it = iter(outs)

    def fake(ctx, pucks, ranges, bearing=None, prior=None, noise=None, want_heat=False):
        reason, reasons = next(it)
        return {"method": "uwb-twr", "geometry": "fix", "x": 1.0, "y": 1.0, "sigma_m": 0.3, "cov": [0.09, 0, 0.09],
                "confidence": 0.8, "candidates": [], "ring": None, "zone": None, "debug": {},
                "pruned": [{"x": 5.0, "y": 5.0, "reason": reason, "reasons": reasons}]}
    monkeypatch.setattr(track_mod.locate, "estimate", fake)
    texts = [tr.estimate(ctx, P, 10.0)["pruned"][0]["reason"] for _ in outs]
    assert texts == ["A", "A", "B"]                               # kept while A still applies, changed when it no longer does


def test_two_pucks_through_the_simulator_never_show_an_unexplained_fix(twin, ctx):
    """World + PhoneTrack as the server runs them (10 Hz world, 4 Hz estimates), live-like (distance only): the judged
    careless layout with a phone whose mirror spot is in the room, and one near P2 whose two spots are close."""
    sim = pytest.importorskip("beacon.sim")
    poly = twin.room(FIRE_ROOM)["polygon"]
    x0, y0 = min(p[0] for p in poly), min(p[1] for p in poly)
    w_, h_ = max(p[0] for p in poly) - x0, max(p[1] for p in poly) - y0
    dx, dy = next(d for d in twin.data["doors"] if FIRE_ROOM in d["rooms"])["at"]
    a, b = (dx - 0.35, dy - 0.5), (x0 + 0.42 * w_, y0 + 0.64 * h_)
    phones = {"deep": (x0 + 2.1, y0 + 1.8), "near-p2": (b[0] - 0.6, b[1] + 0.9)}
    for seed in (310, 1):
        w = sim.World(twin, seed=seed)
        w.set_pucks([{"id": "P1", "x": a[0], "y": a[1]}, {"id": "P2", "x": b[0], "y": b[1]}])
        for k, xy in phones.items():
            w.upsert_phone(k, xy[0], xy[1], report={"status": "trapped"})
        pucks = [{"id": p["id"], "x": p["x"], "y": p["y"], "sigma_m": p.get("sigma_m")} for p in w.pucks]
        trs = {k: PhoneTrack(k) for k in phones}
        nxt = 0.25
        for step in range(1, 401):                                # 40 simulated seconds
            t = step * 0.1
            e2k = {ph["eid"]: ph["key"] for ph in w.truth()["phones"] if ph.get("eid")}
            for o in w.step(t):
                k = e2k.get(o.get("eid"))
                if k in trs and o["kind"] in ("uwb", "sos", "report"):
                    o = dict(o, eid=k)
                    o.pop("los", None)
                    trs[k].add(o)
            if t + 1e-9 >= nxt:
                nxt += 0.25
                for k, tr in trs.items():
                    e = tr.estimate(ctx, pucks, t)
                    if e["geometry"] == "fix":
                        mx, my = _mirror_of((e["x"], e["y"]), a, b)
                        assert locate.inside(e, mx, my) or any(
                            math.dist((q["x"], q["y"]), (mx, my)) <= max(1.0, 2.5 * e["sigma_m"]) for q in e["pruned"]), (seed, t, k, e)
                        assert math.dist((e["x"], e["y"]), phones[k]) <= max(3 * e["sigma_m"], 1.0)


def _mirror_of(p, a, b):
    ux, uy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(ux, uy)
    ux, uy = ux / L, uy / L
    t = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
    return (2 * (a[0] + t * ux) - p[0], 2 * (a[1] + t * uy) - p[1])
