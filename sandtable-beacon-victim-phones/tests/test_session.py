"""BeaconSession with hand-made observations: stub locator (always) and the real locator (when beacon.locate exists)."""
from __future__ import annotations

import json
import math

import pytest

from beacon import config
from beacon.session import BeaconSession, brief_from_beacons, make_ack

from conftest import HAVE_LOCATOR, HAVE_SIM

T0 = 1794679200.0
EID = "a3f19c02"
P3 = [{"id": "P1", "x": 7.9, "y": 6.7}, {"id": "P2", "x": 5.0, "y": 5.0}, {"id": "P3", "x": 8.3, "y": 2.2}]
TRUTH = (2.4, 2.1)


def _d(p, xy=TRUTH):
    return math.hypot(p["x"] - xy[0], p["y"] - xy[1])


def _session(twin_raw, site_raw, locator="stub", **kw):
    events = []
    s = BeaconSession(twin_raw, site_raw, labels=config.labels(), emit=events.append, locator=locator, **kw)
    return s, events


def _range_all(s, pucks, t, eid=EID, xy=TRUTH):
    for p in pucks:
        s.ingest({"kind": "uwb", "t": t, "puck": p["id"], "eid": eid, "d_m": round(_d(p, xy), 3), "los": 0.9})


def _run(s, pucks, t0=T0, n=6, eid=EID, xy=TRUTH):
    b = None
    for i in range(n):
        t = t0 + i * 0.25
        _range_all(s, pucks, t, eid, xy)
        b = s.step(t)
    return b


def _assert_honest(b):
    for p in b["phones"]:
        if p["geometry"] != "fix":
            for k in ("x", "y", "sigma_m", "cov", "geo", "node"):
                assert p[k] is None, (p["id"], p["geometry"], k)
        else:
            assert p["x"] is not None and p["geo"] is not None


PHONE_KEYS = ("id", "eid", "opt_in", "simulated", "method", "geometry", "x", "y", "floor", "floor_source", "sigma_m", "cov",
              "confidence", "room", "room_label", "room_prob", "node", "geo", "ring", "candidates", "pruned", "zone",
              "ranges", "bearing", "report", "track", "text", "stale")
REPORT_KEYS = ("status", "text", "people", "can_move", "battery_pct", "floor_reported", "baro_hpa", "still", "still_s",
               "platform", "home_suite", "safe_outside", "age_s")


def test_beacons_object_shape(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310", floor="f3")
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None, "report": {"status": "trapped", "people": 2, "can_move": False}})
    b = _run(s, P3)
    for k in ("type", "schema", "seq", "t", "simulated", "source", "floor", "room", "room_label", "rungs", "pucks", "phones",
              "presence", "limits", "privacy", "rig", "unplaced_pucks"):
        assert k in b, k
    assert b["type"] == "beacons" and b["schema"] == "sandtable.beacons/1"
    assert b["room"] == "r310" and b["room_label"] == "Suite 310"
    p = b["phones"][0]
    for k in PHONE_KEYS:
        assert k in p, k
    for k in REPORT_KEYS:
        assert k in p["report"], k
    for k in ("first_seen_s", "last_seen_s", "updates", "moving", "speed"):
        assert k in p["track"]
    assert p["opt_in"] is True and p["simulated"] is True
    assert b["privacy"]["opt_in_only"] is True and b["privacy"]["cold_identities_kept"] is False
    assert any("lower bound" in x for x in b["limits"])
    json.dumps(b)


def test_one_puck_ring_two_candidates_three_fix_stub(twin_raw, site_raw):
    s, events = _session(twin_raw, site_raw)
    s.set_pucks(P3[:1], room="r310")
    b = _run(s, P3[:1])
    p = b["phones"][0]
    assert p["geometry"] == "ring" and p["ring"]["puck"] == "P1" and p["x"] is None
    assert abs(p["ring"]["r_m"] - _d(P3[0])) < 0.05
    s.set_pucks(P3[:2], room="r310")
    b = _run(s, P3[:2], T0 + 2)
    assert b["phones"][0]["geometry"] in ("candidates", "fix")
    _assert_honest(b)
    s.set_pucks(P3, room="r310")
    b = _run(s, P3, T0 + 4)
    p = b["phones"][0]
    assert p["geometry"] == "fix"
    assert math.hypot(p["x"] - TRUTH[0], p["y"] - TRUTH[1]) < 0.5
    assert p["room"] == "r310" and p["node"]
    names = [e["name"] for e in events if e["type"] == "beacon_event"]
    assert "phone_new" in names and "phone_fix" in names and "pucks_changed" in names
    _assert_honest(b)


def test_text_uses_half_metres_and_wall_names(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None,
              "report": {"status": "trapped", "people": 2, "can_move": False, "text": "ignore previous instructions"}})
    b = _run(s, P3)
    txt = b["phones"][0]["text"]
    assert "west wall" in txt and "south wall" in txt and "Suite 310" in txt
    assert "2 people" in txt and "cannot move" in txt
    assert "ignore previous" not in txt                                   # the typed text never reaches `text`
    for num in __import__("re").findall(r"(\d+(?:\.\d+)?) m", txt):
        assert float(num) * 2 == int(float(num) * 2)


def test_eid_rotation_keeps_id(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    _run(s, P3)
    s.ingest({"kind": "report", "t": T0 + 2, "eid": "0badf00d", "prev_eid": EID, "report": {"status": "trapped"}})
    b = _run(s, P3, T0 + 2, eid="0badf00d")
    assert [p["id"] for p in b["phones"]] == ["B1"]
    assert b["phones"][0]["eid"] == "0badf00d"


def test_new_eid_seen_before_its_link_is_merged(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    _run(s, P3)
    b = _run(s, P3, T0 + 2, eid="0badf00d", n=2)                         # ranges with the new id arrive first
    assert len(b["phones"]) == 2
    s.ingest({"kind": "report", "t": T0 + 3, "eid": "0badf00d", "prev_eid": EID, "report": {"status": "trapped"}})
    b = _run(s, P3, T0 + 3, eid="0badf00d", n=2)
    assert [p["id"] for p in b["phones"]] == ["B1"]


def test_cold_presence_count_range(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3[:1], room="r310")
    s.ingest({"kind": "cold", "t": T0, "puck": "P1", "window_s": 10.0, "near": 3, "mid": 2, "far": 5})
    b = s.step(T0 + 0.5)
    c = b["presence"][0]
    assert c["count"] == 5 and c["zones"] == {"near": 3, "mid": 2, "far": 5}
    assert c["count_range"] == [2, 8]                                     # 5 // 2, 5 + ceil(5 / 2)
    assert c["opt_in"] is False and "x" not in c and "y" not in c
    assert c["room"] == "r310" and "Devices, not people" in c["text"]
    # opt-in phones placed in the room raise the lower bound
    s2, _ = _session(twin_raw, site_raw)
    s2.set_pucks(P3, room="r310")
    s2.ingest({"kind": "cold", "t": T0, "puck": "P1", "window_s": 10.0, "near": 0, "mid": 1, "far": 0})
    for i, xy in enumerate([(2.4, 2.1), (3.0, 6.0), (6.0, 3.0)]):
        _run(s2, P3, eid=f"0000000{i}", xy=xy, n=2)
    b2 = s2.step(T0 + 0.5)
    c2 = b2["presence"][0]
    # the published count is never below the opt-in phones known in the room; the raw bands stay in `zones`
    assert c2["count"] == 3 and c2["zones"]["mid"] == 1 and c2["count_range"][0] == 3 and c2["count_range"][1] >= 3
    # old scans age out
    assert s.step(T0 + config.COLD_WINDOW_S + 5)["presence"] == []


def test_safe_outside_is_none(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None, "report": {"status": "ok", "safe_outside": True, "home_suite": "310"}})
    b = _run(s, P3)
    p = b["phones"][0]
    assert p["geometry"] == "none" and p["x"] is None and p["room"] is None
    assert p["report"]["safe_outside"] is True
    assert "safe outside" in p["text"]
    assert make_ack(p)["text"].startswith("You are marked safe outside")


def test_stale_then_dropped(twin_raw, site_raw):
    """A stale phone is never a dot: its position is withdrawn, only the room where it was last located is kept."""
    s, events = _session(twin_raw, site_raw, stale_s=20.0, retention_s=60.0)
    s.set_pucks(P3, room="r310")
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None, "report": {"status": "trapped", "people": 2, "home_suite": "310"}})
    b = _run(s, P3)
    assert b["phones"][0]["geometry"] == "fix"
    b = s.step(T0 + 30)
    p = b["phones"][0]
    assert p["stale"] is True and p["geometry"] == "none"
    for k in ("x", "y", "sigma_m", "cov", "geo", "node", "ring", "zone"):
        assert p[k] is None, k
    assert p["candidates"] == []
    assert p["last_known"]["geometry"] == "fix" and p["last_known"]["room"] == "r310" and p["last_known"]["age_s"] >= 20
    assert set(p["last_known"]) <= {"geometry", "room", "room_label", "age_s", "puck"}          # never coordinates
    assert "Not heard for 2" in p["text"] and "last located in Suite 310" in p["text"]
    assert any(e.get("name") == "phone_lost" for e in events)
    if "accountability" in b:
        row = next(r for r in b["accountability"]["suites"] if r["room"] == "r310" or "r310" in (r.get("rooms") or []))
        assert row["people_reported"] >= 2 and row["located"] == 0
        assert "B1" in row.get("stale_phones", []) and "not heard for" in row["why"]
    from beacon import feeds
    fc = feeds.beacons_to_geojson(b)
    assert not [f for f in fc["features"] if f["properties"]["kind"] in ("phone", "phone-candidate", "phone-ring")]
    lk = [f for f in fc["features"] if f["properties"]["kind"] == "phone-last-known"]
    assert lk and lk[0]["geometry"]["type"] == "Polygon"
    b = s.step(T0 + 70)
    assert b["phones"] == []
    assert sum(1 for e in events if e.get("name") == "phone_lost") == 2


def test_bye_forgets_at_once(twin_raw, site_raw):
    s, events = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    _run(s, P3)
    s.ingest({"kind": "bye", "t": T0 + 2, "eid": EID})
    b = s.step(T0 + 2.1)
    assert b["phones"] == []
    lost = [e for e in events if e.get("name") == "phone_lost"]
    assert lost and "stopped sharing" in lost[-1]["text"]
    assert s.phone_by_eid(EID) is None


@pytest.mark.parametrize("obs", [
    {"kind": "uwb", "puck": "P1", "eid": EID, "d_m": -1},
    {"kind": "uwb", "puck": "P1", "eid": EID, "d_m": float("nan")},
    {"kind": "uwb", "puck": "P1", "eid": EID, "d_m": 151},
    {"kind": "uwb", "puck": "P1", "eid": "not-hex!", "d_m": 2},
    {"kind": "uwb", "eid": EID, "d_m": 2},
    {"kind": "sos", "puck": "P1", "eid": EID, "rssi": 40},
    {"kind": "sos", "puck": "P1", "eid": EID, "rssi": "loud"},
    {"kind": "cold", "puck": "P1", "near": -1},
    {"kind": "teleport", "eid": EID},
    {"kind": "report", "eid": EID, "report": "trapped"},
    "not an object",
])
def test_validation_rejects_garbage(twin_raw, site_raw, obs):
    s, _ = _session(twin_raw, site_raw)
    with pytest.raises(ValueError):
        s.ingest(obs)


def test_brief_is_discrete_and_stable(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None, "report": {"status": "trapped", "people": 2, "battery_pct": 41}})
    s.ingest({"kind": "cold", "t": T0, "puck": "P1", "window_s": 10.0, "near": 2, "mid": 1, "far": 4})
    _run(s, P3)
    b1 = s.brief()
    _range_all(s, P3, T0 + 1.6)
    s.step(T0 + 1.6)
    b2 = s.brief()
    assert b1 == b2
    txt = json.dumps(b1)
    assert EID not in txt and '"t":' not in txt and "age_s" not in txt and "seq" not in txt
    ph = b1["phones"][0]
    assert ph["x"] * 2 == int(ph["x"] * 2) and ph["y"] * 2 == int(ph["y"] * 2)
    assert ph["report"]["battery_pct"] == 40
    assert b1["limits"]
    assert brief_from_beacons(s.beacons()) == b1


def test_ack_texts():
    assert make_ack(None)["heard_by"] == []
    p = {"geometry": "fix", "sigma_m": 0.31, "ranges": [{"puck": "P1", "age_s": 0.2}, {"puck": "P2", "age_s": 0.4}], "report": {}}
    a = make_ack(p)
    assert a["heard_by"] == ["P1", "P2"] and a["sigma_m"] == 0.31 and "0.5 m" in a["text"] and a["text"].startswith("2 rescue beacons")
    r = {"geometry": "ring", "ring": {"r_m": 5.9}, "ranges": [{"puck": "P1", "age_s": 0.2}], "report": {}}
    assert make_ack(r)["sigma_m"] is None and "6 m" in make_ack(r)["text"]


def test_reset_and_events(twin_raw, site_raw):
    s, events = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    _run(s, P3)
    s.reset()
    b = s.beacons()
    assert b["phones"] == [] and b["pucks"] == [] and b["presence"] == [] and b["room"] is None
    assert any(e.get("type") == "reset" for e in events)
    assert any(e.get("name") == "reset" for e in events)


def test_accountability_present_when_roster_imports(twin_raw, site_raw):
    pytest.importorskip("beacon.roster")
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(P3, room="r310")
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None, "report": {"status": "trapped", "people": 2, "home_suite": "310"}})
    b = _run(s, P3)
    acc = b["accountability"]
    for k in ("source", "floor", "suites", "totals", "priorities", "text"):
        assert k in acc
    assert acc["source"]["real_sponsor_data"] is False
    s310 = next(x for x in acc["suites"] if x["room"] == "r310")
    assert s310["phones_opt_in"] >= 1


def test_no_roster_means_no_accountability(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, use_roster=False)
    s.set_pucks(P3, room="r310")
    b = _run(s, P3)
    assert "accountability" not in b


# ---------------------------------------------------------------------------- live mode (iPhone app through POST /api/ingest)
def _batch(s, t, rs, eid=EID, extra=()):
    obs = [{"kind": "uwb", "t": t, "accessory": a, "d_m": d} for a, d in rs] + list(extra)
    return s.ingest_batch({"device": {"eid": eid, "platform": "ios", "model": "iPhone 14", "chip": "U1"}, "obs": obs}, now=t)


def test_live_accessories_unplaced_ring_then_candidates(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live", locator="auto")
    assert s.simulated is False
    s.set_pucks([{"id": "P1", "x": 2.0, "y": 4.3, "accessory": "DWM3001CDK#1A2B"}], room="r310")
    d = math.hypot(4.75 - 2.0, 1.5 - 4.3)
    for i in range(8):
        r = _batch(s, T0 + i * 0.25, [("DWM3001CDK#1A2B", d), ("DWM3001CDK#9F8E", d)])
        b = s.step(T0 + i * 0.25)
    assert r["accepted"] == 2 and r["unknown_accessories"] == ["DWM3001CDK#9F8E"]
    assert b["unplaced_pucks"][0]["accessory"] == "DWM3001CDK#9F8E" and b["unplaced_pucks"][0]["suggested_id"] == "P2"
    assert [p["id"] for p in b["pucks"]] == ["P1"]
    p = b["phones"][0]
    assert p["geometry"] == "ring" and p["simulated"] is False
    assert p["report"]["model"] == "iPhone 14" and p["report"]["platform"] == "ios"
    s.set_pucks([{"id": "P2", "x": 7.5, "y": 4.3, "accessory": "DWM3001CDK#9F8E"}], merge=True)
    for i in range(8, 20):
        _batch(s, T0 + i * 0.25, [("DWM3001CDK#1A2B", d), ("DWM3001CDK#9F8E", d)])
        b = s.step(T0 + i * 0.25)
    assert [p["id"] for p in b["pucks"]] == ["P1", "P2"] and b["unplaced_pucks"] == []
    assert b["phones"][0]["geometry"] in ("candidates", "fix")
    if HAVE_LOCATOR:
        assert b["phones"][0]["geometry"] == "candidates"
    _assert_honest(b)


def test_live_rig_scale(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live")
    s.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7, "accessory": "A#0001"}], room="r310")
    s.set_rig_scale(3.0)
    for i in range(4):
        _batch(s, T0 + i * 0.25, [("A#0001", 1.5)])
        b = s.step(T0 + i * 0.25)
    assert b["rig"] == {"scale": 3.0}
    assert abs(b["phones"][0]["ranges"][0]["d_m"] - 4.5) < 0.05
    assert any("1 : 3" in x for x in b["limits"])
    with pytest.raises(ValueError):
        s.set_rig_scale(0)


def test_live_batch_drops_only_the_bad_observation(twin_raw, site_raw):
    """One bad value must not silence a phone: it is dropped and reported; a batch with nothing usable raises."""
    s, _ = _session(twin_raw, site_raw, source="live")
    out = _batch(s, T0, [("A#0001", 2.0), ("A#0001", -3.0)])
    assert out["rejected"] == 1 and out["accepted"] >= 1 and "d_m" in out["errors"][0]
    s2, _ = _session(twin_raw, site_raw, source="live")
    with pytest.raises(ValueError):
        s2.ingest_batch([{"kind": "uwb", "t": T0, "eid": "a3f19c02", "accessory": "A#0001", "d_m": -3.0}], now=T0)
    assert not s2._pending


def test_live_persisted_puck_table(twin_raw, site_raw, tmp_path):
    path = tmp_path / "pucks_live.json"
    s, _ = _session(twin_raw, site_raw, source="live", persist_path=path)
    s.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7, "accessory": "A#0001"}], room="r310")
    s.set_rig_scale(2.0)
    s2, _ = _session(twin_raw, site_raw, source="live", persist_path=path)
    assert [p["id"] for p in s2.pucks] == ["P1"] and s2.pucks[0]["accessory"] == "A#0001" and s2.rig_scale == 2.0


def test_live_host_puck_side_range_and_cold(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live")
    s.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7, "accessory": "A#0001"}], room="r310")
    for i in range(4):
        _batch(s, T0 + i * 0.25, [("A#0001", 5.0)])
        s.step(T0 + i * 0.25)
    host_batch = [{"kind": "host", "t": T0 + 6, "puck": "P1", "host": "uno-q", "uptime_s": 312, "rtt": True, "version": "0.1"},
                  {"kind": "uwb_puck", "t": T0 + 6, "puck": "P1", "d_m": 5.2},
                  {"kind": "cold", "t": T0 + 6, "puck": "P1", "window_s": 10.0, "near": 2, "mid": 1, "far": 4}]
    r = s.ingest_batch(host_batch, now=T0 + 6)
    assert r["accepted"] == 3
    b = s.step(T0 + 6.2)
    assert b["pucks"][0]["host"] == "uno-q" and "host_seen_s" in b["pucks"][0]
    assert b["presence"][0]["count"] == 3
    rg = b["phones"][0]["ranges"]
    assert rg and rg[0]["reporter"] == "puck" and abs(rg[0]["d_m"] - 5.2) < 0.3
    b = s.step(T0 + 30)
    assert b["pucks"][0]["host"] is None                                  # silent host dropped after 20 s
    # a puck-side range with no phone that ranged that puck recently is ignored
    s2, _ = _session(twin_raw, site_raw, source="live")
    s2.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7}], room="r310")
    s2.ingest_batch([{"kind": "uwb_puck", "t": T0, "puck": "P1", "d_m": 3.0}], now=T0)
    assert s2.step(T0 + 0.1)["phones"] == []


# ---------------------------------------------------------------------------- with the real locator
@pytest.mark.skipif(not HAVE_LOCATOR, reason="beacon.locate / beacon.track not available")
def test_real_locator_ring_and_fix(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, locator="auto")
    assert s._locate is not None
    s.set_pucks(P3[:1], room="r310")
    b = _run(s, P3[:1], n=8)
    p = b["phones"][0]
    assert p["geometry"] == "ring" and p["x"] is None and p["ring"]["arcs"]
    s.set_pucks(P3, room="r310")
    b = _run(s, P3, T0 + 3, n=8)
    p = b["phones"][0]
    assert p["geometry"] == "fix"
    assert math.hypot(p["x"] - TRUTH[0], p["y"] - TRUTH[1]) < 0.6
    heat = s.heat("B1", T0 + 4.9)
    assert heat and heat["nx"] > 0 and heat["ny"] > 0


@pytest.mark.skipif(not (HAVE_LOCATOR and HAVE_SIM), reason="beacon.sim / beacon.locate not available")
def test_simulated_walkthrough_end_to_end(twin_raw, site_raw):
    """Simulated time, no sleeping: pucks land one by one on the suite-310 scene; the published geometry of the trapped
    phone goes ring -> (candidates | fix) -> fix, and no phone ever has x/y unless its geometry is a fix."""
    from beacon.sim import World
    s, _ = _session(twin_raw, site_raw, locator="auto")
    w = World(twin_raw, seed=310)
    w.load_scenario("suite-310")
    true_pucks = [{"id": p["id"], "x": p["x"], "y": p["y"]} for p in w.truth()["pucks"]]
    trapped = next(p for p in w.truth()["phones"] if p.get("opt_in") and p.get("uwb") and p.get("room") == "r310")
    seen = []
    t = T0
    for k in (1, 2, 3):
        w.set_pucks(true_pucks[:k])
        s.set_pucks(w.pucks, room="r310")
        for i in range(60):                                               # 6 simulated seconds at 10 Hz
            t += 0.1
            for o in w.step(t):
                s.ingest(o)
            if i % 2 == 1:
                b = s.step(t)
                _assert_honest(b)
                ph = s.phone_by_eid(next(p["eid"] for p in w.truth()["phones"] if p["key"] == trapped["key"]))
                if ph and (not seen or seen[-1] != ph["geometry"]):
                    seen.append(ph["geometry"])
    assert "ring" in seen
    assert seen[-1] == "fix"
    assert seen.index("ring") < len(seen) - 1


# ---------------------------------------------------------------------------- stated evidence (BEACON_FEED A8, beacon.prune)
PAIR = [{"id": "P1", "x": 9.15, "y": 6.7}, {"id": "P2", "x": 9.15, "y": 2.7}]       # both along the door wall of r310


def _two_cands_spatial(room_a="r310", room_b="rcW"):
    return {"method": "uwb-twr", "geometry": "candidates", "x": None, "y": None, "sigma_m": None, "cov": None, "confidence": 0.9,
            "room": None, "room_label": None, "room_prob": None, "node": None, "ring": None, "zone": None, "pruned": [],
            "candidates": [{"x": 2.4, "y": 4.7, "sigma_m": 0.3, "weight": 0.5, "room": room_a, "room_label": "Suite 310"},
                           {"x": 15.9, "y": 4.7, "sigma_m": 0.3, "weight": 0.5, "room": room_b, "room_label": "elsewhere"}]}


class _FixedTrack:
    """A tracker stub that returns a fixed locator result (to test what the session does with it)."""
    result: dict = {}

    def __init__(self, eid, georef_bearing_deg=None):
        self.eid, self.report, self.last_seen, self.moving = eid, {}, None, False

    def add(self, obs):
        pass

    def inputs(self, now):
        return [], None

    def estimate(self, ctx, pucks, now, noise=None):
        return json.loads(json.dumps(self.result))


def _stated(twin_raw, site_raw, report, result, search_room=None):
    s, _ = _session(twin_raw, site_raw)
    s._Track = _FixedTrack
    _FixedTrack.result = result
    s.set_pucks(PAIR, room="r310")
    if search_room:
        s.set_search_room(search_room)
    s.ingest({"kind": "report", "t": T0, "eid": EID, "prev_eid": None, "report": report})
    return s.step(T0 + 0.25)["phones"][0]


def test_occupant_statement_dismisses_mirror_visibly(twin_raw, site_raw):
    pytest.importorskip("beacon.prune")
    p = _stated(twin_raw, site_raw, {"status": "trapped", "home_suite": "310"}, _two_cands_spatial())
    assert p["geometry"] == "fix" and p["x"] == 2.4 and p["room"] == "r310"
    assert p["assumptions"] and p["assumptions"][0]["kind"] == "occupant-statement"
    assert any(q.get("by") == "occupant-statement" for q in p["pruned"])
    assert p["text"].endswith("(mirror spot dismissed: the occupant says they are in Suite 310)")


def test_search_room_assumption(twin_raw, site_raw):
    pytest.importorskip("beacon.prune")
    p = _stated(twin_raw, site_raw, {"status": "trapped"}, _two_cands_spatial(), search_room="r310")
    assert p["geometry"] == "fix" and p["assumptions"][0]["kind"] == "search-room"
    assert "command is searching Suite 310" in p["text"]


def test_candidates_stay_when_nothing_decides(twin_raw, site_raw):
    p = _stated(twin_raw, site_raw, {"status": "trapped", "home_suite": "310"}, _two_cands_spatial("r310", "r310"))
    assert p["geometry"] == "candidates" and p["x"] is None and p["assumptions"] == []
    assert "cannot choose between them" in p["text"] and "a third puck, or moving one puck to the wall, would decide" in p["text"]


def test_safe_outside_statement_not_used(twin_raw, site_raw):
    p = _stated(twin_raw, site_raw, {"status": "ok", "home_suite": "310", "safe_outside": True}, _two_cands_spatial())
    assert p["geometry"] == "none" and p["assumptions"] == []


def test_live_links_kept_25_s_and_stale_after_40(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live", locator="auto")
    s.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7, "accessory": "A#0001"}], room="r310")
    _batch(s, T0, [("A#0001", 5.0)])
    s.step(T0 + 0.1)
    b = s.step(T0 + 18.0)                                                 # one turn of the stock firmware later
    p = b["phones"][0]
    assert p["stale"] is False and p["geometry"] == "ring"
    assert s.ack(p)["heard_by"] == ["P1"]
    assert s.step(T0 + 35.0)["phones"][0]["stale"] is False
    assert s.step(T0 + 41.0)["phones"][0]["stale"] is True
