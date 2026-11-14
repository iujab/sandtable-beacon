"""Reflex layer with a fake hub and a fake clock: registration, map matching, no-motion mayday, hazards, contact, all out."""
import json

import pytest

from sandtable import config
from sandtable.adapters import build_provider
from sandtable.incident import Incident
from sandtable.spatial import Twin


class Rig:
    """A stand-in for the hub: remembers where each tag was teleported and feeds positions back."""

    def __init__(self):
        self.t = 1_000_000.0
        self.msgs: list[dict] = []
        self.site: dict[str, tuple[float, float]] = {}
        self.provider = build_provider()
        self.provider.matterport.clear_overlay()            # like the server: every incident starts from clean local files
        self.provider.costar.clear_corrections()
        self.twin = Twin(self.provider.twin_raw())
        self.situations: list[dict] = []
        self.inc = Incident(self.twin, self.provider, self.msgs.append, clock=lambda: self.t)
        self.inc.teleport = lambda tag, x, y: self.site.__setitem__(tag, (x, y))
        self.inc.on_situation = self.situations.append
        self.inc.on_hello({"type": "hello", "simulated": True, "source": "sim:test", "profile": "3A2T",
                           "nodes": {"A1": {"role": "anchor"}, "A2": {"role": "anchor"}, "A3": {"role": "anchor"}, "T1": {"role": "tag"}, "T2": {"role": "tag"}}})
        self.inc.on_anchors({"type": "anchors", "anchors": {"A1": {"x": 0, "y": 0}, "A2": {"x": 3.6, "y": 0}, "A3": {"x": 1.8, "y": 4.4}}})

    def step(self, seconds: float, dt: float = 0.1, jitter: float = 0.0):
        n = int(round(seconds / dt))
        for i in range(n):
            self.t += dt
            for tag, (x, y) in list(self.site.items()):
                j = jitter * (1 if i % 2 else -1)
                self.inc.on_pos({"type": "pos", "id": tag, "t": self.t, "x": x + j, "y": y - j, "cov": [0.004, 0.0, 0.004], "q": 0.9})
            self.inc.tick()

    def kinds(self):
        return [s["kind"] for s in self.situations]


@pytest.fixture
def rig():
    r = Rig()
    r.step(1.0)
    return r


def fire_room(r):
    floor_no = int(r.twin.floor()["label"])
    hazard = {t["suite"] for t in r.provider.costar.list_tenants(floor_no) if t.get("life_hazard")}
    return max((s for s in r.twin.rooms("suite") if s["suite"] in hazard), key=lambda s: r.twin.route("stairA", s["id"])["length_m"])["id"]


def start_operations(r):
    r.inc.start()
    r.inc.begin_operations()
    assert r.inc.phase == "operations"


def test_units_bind_to_tags_register_and_stage_at_the_attack_stair(rig):
    e1, rit = rig.inc.units["E1"], rig.inc.units["RIT"]
    assert e1["tag"] == "T1" and rit["tag"] == "T2" and e1["physical"] and rit["physical"]
    assert 5 < rig.inc.registration.scale < 20
    stair_room = rig.twin.stair("stairA")["room"]
    assert e1["room"] == stair_room and rit["room"] == stair_room
    assert e1["sigma_m"] == pytest.approx((0.004 ** 0.5) * rig.inc.registration.scale, abs=0.02)
    assert rig.kinds() == []


def test_a_single_tag_makes_the_rescue_team_virtual():
    r = Rig()
    r.inc.on_hello({"type": "hello", "simulated": True, "nodes": {"A1": {"role": "anchor"}, "T1": {"role": "tag"}}})
    assert r.inc.units["RIT"]["physical"] is False and r.inc.units["RIT"]["x"] is not None


def test_no_motion_raises_a_mayday_only_after_the_threshold_and_not_in_the_stair(rig):
    start_operations(rig)
    rig.step(config.NO_MOTION_S + 3, jitter=0.03)                      # everybody is still in the stair: exempt
    assert rig.inc.mayday is None
    room = fire_room(rig)
    rig.inc.assign("E1", "attack", room, source="test")
    rig.inc.drive("E1")
    rig.step(30)
    e1 = rig.inc.units["E1"]
    assert e1["room"] == room and e1["status"] == "interior" and rig.inc.mayday is None or rig.inc.mayday["unit"] == "E1"
    rig.step(config.NO_MOTION_S + 2, jitter=0.03)                      # noise at exercise scale must not look like movement
    assert rig.inc.mayday and rig.inc.mayday["unit"] == "E1" and rig.inc.mayday["reason"] == "no_motion"
    assert rig.inc.phase == "mayday" and rig.kinds() == ["mayday"]
    assert any(m.get("type") == "alert" and m["level"] == "mayday" for m in rig.msgs)


def test_a_moving_crew_does_not_trigger(rig):
    start_operations(rig)
    room = fire_room(rig)
    rig.inc.assign("E1", "attack", room, source="test")
    rig.inc.drive("E1")
    rig.step(8)
    assert rig.inc.mayday is None and rig.inc.units["E1"]["status"] == "interior"


def run_to_mayday(r):
    start_operations(r)
    room = fire_room(r)
    r.inc.assign("E1", "attack", room, source="test")
    r.inc.drive("E1")
    r.step(30 + config.NO_MOTION_S + 2)
    assert r.inc.mayday
    return room


def test_hazard_invalidates_the_rescue_route_and_reassignment_goes_around(rig):
    run_to_mayday(rig)
    out = rig.inc.assign("RIT", "rit_rescue", "E1")
    assert out["ok"] and rig.inc.phase == "rescue"
    rit = rig.inc.units["RIT"]
    first = rit["route"]["length_m"]
    rig.step(3)
    nodes = rit["route"]["nodes"]
    here = nodes.index(rit["_logical"])
    edge = next(f"{a}-{b}" for a, b in zip(nodes[here + 1:], nodes[here + 2:])
                if all(rig.twin.node(n)["kind"] == "sweep" and rig.twin.room(rig.twin.node(n)["room"])["kind"] == "corridor" for n in (a, b)))
    hz = rig.inc.mark_hazard(edge, "collapse", "test", source="inject")
    assert hz["geometry"]["kind"] == "edge" and rit["route"]["valid"] is False and rit["_follow"] is None
    assert rig.kinds()[-1] == "hazard" and rig.situations[-1]["data"]["affected_units"] == ["RIT"]
    again = rig.inc.assign("RIT", "rit_rescue", "E1")
    assert again["ok"] and rit["route"]["valid"] and rit["route"]["length_m"] > first
    assert rig.inc.stats["replans"] == 1
    for a, b in zip(rit["route"]["nodes"], rit["route"]["nodes"][1:]):
        assert {a, b} != set(edge.split("-"))


def test_contact_then_exit_then_all_out(rig):
    run_to_mayday(rig)
    rig.inc.assign("RIT", "rit_rescue", "E1")
    rig.step(40)
    assert rig.inc.contact_done and rig.inc.phase == "egress" and rig.kinds()[-1] == "contact"
    assert rig.situations[-1]["data"]["rescuer"] == "RIT"
    rig.inc.assign("RIT", "exit", "exit")
    rig.step(30)
    assert rig.inc.phase == "terminated" and rig.kinds()[-1] == "all_out" and rig.inc.mayday is None
    assert all(u["status"] == "out" for u in rig.inc.units.values())
    m = rig.inc.metrics()
    assert m["mayday_to_contact_s"] > 0 and m["mayday_to_all_out_s"] > m["mayday_to_contact_s"]


def test_snapshot_is_discrete_and_state_matches_the_contract(rig):
    run_to_mayday(rig)
    snap = rig.inc.snapshot()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        else:
            assert not isinstance(o, float), f"float {o!r} in the snapshot"
    walk(snap)
    assert snap["mayday"] == {"unit": "E1", "room": snap["units"][0]["room"], "room_label": snap["units"][0]["room_label"], "reason": "no_motion"}
    assert json.dumps(snap, sort_keys=True) == json.dumps(rig.inc.snapshot(), sort_keys=True)
    st = rig.inc.state()
    for key in ("type", "t", "phase", "elapsed_s", "units", "hazards", "mayday", "anchors"):
        assert key in st
    for key in ("id", "label", "kind", "physical", "tag", "status", "x", "y", "floor", "room", "room_label", "suite", "node", "sweep", "sigma_m", "still_s", "air_pct", "time_in_s", "assignment", "route", "geo"):
        assert key in st["units"][0]
    assert not any(k.startswith("_") for k in st["units"][0])


def test_real_hardware_tags_are_never_moved_by_software(rig):
    rig.inc.simulated = False
    with pytest.raises(PermissionError):
        rig.inc.move_unit("E1", 5.0, 5.0)
    start_operations(rig)
    before = dict(rig.site)
    rig.inc.assign("RIT", "search", fire_room(rig))
    assert rig.inc.units["RIT"]["_follow"] is None and rig.site == before


def test_button_event_and_manual_mayday(rig):
    start_operations(rig)
    rig.inc.on_event({"type": "event", "id": "T2", "name": "button", "data": {"n": 1}})
    assert rig.inc.mayday["unit"] == "RIT" and rig.inc.mayday["reason"] == "button"
    rig.inc.reset()
    assert rig.inc.phase == "standby" and rig.inc.mayday is None and rig.inc.hazards == []
