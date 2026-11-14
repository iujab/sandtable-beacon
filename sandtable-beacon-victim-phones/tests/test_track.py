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


def test_hysteresis_holds_a_fix_for_one_noisy_update(twin, ctx, scene):
    P, phone = scene["pucks"], scene["phone"]
    tr = PhoneTrack("e4")
    feed_uwb(tr, twin, P, phone, 0.0, 1.0, eid="e4")
    e = tr.estimate(ctx, P, 1.0)
    assert e["geometry"] == "fix" and not e["debug"]["held"]
    # P2 and P3 go quiet; only P1 keeps ranging -> the locator alone would say "ring"
    feed_uwb(tr, twin, P[:1], phone, 1.2, 7.0, eid="e4")
    e = tr.estimate(ctx, P, 7.0)
    assert e["debug"]["latest_geometry"] == "ring"
    assert e["geometry"] == "fix" and e["debug"]["held"]          # one estimate does not demote the fix
    e = tr.estimate(ctx, P, 7.25)
    assert e["geometry"] == "ring" and not e["debug"]["held"]     # two consecutive do
    assert e["x"] is None and e["ring"]["puck"] == "P1"
    # and back: a single fix does not replace the ring either
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
