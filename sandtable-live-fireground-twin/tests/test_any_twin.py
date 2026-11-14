"""Live mode: the reflex layer, the scripts' choices and accountability on a twin unlike the fixture's.

A small made-up "scanned suite" (no stairs, no corridor, rooms without suite numbers, its entrance as the only exit),
shaped like what a normalised Matterport scan of one suite gives. No real data in here.
"""
import types

import pytest

from sandtable.adapters import build_provider
from sandtable.beacon_link import accountability
from sandtable.incident import Incident
from sandtable.scout.session import entrance_ref, fire_room, floor_number
from sandtable.spatial import Twin


def suite_twin() -> dict:
    nodes = [("nx", 0.6, 4.0, "ra", "exit"), ("na", 2.5, 4.0, "ra", "sweep"), ("n1", 5.0, 2.0, "ra", "door"), ("nb", 7.5, 2.0, "rb", "sweep"),
             ("n2", 5.0, 6.0, "ra", "door"), ("nc", 7.5, 6.0, "rc", "sweep")]
    edges = [("nx", "na", 1.9, None), ("na", "n1", 3.2, None), ("n1", "nb", 2.5, "door:d1"), ("na", "n2", 3.2, None), ("n2", "nc", 2.5, "door:d2")]
    rect = lambda x0, y0, x1, y1: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return {"schema": "sandtable.twin/1", "source": {"kind": "test", "synthetic": True}, "georef": {"origin": {"lat": 0.0, "lon": 0.0}, "bearing_deg": 90.0},
            "exercise_floor": "fL", "floors": [{"id": "fL", "index": 0, "label": "Level 3", "elevation_m": 0.0, "outline": rect(0, 0, 10, 8), "modelled": True}],
            "rooms": [{"id": "ra", "floor": "fL", "kind": "office", "label": "Reception", "suite": None, "polygon": rect(0, 0, 5, 8), "area_m2": 40, "height_m": 2.7},
                      {"id": "rb", "floor": "fL", "kind": "office", "label": "Office", "suite": None, "polygon": rect(5, 0, 10, 4), "area_m2": 20, "height_m": 2.7},
                      {"id": "rc", "floor": "fL", "kind": "office", "label": "Lab", "suite": None, "polygon": rect(5, 4, 10, 8), "area_m2": 20, "height_m": 2.7}],
            "walls": [{"floor": "fL", "a": a, "b": b} for a, b in ([[0, 0], [10, 0]], [[10, 0], [10, 8]], [[10, 8], [0, 8]], [[0, 8], [0, 0]], [[5, 0], [5, 8]], [[5, 4], [10, 4]])],
            "doors": [{"id": "dx", "floor": "fL", "at": [0.0, 4.0], "width_m": 0.9, "rooms": ["ra", "ra"], "kind": "exit", "node": "nx"},
                      {"id": "d1", "floor": "fL", "at": [5.0, 2.0], "width_m": 0.9, "rooms": ["rb", "ra"], "kind": "door", "node": "n1"},
                      {"id": "d2", "floor": "fL", "at": [5.0, 6.0], "width_m": 0.9, "rooms": ["rc", "ra"], "kind": "door", "node": "n2"}],
            "stairs": [], "sweeps": [], "tags": [],
            "graph": {"nodes": [{"id": i, "floor": "fL", "at": [x, y], "room": r, "kind": k} for i, x, y, r, k in nodes],
                      "edges": [{"a": a, "b": b, "len_m": L, "via": v} for a, b, L, v in edges]}}


@pytest.fixture
def world():
    provider = build_provider()
    twin = Twin(suite_twin())
    t = [1000.0]
    msgs, sits = [], []
    inc = Incident(twin, provider, msgs.append, clock=lambda: t[0])
    inc.on_situation = sits.append

    def step(seconds, dt=0.1):
        for _ in range(int(seconds / dt)):
            t[0] += dt
            inc.tick()
    return types.SimpleNamespace(provider=provider, twin=twin, inc=inc, step=step, sits=sits)


def test_staging_exits_and_the_fire_room_need_no_stair_corridor_or_suite_numbers(world):
    w = world
    assert floor_number(w.twin) == 3 and entrance_ref(w.twin) == "nx"
    room = fire_room(w.twin, w.provider)
    assert room in ("rb", "rc")
    e1 = w.inc.units["E1"]
    assert abs(e1["x"] - 0.6) <= 1.3 and w.inc._in_exit(e1) and w.inc._in_exit(w.inc.units["RIT"])     # staged at the entrance
    w.inc.start()
    w.inc.begin_operations()
    assert w.inc.assign("E1", "search", room)["ok"]
    w.step(8)
    assert e1["room"] == room and e1["status"] == "interior"
    out = w.inc.assign("E1", "exit", "exit")
    assert out["ok"] and e1["assignment"]["target_label"] == "nearest exit"
    w.step(8)
    assert e1["status"] == "out"


def test_a_phone_rescue_ends_through_all_out_on_a_scanned_suite(world):
    w = world
    room = fire_room(w.twin, w.provider)
    c = w.twin.room_centre(room)
    w.inc.start()
    w.inc.begin_operations()
    w.inc.activate_beacon(True)
    obj = {"type": "beacons", "seq": 1, "simulated": True, "room": room, "phones": [
        {"id": "B1", "opt_in": True, "method": "uwb-twr", "geometry": "fix", "x": c[0], "y": c[1], "sigma_m": 0.3, "confidence": 0.8,
         "room": room, "room_label": w.twin.room(room)["label"], "node": None, "pruned": [], "candidates": [],
         "report": {"status": "trapped", "people": 2, "can_move": False}, "text": "Phone B1: 2 people, trapped"}]}
    w.inc.on_beacons(obj)
    assert [s["kind"] for s in w.sits] == ["phone"]
    assert w.inc.assign("RIT", "rescue", "B1")["ok"]
    w.step(10)
    assert w.inc.phones["B1"]["status"] == "reached" and w.sits[-1]["kind"] == "phone_reached"
    w.inc.assign("RIT", "exit", "exit")
    w.step(10)
    assert w.inc.phase == "terminated" and w.sits[-1]["kind"] == "all_out" and w.inc.phones["B1"]["status"] == "out"


def test_accountability_falls_back_to_rooms_and_the_floors_tenants(world):
    w = world
    acc = accountability(None, w.inc, w.twin, w.provider)
    rooms = [r for r in acc["rows"] if r["room"]]
    tenants = [r for r in acc["rows"] if not r["room"]]
    assert {r["room"] for r in rooms} == {"ra", "rb", "rc"} and tenants and all(r["tenant"] for r in tenants)
    assert all(r["state"] == "unscanned" for r in acc["rows"])


def test_the_beacon_script_derives_its_scene_inside_any_room(world):
    from sandtable.director import Director
    d = Director(types.SimpleNamespace(twin=world.twin))
    room = fire_room(world.twin, world.provider)
    sc = d._scene_for(room)
    for p in sc["pucks"] + sc["phones"]:
        assert world.twin.locate(p["x"], p["y"])["room"] == room
    assert len(sc["pucks"]) == 2 and len(sc["phones"]) == 2 and sc["derived"]
