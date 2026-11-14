"""The building record against the phones (beacon/roster.py): the join, the estimates and their basis, the
accounting, the priorities, the next puck, record checks, and no names. Offline, fixtures only."""
from __future__ import annotations

import copy
import json
import math

import pytest

from beacon import config, roster
from beacon.twin import Twin

FLOOR = "f3"


@pytest.fixture(scope="module")
def twin() -> Twin:
    return Twin(config.twin_raw())


@pytest.fixture
def site() -> dict:
    return copy.deepcopy(config.site_raw())


def suite_rooms(twin) -> dict[str, dict]:
    return {r["suite"]: r for r in twin.rooms("suite") if r.get("floor") == FLOOR and r.get("suite")}


def tenant_suites(site) -> set[str]:
    return {str(t["suite"]) for t in site["tenants"] if t.get("floor") == 3}


def fix(pid, room, people=1, can_move=True, home=None, x=None, y=None):
    return {"id": pid, "geometry": "fix", "method": "uwb-twr", "room": room, "x": x, "y": y, "stale": False,
            "report": {"status": "trapped" if can_move is False else "sheltering", "people": people, "can_move": can_move,
                       "home_suite": home, "safe_outside": False}}


def safe_out(pid, home, people=1):
    return {"id": pid, "geometry": "none", "method": "ble-presence", "room": None, "stale": False,
            "report": {"status": "ok", "people": people, "can_move": True, "home_suite": home, "safe_outside": True}}


def cold(cid, room, count):
    return {"id": cid, "puck": "P1", "room": room, "count": count, "count_range": [max(0, count - 1), count + 2]}


def puck(twin, room="r310"):
    cx, cy = twin.room_centre(room)
    return {"id": "P1", "x": cx, "y": cy, "room": room}


def row(acc, suite):
    return next(r for r in acc["suites"] if r["suite"] == suite)


# ---------------------------------------------------------------------------- the join
def test_join_by_suite_covers_every_suite_room_of_the_floor(twin, site):
    rows = roster.suites(site, twin, FLOOR)
    rooms = suite_rooms(twin)
    assert [r["suite"] for r in rows] == sorted(rooms)
    for r in rows:
        assert r["room"] == rooms[r["suite"]]["id"]
        mine = [t for t in site["tenants"] if t.get("floor") == 3 and str(t["suite"]) == r["suite"]]
        assert r["tenants"] == len(mine)
        if mine:
            assert set(r["industry"].split("; ")) == {t["industry"] for t in mine}
            assert r["naics"] == [t["naics"] for t in mine if t.get("naics")]
    multi = [r for r in rows if r["tenants"] > 1]
    assert multi, "the fixture has suites with several registrations"


def test_tenants_of_other_floors_are_not_joined(twin, site):
    other = {str(t["suite"]) for t in site["tenants"] if t.get("floor") != 3}
    rows = {r["suite"]: r for r in roster.suites(site, twin, FLOOR)}
    assert not (other & set(rows) - tenant_suites(site))


# ---------------------------------------------------------------------------- vacant on record
def test_suites_without_tenant_are_vacant_on_record(twin, site):
    vacant = set(suite_rooms(twin)) - tenant_suites(site)
    assert vacant, "the fixture has suites with no active registration"
    acc = roster.accountability(site, twin, FLOOR, [], [], [])
    for s in vacant:
        r = row(acc, s)
        assert r["vacant"] and r["state"] == "vacant-on-record"
        assert r["expected"] == {"people": 0, "low": 0, "high": 0, "basis": r["expected"]["basis"]}
        assert "no tenant on record" in r["expected"]["basis"]
        assert r["priority"] is None


def test_available_space_in_the_record_marks_a_suite_vacant(twin, site):
    site["available_spaces"] = [{"suite": "305", "floor": 3, "sf": 1000}]
    r = next(r for r in roster.suites(site, twin, FLOOR) if r["suite"] == "305")
    assert r["vacant"] is True


# ---------------------------------------------------------------------------- expected people: basis and band per branch
def test_expected_from_twin_area_with_the_outpatient_load_factor(twin, site):
    r = next(r for r in roster.suites(site, twin, FLOOR) if r["suite"] == "310")
    area_sf = twin.room("r310")["area_m2"] * roster.SQM_TO_SQFT
    high = math.ceil(area_sf / 100.0)
    assert r["sf"] is None and r["sf_source"] == "twin room area"
    assert r["expected"]["high"] == high
    assert r["expected"]["people"] == round(high * roster.UTILISATION_OPEN)
    assert r["expected"]["low"] == math.floor(high * roster.UTILISATION_OPEN_LOW)
    b = r["expected"]["basis"]
    assert "(twin room area)" in b and "100 sf per person" in b and "outpatient load factor" in b and b.endswith("estimate")


def test_expected_business_load_factor_for_a_non_medical_tenant(twin, site):
    r = next(r for r in roster.suites(site, twin, FLOOR) if r["suite"] == "305")
    assert "541" in "".join(r["naics"])
    area_sf = twin.room("r305")["area_m2"] * roster.SQM_TO_SQFT
    assert r["expected"]["high"] == math.ceil(area_sf / 150.0)
    assert "150 sf per person (business load factor" in r["expected"]["basis"]


def test_expected_from_record_area(twin, site):
    for t in site["tenants"]:
        if t.get("floor") == 3 and str(t["suite"]) == "305":
            t["sf"] = 1200
    r = next(r for r in roster.suites(site, twin, FLOOR) if r["suite"] == "305")
    assert r["sf"] == 1200.0 and r["sf_source"] == "record"
    assert r["expected"]["high"] == 8                      # ceil(1200 / 150)
    assert "1,200 sf (record area)" in r["expected"]["basis"]


def test_expected_from_record_head_count(twin, site):
    for t in site["tenants"]:
        if t.get("floor") == 3 and str(t["suite"]) == "307":
            t["employees"] = 10
    r = next(r for r in roster.suites(site, twin, FLOOR) if r["suite"] == "307")
    assert r["expected"]["high"] == 10
    assert r["expected"]["people"] == 8                    # open hours assumed: 80 % of the head count
    assert r["expected"]["low"] == 2
    assert r["expected"]["basis"].startswith("record head count 10")


def test_head_count_needs_every_tenant_of_the_suite(twin, site):
    first = next(t for t in site["tenants"] if t.get("floor") == 3 and str(t["suite"]) == "304")
    first["employees"] = 10                                # suite 304 has two registrations: one head count is not enough
    r = next(r for r in roster.suites(site, twin, FLOOR) if r["suite"] == "304")
    assert "record head count" not in r["expected"]["basis"]


@pytest.mark.parametrize("hour,words", [(10.0, "open hours"), (22.0, "outside open hours"), (None, "open hours assumed")])
def test_time_of_day(twin, site, hour, words):
    r = next(r for r in roster.suites(site, twin, FLOOR, now_local_hour=hour) if r["suite"] == "310")
    b = r["expected"]["basis"]
    assert words in b
    if hour == 10.0:
        assert "assumed" not in b and "outside" not in b
    if hour == 22.0:
        assert r["expected"]["people"] == round(r["expected"]["high"] * roster.UTILISATION_CLOSED)
        assert r["expected"]["low"] == 0


# ---------------------------------------------------------------------------- accounting
def test_safe_outside_is_accounted_for_not_searched_for(twin, site):
    phones = [fix("B1", "r310", people=2, can_move=False, home="310"), safe_out("B2", "310")]
    acc = roster.accountability(site, twin, FLOOR, phones, [], [puck(twin)])
    r = row(acc, "310")
    assert r["app_registered"] == 2
    assert r["safe_outside"] == 1
    assert r["phones_opt_in"] == 1 and r["people_reported"] == 2 and r["located"] == 1
    e = r["expected"]
    assert r["unaccounted"] == {"low": max(0, e["low"] - 3), "high": max(0, e["high"] - 3)}
    assert acc["totals"]["safe_outside"] == 1 and acc["totals"]["phones_opt_in"] == 1


def test_a_ring_phone_counts_in_the_pucks_room_as_an_assumption(twin, site):
    ring = {"id": "B3", "geometry": "ring", "room": None, "stale": False, "ring": {"puck": "P1"},
            "report": {"status": "injured", "people": 1, "can_move": True}}
    acc = roster.accountability(site, twin, FLOOR, [ring], [], [puck(twin)])
    r = row(acc, "310")
    assert r["phones_opt_in"] == 1 and r["located"] == 0
    assert "not yet located (range only)" in r["why"]


# ---------------------------------------------------------------------------- priorities and the next puck
def test_priorities_order(twin, site):
    haz = {r["suite"]: r["life_hazard"] for r in roster.suites(site, twin, FLOOR)}
    assert haz["301"] and not haz["305"]
    phones = [fix("B1", "r302", people=1, can_move=True), fix("B2", "r310", people=2, can_move=False)]
    presence = [cold("C1", "r305", 3), cold("C2", "r301", 2)]
    acc = roster.accountability(site, twin, FLOOR, phones, presence, [puck(twin)], focus_room="r310")
    order = [(p["suite"], p["action"]) for p in acc["priorities"]]
    assert order[:4] == [("310", "rescue"), ("302", "rescue"), ("301", "search"), ("305", "search")]
    assert all(a == "scan" for _s, a in order[4:])
    assert [p["rank"] for p in acc["priorities"]] == list(range(1, len(order) + 1))
    assert acc["priorities"][0]["target"] == "B2"
    assert "devices, not people" in row(acc, "305")["why"]


def test_next_puck_is_the_first_scan_priority_with_its_door(twin, site):
    acc = roster.accountability(site, twin, FLOOR, [fix("B1", "r310")], [], [puck(twin)], focus_room="r310")
    scans = [p for p in acc["priorities"] if p["action"] == "scan"]
    assert acc["next_puck"]["suite"] == scans[0]["suite"]
    door = acc["next_puck"]["door"]
    assert isinstance(door, list) and len(door) == 2
    r = row(acc, acc["next_puck"]["suite"])
    assert r["state"] == "unscanned" and not r["vacant"]
    # equal scores are broken by walking distance from the room being worked
    def score(p):
        r = row(acc, p["suite"])
        return r["expected"]["people"] * roster._hazard_weight(r["life_hazard"])
    scores = [score(p) for p in scans]
    assert scores == sorted(scores, reverse=True)
    top = [p for p in scans if score(p) == scores[0]]
    assert len(top) > 1
    dist = [twin.route("r310", p["room"])["length_m"] for p in top]
    assert dist == sorted(dist)


def test_no_scan_priority_for_vacant_or_scanned_suites(twin, site):
    acc = roster.accountability(site, twin, FLOOR, [], [], [puck(twin)], focus_room="r310")
    scanned = {"310"}
    vacant = set(suite_rooms(twin)) - tenant_suites(site)
    for p in acc["priorities"]:
        assert p["suite"] not in scanned | vacant
    assert row(acc, "310")["state"] == "silent"


# ---------------------------------------------------------------------------- record checks
def test_record_check_when_a_vacant_suite_has_phones_or_devices(twin, site):
    vacant = sorted(set(suite_rooms(twin)) - tenant_suites(site))
    a, b = vacant[0], vacant[1]
    ra, rb = suite_rooms(twin)[a]["id"], suite_rooms(twin)[b]["id"]
    acc = roster.accountability(site, twin, FLOOR, [fix("B1", ra, people=1)], [cold("C1", rb, 2)], [puck(twin)])
    checks = {c["suite"]: c["text"] for c in acc["record_checks"]}
    assert a in checks and "vacant on record" in checks[a] and "people report" in checks[a]
    assert b in checks and "devices" in checks[b]
    assert row(acc, a)["state"] == "people-reported"
    assert row(acc, b)["state"] == "devices-heard"


def test_no_record_check_without_evidence(twin, site):
    acc = roster.accountability(site, twin, FLOOR, [fix("B1", "r310")], [], [puck(twin)])
    assert acc["record_checks"] == []


# ---------------------------------------------------------------------------- names
def test_no_person_names_anywhere(twin, site):
    names = ["Jane Q. Roe", "John Example", "Alex Sample"]
    for t in site["tenants"]:
        t["contact_name"] = names[0]
        t["owner_name"] = names[1]
    site["property"]["property_manager"] = {"name": names[2], "phone": "555-0100"}
    phones = [fix("B1", "r310", people=2, can_move=False, home="310"), safe_out("B2", "310")]
    acc = roster.accountability(site, twin, FLOOR, phones, [cold("C1", "r310", 2)], [puck(twin)], focus_room="r310")
    out = json.dumps([acc, roster.suites(site, twin, FLOOR), roster.app_directory(site, twin)])
    for n in names + ["555-0100"]:
        assert n not in out
    for r in acc["suites"]:
        for name in (r["tenant"] or "").split("; "):
            assert not name or name.endswith("(name withheld)")
    for d in roster.app_directory(site, twin):
        assert "(name withheld)" not in d["label"]           # the pick-list shows the kind of business, not a name
