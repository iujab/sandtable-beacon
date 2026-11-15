"""Puck-to-puck baselines and on-landing calibration (docs/BEACON_FEED.md A12): validation, anchoring, the
placed-vs-measured check, persistence, the HTTP routes, and `placed_by` on every Puck."""
from __future__ import annotations

import json
import logging
import math
import time

import pytest

from beacon import baseline as bl
from beacon import config
from beacon.session import BeaconSession, brief_from_beacons

from conftest import HAVE_LOCATOR, HAVE_SIM

T0 = 1794679200.0
DOOR_WALL = [{"id": "P1", "x": 9.15, "y": 6.7}, {"id": "P2", "x": 9.15, "y": 2.7}]      # 4.0 m apart (hardware/BRINGUP 7.2)


def _session(twin_raw, site_raw, **kw):
    events: list[dict] = []
    s = BeaconSession(twin_raw, site_raw, labels=config.labels(), emit=events.append, locator="stub", **kw)
    return s, events


# ---------------------------------------------------------------------------- pure validation and geometry
def test_make_baseline_normalises_the_pair_and_rounds():
    b = bl.make_baseline(["P2", "P1"], 3.97249, sigma_m=0.04123, samples=48.0, source="twr", t=T0 + 0.12345)
    assert b == {"pair": ["P1", "P2"], "d_m": 3.972, "sigma_m": 0.041, "samples": 48, "t": round(T0 + 0.12345, 3),
                 "source": "twr"}
    m = bl.make_baseline(("P3", "P5"), 2.0, t=T0)
    assert m["sigma_m"] is None and m["samples"] is None and m["source"] == "manual"


@pytest.mark.parametrize("pair,d,kw", [
    (["P1", "P1"], 3.0, {}),                      # the same puck twice
    (["P1", "P6"], 3.0, {}),                      # ids are P1..P5
    (["P1"], 3.0, {}),
    ("P1,P2", 3.0, {}),
    (["P1", "P2"], 0.05, {}),                     # bounds are exclusive
    (["P1", "P2"], 150.0, {}),
    (["P1", "P2"], float("nan"), {}),
    (["P1", "P2"], "far", {}),
    (["P1", "P2"], True, {}),
    (["P1", "P2"], 3.0, {"sigma_m": -0.1}),
    (["P1", "P2"], 3.0, {"samples": 0}),
    (["P1", "P2"], 3.0, {"samples": 2.5}),
    (["P1", "P2"], 3.0, {"source": "guess"}),
])
def test_make_baseline_rejects(pair, d, kw):
    with pytest.raises(ValueError):
        bl.make_baseline(pair, d, t=T0, **kw)


def test_anchor_point_is_along_the_tapped_direction():
    x, y = bl.anchor_point(1.0, 1.0, 1.0, 11.0, 3.972)
    assert (round(x, 9), round(y, 9)) == (1.0, 4.972)
    x, y = bl.anchor_point(0.0, 0.0, 3.0, 4.0, 10.0)          # the tap distance does not matter, only its direction
    assert math.isclose(x, 6.0) and math.isclose(y, 8.0)
    with pytest.raises(ValueError):
        bl.anchor_point(1.0, 1.0, 1.0, 1.0, 3.0)


def test_spacing_check_tolerance_is_three_sigma_plus_20_cm():
    b = bl.make_baseline(["P1", "P2"], 3.972, sigma_m=0.041, samples=48, source="twr", t=T0)
    pa = {"id": "P1", "x": 0.0, "y": 0.0, "floor": "f3", "placed_by": "hand"}
    tol = 3 * 0.041 + 0.2
    assert bl.spacing_check(b, pa, {"id": "P2", "x": 3.972 + tol - 0.01, "y": 0.0, "floor": "f3", "placed_by": "hand"}) is None
    w = bl.spacing_check(b, pa, {"id": "P2", "x": 4.6, "y": 0.0, "floor": "f3", "placed_by": "hand"})
    assert w["kind"] == "baseline-mismatch" and w["pair"] == ["P1", "P2"] and w["tolerance_m"] == round(tol, 3)
    assert w["placed_m"] == 4.6 and w["diff_m"] == round(4.6 - 3.972, 3) and "by hand" in w["text"]
    # no sigma (a tape measure): 0.2 m alone
    m = bl.make_baseline(["P1", "P2"], 4.0, t=T0)
    assert bl.spacing_check(m, pa, {"id": "P2", "x": 4.19, "y": 0.0, "floor": "f3"}) is None
    assert bl.spacing_check(m, pa, {"id": "P2", "x": 4.21, "y": 0.0, "floor": "f3"}) is not None
    # compared in physical metres under an exercise scale
    assert bl.spacing_check(m, pa, {"id": "P2", "x": 12.0, "y": 0.0, "floor": "f3"}, scale=3.0) is None
    # different floors: not comparable
    assert bl.spacing_check(m, pa, {"id": "P2", "x": 9.0, "y": 0.0, "floor": "f2"}) is None


# ---------------------------------------------------------------------------- the session
def test_session_baselines_latest_per_unordered_pair(twin_raw, site_raw):
    s, events = _session(twin_raw, site_raw)
    assert s.step(T0)["baselines"] == [] and s.beacons()["warnings"] == []
    s.set_baseline(["P1", "P2"], 3.9, source="manual", now=T0)
    s.set_baseline(["P2", "P1"], 3.972, sigma_m=0.041, samples=48, source="twr", now=T0 + 1)
    s.set_baseline(["P1", "P3"], 2.5, now=T0 + 2)
    b = s.step(T0 + 3)
    assert b["baselines"] == [
        {"pair": ["P1", "P2"], "d_m": 3.972, "sigma_m": 0.041, "samples": 48, "t": T0 + 1, "source": "twr"},
        {"pair": ["P1", "P3"], "d_m": 2.5, "sigma_m": None, "samples": None, "t": T0 + 2, "source": "manual"}]
    assert any(e.get("name") == "pucks_changed" and "Baseline P1-P2: 3.97 m" in e["text"] for e in events)
    s.reset()
    assert s.step(T0 + 4)["baselines"] == []


def test_placed_by_on_every_puck(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(DOOR_WALL, room="r310")
    assert {p["placed_by"] for p in s.step(T0)["pucks"]} == {"hand"}
    s.set_pucks([{"id": "P3", "x": 2.0, "y": 2.0, "placed_by": "baseline"}], merge=True)
    s.set_pucks([{"id": "P4", "x": 3.0, "y": 3.0}], merge=True, placed_by="sim")
    got = {p["id"]: p["placed_by"] for p in s.step(T0 + 1)["pucks"]}
    assert got == {"P1": "hand", "P2": "hand", "P3": "baseline", "P4": "sim"}
    with pytest.raises(ValueError):
        s.set_pucks([{"id": "P1", "x": 1.0, "y": 1.0, "placed_by": "magic"}], merge=True)


def test_anchor_places_the_pair_at_the_measured_spacing(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    with pytest.raises(ValueError, match="no baseline"):
        s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P2")
    s.set_baseline(["P1", "P2"], 3.972, sigma_m=0.041, samples=48, source="twr", now=T0)
    with pytest.raises(ValueError, match="toward"):
        s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 6.7}, "P2")
    with pytest.raises(ValueError):
        s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P1")
    with pytest.raises(ValueError):
        s.anchor("P1", 9.15, 6.7, [9.15, 0.0], "P2")
    r = s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P2", room="r310")
    assert r["spacing_plan_m"] == 3.972 and r["baseline"]["d_m"] == 3.972
    pk = {p["id"]: p for p in s.step(T0 + 1)["pucks"]}
    assert (pk["P1"]["x"], pk["P1"]["y"]) == (9.15, 6.7)
    assert (pk["P2"]["x"], round(pk["P2"]["y"], 3)) == (9.15, round(6.7 - 3.972, 3))
    assert pk["P1"]["placed_by"] == pk["P2"]["placed_by"] == "baseline"
    assert pk["P2"]["sigma_m"] == round(math.hypot(0.08, 0.041), 3)          # placement plus measurement spread
    assert s.beacons()["warnings"] == []


def test_anchor_keeps_the_other_pucks_accessory(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live", simulated=False)
    s.set_pucks([dict(DOOR_WALL[0], accessory="DWM3001CDK (088D9BF3)"), dict(DOOR_WALL[1], accessory="DWM3001CDK (1BDB5E43)")],
                room="r310")
    s.set_baseline(["P1", "P2"], 3.0, now=T0)
    s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P2")
    pk = {p["id"]: p for p in s.step(T0)["pucks"]}
    assert pk["P2"]["accessory"] == "DWM3001CDK (1BDB5E43)" and pk["P1"]["accessory"] == "DWM3001CDK (088D9BF3)"


def test_anchor_and_check_follow_the_exercise_scale_in_live_mode(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live", simulated=False)
    s.set_rig_scale(3.0)
    s.set_baseline(["P1", "P2"], 1.33, sigma_m=0.04, source="twr", now=T0)
    r = s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P2", room="r310")
    assert r["spacing_plan_m"] == 3.99
    assert s.step(T0)["warnings"] == []
    # the simulator ignores the exercise scale (it is applied to live UWB ranges only), and so does the baseline
    sim, _ = _session(twin_raw, site_raw, source="sim")
    sim.set_rig_scale(3.0)
    sim.set_baseline(["P1", "P2"], 1.33, now=T0)
    assert sim.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P2")["spacing_plan_m"] == 1.33


def test_hand_placement_disagreeing_with_the_measurement_is_flagged(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks([DOOR_WALL[0], {"id": "P2", "x": 9.15, "y": 2.1}], room="r310")        # 4.6 m apart by hand
    s.set_baseline(["P1", "P2"], 3.972, sigma_m=0.041, samples=48, source="twr", now=T0)
    b = s.step(T0)
    assert len(b["warnings"]) == 1
    w = b["warnings"][0]
    assert w["pair"] == ["P1", "P2"] and w["placed_by"] == {"P1": "hand", "P2": "hand"}
    assert w["placed_m"] == 4.6 and w["measured_m"] == 3.972 and w["tolerance_m"] == 0.323
    # the warning is for the board: the agent's brief (and so every prompt) is unchanged by baselines and warnings
    plain, _ = _session(twin_raw, site_raw)
    plain.set_pucks([DOOR_WALL[0], {"id": "P2", "x": 9.15, "y": 2.1}], room="r310")
    assert json.dumps(brief_from_beacons(b), sort_keys=True) == json.dumps(brief_from_beacons(plain.step(T0)), sort_keys=True)
    assert b["limits"] == plain.beacons()["limits"]
    # re-anchoring resolves it
    s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 2.1}, "P2")
    assert s.step(T0 + 1)["warnings"] == []


def test_baselines_and_placed_by_survive_a_restart(twin_raw, site_raw, tmp_path):
    path = tmp_path / "pucks_live.json"
    s, _ = _session(twin_raw, site_raw, source="live", simulated=False, persist_path=path)
    s.set_baseline(["P1", "P2"], 3.972, sigma_m=0.041, samples=48, source="twr", now=T0)
    s.anchor("P1", 9.15, 6.7, {"x": 9.15, "y": 0.0}, "P2", room="r310")
    again, _ = _session(twin_raw, site_raw, source="live", simulated=False, persist_path=path)
    b = again.step(T0 + 5)
    assert b["baselines"] == s.step(T0 + 5)["baselines"]
    assert {p["id"]: p["placed_by"] for p in b["pucks"]} == {"P1": "baseline", "P2": "baseline"}


# ---------------------------------------------------------------------------- session bugs found in the sweep
def test_stub_locator_forgets_a_moved_puck_without_errors(twin_raw, site_raw, caplog):
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks(DOOR_WALL, room="r310")
    for i in range(4):
        for p in DOOR_WALL:
            s.ingest({"kind": "uwb", "t": T0 + i * 0.25, "puck": p["id"], "eid": "a3f19c02", "d_m": 3.0})
        s.step(T0 + i * 0.25)
    with caplog.at_level(logging.ERROR, logger="beacon.session"):
        s.set_pucks([{"id": "P2", "x": 5.0, "y": 2.7}], merge=True)          # moved 4 m: its ranges are forgotten
        b = s.step(T0 + 1.0)
    assert not [r for r in caplog.records if "could not forget" in r.getMessage()]
    assert {r["puck"] for r in b["phones"][0]["ranges"]} >= {"P1"}
    assert s._phones["B1"].track.inputs(T0 + 1.0)[0] and all(r["puck"] == "P1" for r in s._phones["B1"].track.inputs(T0 + 1.0)[0])


def test_a_non_object_in_a_batch_is_dropped_not_the_batch(twin_raw, site_raw):
    s, _ = _session(twin_raw, site_raw, source="live", simulated=False)
    s.set_pucks([dict(DOOR_WALL[0], accessory="X#0001")], room="r310")
    r = s.ingest_batch({"device": {"eid": "a3f19c02"},
                        "obs": [42, {"kind": "uwb", "t": T0, "accessory": "X#0001", "d_m": 2.0}]}, now=T0)
    assert r["accepted"] == 1 and r["rejected"] == 1 and "obs[0]" in r["errors"][0]
    with pytest.raises(ValueError):
        s.ingest_batch({"device": {"eid": "a3f19c02"}, "obs": [42, "x"]}, now=T0)


def test_ingest_dedup_eviction_keeps_recent_readings(twin_raw, site_raw, monkeypatch):
    import beacon.session as sess
    monkeypatch.setattr(sess, "SEEN_MAX", 10)
    s, _ = _session(twin_raw, site_raw, source="live", simulated=False)
    s.set_pucks([dict(DOOR_WALL[0], accessory="X#0001")], room="r310")
    obs = {"kind": "uwb", "t": T0, "accessory": "X#0001", "d_m": 2.0}
    for k in range(9):                                          # readings from long ago fill the table
        s.ingest_batch({"device": {"eid": f"{k:08x}"}, "obs": [dict(obs, t=T0 - 600)]}, now=T0 - 600)
    assert s.ingest_batch({"device": {"eid": "a3f19c02"}, "obs": [obs]}, now=T0)["accepted"] == 1
    for k in range(9, 12):                                      # ... and overflow it now
        s.ingest_batch({"device": {"eid": f"{k:08x}"}, "obs": [dict(obs, t=T0 + 1)]}, now=T0 + 1)
    # the first phone re-sends its reading (the iOS app does for up to 25 s): it must not count twice
    r = s.ingest_batch({"device": {"eid": "a3f19c02"}, "obs": [obs]}, now=T0 + 2)
    assert r["accepted"] == 0 and r["skipped"] == 1
    assert len(s._seen_readings) <= 10


# ---------------------------------------------------------------------------- HTTP
def test_baseline_and_anchor_routes(make_client):
    c = make_client("live")
    r = c.post("/api/pucks/baseline", json={"pair": ["P2", "P1"], "d_m": 3.972, "sigma_m": 0.041, "samples": 48, "source": "twr"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["baseline"]["pair"] == ["P1", "P2"] and j["baseline"]["d_m"] == 3.972
    assert set(j["baseline"]) == {"pair", "d_m", "sigma_m", "samples", "t", "source"} and abs(j["baseline"]["t"] - time.time()) < 60
    for bad in ({"pair": ["P1", "P1"], "d_m": 3}, {"pair": ["P1", "P9"], "d_m": 3}, {"pair": ["P1", "P2"], "d_m": 0.05},
                {"pair": ["P1", "P2"], "d_m": 200}, {"pair": ["P1", "P2"], "d_m": 3, "source": "radar"},
                {"pair": ["P1", "P2"], "d_m": 3, "dry_run": True}, {"d_m": 3}):
        assert c.post("/api/pucks/baseline", json=bad).status_code == 400, bad
    assert c.post("/api/pucks/baseline", json=[1, 2]).status_code == 400
    assert c.post("/api/pucks/anchor", json={"anchor": "P1", "x": 9.15, "y": 6.7, "toward": {"x": 9.15, "y": 0}, "other": "P3"}).status_code == 400
    assert c.post("/api/pucks/anchor", json={"anchor": "P1", "x": 9.15, "y": 6.7, "toward": {"x": 9.15, "y": 6.7}, "other": "P2"}).status_code == 400
    assert c.post("/api/pucks/anchor", json={"anchor": "P1", "x": 9.15, "y": 6.7, "toward": {"x": 9.15, "y": 0}, "other": "P2",
                                             "room": "nowhere"}).status_code == 400
    r = c.post("/api/pucks/anchor", json={"anchor": "P1", "x": 9.15, "y": 6.7, "toward": {"x": 9.15, "y": 0.0}, "other": "P2",
                                          "room": "r310", "floor": None})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["room"] == "r310" and j["baseline"]["d_m"] == 3.972
    assert [(p["id"], p["placed_by"]) for p in j["pucks"]] == [("P1", "baseline"), ("P2", "baseline")]
    b = c.get("/api/beacons").json()
    deadline = time.time() + 3
    while not b.get("baselines") and time.time() < deadline:
        time.sleep(0.1)
        b = c.get("/api/beacons").json()
    assert b["baselines"][0]["pair"] == ["P1", "P2"] and b["warnings"] == []
    # hand placement that disagrees: flagged
    c.post("/api/pucks", json={"pucks": [{"id": "P2", "x": 9.15, "y": 2.0}], "merge": True})
    deadline = time.time() + 3
    while not b.get("warnings") and time.time() < deadline:
        time.sleep(0.1)
        b = c.get("/api/beacons").json()
    assert b["warnings"][0]["kind"] == "baseline-mismatch" and b["warnings"][0]["placed_by"]["P2"] == "hand"


def test_pucks_route_placed_by(make_client):
    c = make_client("live")
    r = c.post("/api/pucks", json={"pucks": [{"id": "P1", "x": 7.9, "y": 6.7}, {"id": "P2", "x": 5, "y": 5, "placed_by": "baseline"}],
                                   "room": "r310"})
    assert [(p["id"], p["placed_by"]) for p in r.json()["pucks"]] == [("P1", "hand"), ("P2", "baseline")]
    for how in ("sim", "layout", "guess", 3):
        assert c.post("/api/pucks", json={"pucks": [{"id": "P1", "x": 1, "y": 1, "placed_by": how}]}).status_code == 400


def test_fake_engine_has_the_a12_fields(make_client):
    c = make_client("fake")
    b = c.get("/api/beacons").json()
    assert b["baselines"] == [] and b["warnings"] == []
    assert all(p["placed_by"] == "sim" for p in b["pucks"])
    assert c.post("/api/pucks/baseline", json={"pair": ["P1", "P2"], "d_m": 3}).status_code == 409


@pytest.mark.skipif(not (HAVE_LOCATOR and HAVE_SIM), reason="beacon.sim / beacon.locate not available")
def test_simulated_pucks_are_marked_and_follow_an_anchor(make_client):
    c = make_client("sim")
    assert {p["placed_by"] for p in c.post("/api/pucks", json={"n": 2, "room": "r310"}).json()["pucks"]} == {"layout"}
    assert {p["placed_by"] for p in c.post("/api/pucks", json={"n": 3, "room": "r310", "seed": 1}).json()["pucks"]} == {"sim"}
    c.post("/api/pucks/baseline", json={"pair": ["P1", "P2"], "d_m": 3.0, "source": "manual"})
    c.post("/api/pucks/anchor", json={"anchor": "P1", "x": 9.0, "y": 6.0, "toward": {"x": 9.0, "y": 0.0}, "other": "P2"})
    truth = c.get("/api/sim/truth").json()
    tp = {p["id"]: p for p in truth["pucks"]}
    assert math.isclose(tp["P2"]["y"], 3.0, abs_tol=1e-6) and math.isclose(tp["P2"]["x"], 9.0, abs_tol=1e-6)


def test_re_placing_pucks_by_position_keeps_their_bluetooth_names(twin_raw, site_raw):
    """A board's Bluetooth name is its identity, not part of where it is drawn. Re-placing the pucks by position only (what
    the command board's placement guide sends) used to drop each puck's name, so real phones' ranges stopped matching a puck."""
    s, _ = _session(twin_raw, site_raw)
    s.set_pucks([{**DOOR_WALL[0], "accessory": "DWM3001CDK (AAAA0001)"},
                 {**DOOR_WALL[1], "accessory": "DWM3001CDK (BBBB0002)"}], room="r310")
    s.set_pucks([{"id": "P1", "x": 9.15, "y": 6.6, "placed_by": "hand"},
                 {"id": "P2", "x": 9.15, "y": 2.65, "placed_by": "baseline"}], room="r310")
    by = {p["id"]: p for p in s.pucks}
    assert by["P1"]["accessory"] == "DWM3001CDK (AAAA0001)" and by["P2"]["accessory"] == "DWM3001CDK (BBBB0002)"
    assert by["P2"]["placed_by"] == "baseline" and by["P1"]["y"] == 6.6
    # One board, one puck: a name claimed by another puck leaves the first, whatever order the request lists them in.
    for order in ([{"id": "P1", "x": 9.15, "y": 6.6}, {"id": "P2", "x": 9.15, "y": 2.65, "accessory": "DWM3001CDK (AAAA0001)"}],
                  [{"id": "P2", "x": 9.15, "y": 2.65, "accessory": "DWM3001CDK (AAAA0001)"}, {"id": "P1", "x": 9.15, "y": 6.6}]):
        s.set_pucks([{**DOOR_WALL[0], "accessory": "DWM3001CDK (AAAA0001)"},
                     {**DOOR_WALL[1], "accessory": "DWM3001CDK (BBBB0002)"}], room="r310")
        s.set_pucks(order, room="r310")
        by = {p["id"]: p for p in s.pucks}
        assert by["P2"]["accessory"] == "DWM3001CDK (AAAA0001)" and by["P1"]["accessory"] is None
        assert s._accessories.get("DWM3001CDK (AAAA0001)") == "P2"
