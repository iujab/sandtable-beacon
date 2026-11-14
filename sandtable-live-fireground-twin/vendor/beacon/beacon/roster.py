"""The building record against the phones still inside (the CoStar tie-in).

The record (CoStar in live mode; public open data in fixture mode) says which tenant
occupies which suite, what kind of business it is, how large it is and which suites are
empty. The twin says which room is which suite. Beacon says which phones are inside,
how many people they report and where they are. Joining the three gives the command
agent an accountability table per suite, search priorities, and where to throw the
next puck.

Honesty rules of this module:

* "Expected people" is an ESTIMATE and always carries its basis. When the record has no
  head count, the upper bound is the building-code occupant load (floor area divided by
  the load factor for that use), which is a design maximum, not a census. The central
  figure scales it by a stated time-of-day utilisation. Nothing here is a measurement.
* Cold device counts are devices, not people, and include the opt-in phones themselves.
* "Silent" (a puck in range heard nothing) is weak evidence of an empty room, never proof.
* A suite with no tenant on record is "vacant on record"; if devices or people are heard
  there anyway, that is reported as a disagreement with the record to verify.
* No names: the fixture record withholds tenant names; phones are random ids.

Pure functions over plain dicts; no I/O.
"""
from __future__ import annotations

import math
from typing import Any

SQM_TO_SQFT = 10.7639

# Floor area per occupant, square feet (gross), International Building Code Table 1004.5.
# Business areas 150 gross (2018 and later editions; 100 before), outpatient areas 100 gross.
# Sources: https://www.houstonpermittingcenter.org/media/9311/download (2021 change sheet:
# business areas 100 -> 150) and the Washington State adoption of the table,
# https://lawfilesext.leg.wa.gov/law/WACArchive/2023/htm/WAC%20%2051%20%20TITLE/WAC%20%2051%20-%2050%20%20CHAPTER/WAC%20%2051%20-%2050%20-1004.htm
# (outpatient areas 100 gross). These are egress-design maxima.
LOAD_FACTORS_SF = {
    "outpatient": {"sf_per_person": 100.0, "label": "outpatient load factor"},
    "business": {"sf_per_person": 150.0, "label": "business load factor"},
}

# Share of the code occupant load assumed present. ASSUMPTIONS, stated in every basis text.
UTILISATION_OPEN = 0.5        # open hours: half the design maximum is the central estimate
UTILISATION_OPEN_LOW = 0.25   # low end of the band
UTILISATION_CLOSED = 0.05     # outside open hours: cleaners, late staff
OPEN_HOURS = (8.0, 18.0)      # local time


def _use_class(naics: str | None, industry: str | None) -> str:
    """Health-care practices (NAICS 621, 622, 623) are outpatient areas; everything else business."""
    code = str(naics or "")
    if code.startswith(("621", "622", "623")):
        return "outpatient"
    text = (industry or "").lower()
    if any(w in text for w in ("physician", "dentist", "hospital", "therap", "clinic", "medical", "health", "podiatr", "optomet")):
        return "outpatient"
    return "business"


def _is_open(now_local_hour: float | None) -> bool | None:
    if now_local_hour is None:
        return None
    return OPEN_HOURS[0] <= float(now_local_hour) < OPEN_HOURS[1]


def _expected(tenants: list[dict], room: dict, now_local_hour: float | None) -> dict:
    """Expected people in one suite, with a band and a basis text."""
    if not tenants:
        return {"people": 0, "low": 0, "high": 0,
                "basis": "no tenant on record for this suite (fixture: no active business registration); estimate"}
    is_open = _is_open(now_local_hour)
    hours = "open hours assumed" if is_open is None else ("open hours" if is_open else "outside open hours")
    util, util_low = (UTILISATION_CLOSED, 0.0) if is_open is False else (UTILISATION_OPEN, UTILISATION_OPEN_LOW)

    # 1. The record's own head count, when it has one (live CoStar tenant records can carry it).
    heads = [t.get("employees") for t in tenants if isinstance(t.get("employees"), (int, float)) and t.get("employees") > 0]
    if heads and len(heads) == len(tenants):
        high = int(math.ceil(sum(heads)))
        people = int(round(high * (util / UTILISATION_OPEN) * 0.8)) if is_open is not False else int(round(high * UTILISATION_CLOSED))
        low = int(math.floor(high * util_low))
        return {"people": people, "low": low, "high": high,
                "basis": f"record head count {high} (staff; visitors not counted), {hours}; estimate"}

    # 2. Area divided by the load factor for the use.
    rec_sf = [t.get("sf") for t in tenants if isinstance(t.get("sf"), (int, float)) and t.get("sf") > 0]
    if rec_sf and len(rec_sf) == len(tenants):
        sf, sf_source = float(sum(rec_sf)), "record area"
    else:
        sf, sf_source = float(room.get("area_m2") or 0.0) * SQM_TO_SQFT, "twin room area"
    classes = [_use_class(t.get("naics"), t.get("industry")) for t in tenants]
    use = "outpatient" if "outpatient" in classes else "business"
    lf = LOAD_FACTORS_SF[use]
    high = int(math.ceil(sf / lf["sf_per_person"])) if sf > 0 else 0
    people = int(round(high * util))
    low = int(math.floor(high * util_low))
    return {"people": people, "low": low, "high": high,
            "basis": (f"{sf:,.0f} sf ({sf_source}) at {lf['sf_per_person']:.0f} sf per person ({lf['label']}, building-code maximum {high}), "
                      f"{int(util * 100)}% present, {hours}; estimate")}


def _floor_index(twin, floor: str) -> int | None:
    for f in twin.data.get("floors", []):
        if f.get("id") == floor:
            return int(f.get("index", 0)) + 1          # the record counts storeys from 1
    return None


def suites(site: dict, twin, floor: str | None = None, now_local_hour: float | None = None) -> list[dict]:
    """One row per suite of the twin's floor, joined to the record's tenants by suite number.

    A suite may be one room of the twin (the fixture: a floor of suites) or several (a scan of one suite: offices,
    a kitchen, a lobby, all carrying the same suite number): rooms are grouped by suite number, the row's `rooms` lists
    them all, `room` is the largest (the representative for routes and doors) and the area is their sum.

    The join is by suite number. The record's storey is used only to choose between tenants when the same suite number
    exists on several floors of the record: the twin's floor index is not trusted to equal the record's storey."""
    floor = floor or twin.exercise_floor
    storey = _floor_index(twin, floor)
    all_by_suite: dict[str, list[dict]] = {}
    for t in site.get("tenants", []):
        all_by_suite.setdefault(str(t.get("suite")), []).append(t)

    def tenants_of(suite: str) -> list[dict]:
        ts = all_by_suite.get(suite, [])
        floors = {t.get("floor") for t in ts}
        if len(floors) <= 1:
            return ts
        on_storey = [t for t in ts if t.get("floor") in (None, storey)]
        return on_storey                    # the number exists on several floors: only the matching storey counts

    available = {str(a.get("suite")) for a in site.get("available_spaces", [])
                 if len({x.get("floor") for x in site.get("available_spaces", []) if str(x.get("suite")) == str(a.get("suite"))}) <= 1
                 or a.get("floor") in (None, storey)}

    groups: dict[str, list[dict]] = {}
    for room in twin.rooms("suite"):
        if room.get("floor") != floor or not room.get("suite"):
            continue
        groups.setdefault(str(room["suite"]), []).append(room)

    rows = []
    for suite, rooms in groups.items():
        rooms = sorted(rooms, key=lambda r: (-float(r.get("area_m2") or 0.0), r["id"]))
        main = rooms[0]
        area = {"area_m2": sum(float(r.get("area_m2") or 0.0) for r in rooms)}
        tenants = tenants_of(suite)
        vacant = not tenants or suite in available
        names = [t.get("name") or t.get("industry") or "tenant" for t in tenants]
        hazards = list(dict.fromkeys(t["life_hazard"] for t in tenants if t.get("life_hazard")))
        hazmat = list(dict.fromkeys(t["hazmat"] for t in tenants if t.get("hazmat")))
        sfs = [t.get("sf") for t in tenants if isinstance(t.get("sf"), (int, float))]
        rows.append({
            "suite": suite, "room": main["id"], "rooms": [r["id"] for r in rooms],
            "room_label": (main.get("label") or f"Suite {suite}") if len(rooms) == 1 else f"Suite {suite}",
            "tenant": ("; ".join(names) if names else None),
            "tenants": len(tenants),
            "industry": ("; ".join(dict.fromkeys(t.get("industry") or "" for t in tenants)) or None),
            "naics": [t.get("naics") for t in tenants if t.get("naics")],
            "vacant": bool(vacant),
            "life_hazard": ("; ".join(hazards) or None),
            "hazmat": ("; ".join(hazmat) or None),
            "sf": (float(sum(sfs)) if sfs else None),
            "sf_source": ("record" if sfs else "twin room area"),
            "record_source": (tenants[0].get("source") if tenants else None),
            "synthetic": any(bool(t.get("synthetic")) for t in tenants),
            "expected": _expected([] if vacant and not tenants else tenants, area, now_local_hour),
        })
    rows.sort(key=lambda r: r["suite"])
    return rows


def app_directory(site: dict, twin) -> list[dict]:
    """The suite pick-list the occupant app shows at set-up: every suite the record knows, all floors.

    Fixture tenant names are withheld, so the label is the kind of business."""
    seen: dict[tuple, dict] = {}
    for t in site.get("tenants", []):
        key = (t.get("floor"), str(t.get("suite")))
        label = t.get("industry") or t.get("name") or "tenant"
        if key in seen:
            if label not in seen[key]["label"]:
                seen[key]["label"] += f"; {label}"
        else:
            seen[key] = {"suite": str(t.get("suite")), "floor": t.get("floor"), "label": f"Suite {t.get('suite')} · {label}"}
    return sorted(seen.values(), key=lambda d: (d["floor"] or 0, d["suite"]))


def _phone_room(phone: dict, puck_room: dict[str, str | None], focus_room: str | None) -> tuple[str | None, bool]:
    """The room a phone is attributed to, and whether that is a position (True) or only an assumption (False)."""
    if phone.get("room"):
        return phone["room"], True
    ring = phone.get("ring") or {}
    if ring.get("puck") and puck_room.get(ring["puck"]):
        return puck_room[ring["puck"]], False
    for r in phone.get("ranges") or []:
        if puck_room.get(r.get("puck")):
            return puck_room[r["puck"]], False
    return focus_room, False


_MOBILITY_WORDS = ("non-ambulatory", "sedated", "limited mobility", "impaired", "under treatment", "mid-procedure")


def _hazard_weight(text: str | None) -> float:
    """2.0 when the tenant's occupants may be unable to leave unaided, 1.0 otherwise (a rule on the record's hazard text)."""
    t = (text or "").lower()
    return 2.0 if any(w in t for w in _MOBILITY_WORDS) else 1.0


def _walk_m(twin, src_room: str | None, dst_room: str) -> float:
    """Walking distance in the twin between two rooms (for ranking where the next puck goes); large when unknown."""
    if not src_room:
        return 0.0
    try:
        r = twin.route(src_room, dst_room)
        return float(r["length_m"]) if r.get("ok") else 999.0
    except Exception:
        return 999.0


def _door_of(twin, room_id: str, rooms: list[str] | None = None) -> list[float] | None:
    """The door to throw a puck through: for a suite of several rooms, a door that leads out of the suite when there is one."""
    group = set(rooms or [room_id])
    inner = None
    for d in twin.data.get("doors", []):
        dr = [x for x in (d.get("rooms") or []) if x]
        if not group.intersection(dr):
            continue
        at = [float(d["at"][0]), float(d["at"][1])]
        if any(x not in group for x in dr) or len(dr) < 2:
            return at
        inner = inner or at
    return inner


def accountability(site: dict, twin, floor: str | None, phones: list[dict], presence: list[dict], pucks: list[dict],
                   labels: dict | None = None, now_local_hour: float | None = None, focus_room: str | None = None) -> dict:
    """The Accountability object of docs/BEACON_FEED.md section 2.4 (plus `next_puck` and `record_checks`)."""
    floor = floor or twin.exercise_floor
    rows = suites(site, twin, floor, now_local_hour)
    by_room = {rid: r for r in rows for rid in (r.get("rooms") or [r["room"]])}
    by_suite = {r["suite"]: r for r in rows}

    puck_room: dict[str, str | None] = {}
    for p in pucks or []:
        room = p.get("room")
        if not room and p.get("x") is not None:
            try:
                room = twin.locate(float(p["x"]), float(p["y"]), floor).get("room")
            except Exception:
                room = None
        puck_room[p["id"]] = room
    scanned_rooms = {r for r in puck_room.values() if r} | {e.get("room") for e in presence or [] if e.get("room")}

    for r in rows:
        r.update({"app_registered": 0, "safe_outside": 0, "phones_opt_in": 0, "people_reported": 0, "located": 0,
                  "cold_devices": 0, "cold_range": [0, 0], "cannot_move": 0, "_targets": [], "_assumed": 0})

    for ph in phones or []:
        rep = ph.get("report") or {}
        home = by_suite.get(str(rep.get("home_suite"))) if rep.get("home_suite") is not None else None
        if home is not None:
            home["app_registered"] += 1
        if rep.get("safe_outside"):
            if home is not None:
                home["safe_outside"] += max(1, int(rep.get("people") or 1))
            continue
        room, positioned = _phone_room(ph, puck_room, focus_room)
        row = by_room.get(room) if room else None
        if row is None:
            continue
        row["phones_opt_in"] += 1
        row["people_reported"] += max(1, int(rep.get("people") or 1))
        if not positioned:
            row["_assumed"] += 1
        if ph.get("geometry") == "fix":
            row["located"] += 1
        if rep.get("can_move") is False:
            row["cannot_move"] += 1
        row["_targets"].append((0 if ph.get("geometry") == "fix" else 1, 0 if rep.get("can_move") is False else 1, ph.get("id")))
        scanned_rooms.add(room)

    for e in presence or []:
        row = by_room.get(e.get("room"))
        if row is None:
            continue
        cr = e.get("count_range") or [e.get("count", 0), e.get("count", 0)]
        if int(e.get("count") or 0) >= row["cold_devices"]:
            row["cold_devices"] = int(e.get("count") or 0)
            row["cold_range"] = [int(cr[0]), int(cr[1])]

    record_checks = []
    for r in rows:
        scanned = any(rid in scanned_rooms for rid in (r.get("rooms") or [r["room"]]))
        if r["phones_opt_in"] > 0:
            state = "people-reported"
        elif r["cold_devices"] > 0:
            state = "devices-heard"
        elif r["vacant"]:
            state = "vacant-on-record"
        elif scanned:
            state = "silent"
        else:
            state = "unscanned"
        r["state"] = state
        accounted = r["people_reported"] + r["safe_outside"]
        r["unaccounted"] = {"low": max(0, r["expected"]["low"] - accounted), "high": max(0, r["expected"]["high"] - accounted)}

        hazard = bool(r["life_hazard"])
        score, why, action = 0.0, None, None
        if r["cannot_move"] > 0:
            score = 1000 + 10 * r["people_reported"]
            action = "rescue"
            why = f"{r['people_reported']} people reported by {r['phones_opt_in']} phone{'s' if r['phones_opt_in'] != 1 else ''}, {r['cannot_move']} cannot move"
        elif r["phones_opt_in"] > 0:
            score = 500 + 10 * r["people_reported"]
            action = "rescue"
            why = f"{r['people_reported']} people reported by {r['phones_opt_in']} phone{'s' if r['phones_opt_in'] != 1 else ''}"
        elif r["cold_devices"] > 0:
            score = (200 if hazard else 100) + 5 * r["cold_devices"]
            action = "search"
            why = f"about {r['cold_devices']} devices heard with no app (devices, not people)"
        elif state == "unscanned":
            score = min(99.0, r["expected"]["people"] * _hazard_weight(r["life_hazard"]))
            action = "scan" if score > 0 else None
            why = (f"no puck in range yet; the record expects about {r['expected']['people']} people" if score > 0 else None)
        if why and r["phones_opt_in"] > 0:
            if r["located"]:
                why += f", {r['located']} located"
            elif r["_assumed"]:
                why += ", not yet located (range only)"
        if why and hazard and action != "scan":
            why += f"; tenant: {r['life_hazard']}"
        elif why and hazard:
            why += f" ({r['life_hazard']})"
        if r["vacant"] and (r["phones_opt_in"] > 0 or r["cold_devices"] > 0):
            record_checks.append({"suite": r["suite"], "room": r["room"],
                                  "text": f"Suite {r['suite']} is vacant on record but "
                                          + (f"{r['people_reported']} {'person reports' if r['people_reported'] == 1 else 'people report'} from it" if r["phones_opt_in"] else f"about {r['cold_devices']} devices are heard in it")
                                          + ": verify the record"})
        r["_score"], r["_action"], r["why"] = score, action, why

    # Ties among "throw a puck here next" are broken by walking distance from the room already being worked.
    ranked = sorted([r for r in rows if r["_score"] > 0],
                    key=lambda r: (-r["_score"], _walk_m(twin, focus_room, r["room"]) if r["_action"] == "scan" else 0.0, r["suite"]))
    priorities = []
    for i, r in enumerate(ranked, start=1):
        r["priority"] = i
        target = sorted(r["_targets"])[0][2] if r["_targets"] else None
        verb = {"rescue": "Rescue", "search": "Search", "scan": "Throw a puck into"}[r["_action"]]
        priorities.append({"rank": i, "suite": r["suite"], "room": r["room"], "action": r["_action"], "target": target,
                           "text": f"{verb} Suite {r['suite']}: {r['why']}"})
    next_puck = None
    for r in ranked:
        if r["_action"] == "scan":
            next_puck = {"suite": r["suite"], "room": r["room"], "door": _door_of(twin, r["room"], r.get("rooms")),
                         "text": f"Next puck: Suite {r['suite']} ({r['why']})"}
            break
    for r in rows:
        r.setdefault("priority", None)
        for k in ("_targets", "_assumed", "_score", "_action"):
            r.pop(k, None)

    occupied = [r for r in rows if not r["vacant"]]
    totals = {
        "suites": len(rows),
        "suites_vacant_on_record": sum(1 for r in rows if r["vacant"]),
        "expected_people": sum(r["expected"]["people"] for r in rows),
        "expected_high": sum(r["expected"]["high"] for r in rows),
        "app_registered": sum(r["app_registered"] for r in rows),
        "safe_outside": sum(r["safe_outside"] for r in rows),
        "phones_opt_in": sum(r["phones_opt_in"] for r in rows),
        "people_reported": sum(r["people_reported"] for r in rows),
        "located": sum(r["located"] for r in rows),
        "cold_devices": sum(r["cold_devices"] for r in rows),
        "suites_unscanned": sum(1 for r in occupied if r["state"] == "unscanned"),
        "unaccounted_low": sum(r["unaccounted"]["low"] for r in rows),
        "unaccounted_high": sum(r["unaccounted"]["high"] for r in rows),
    }
    costar = (labels or {}).get("costar") or {"mode": "fixture", "real_sponsor_data": False,
                                              "text": "Building record: public open data with named sources; CoStar-only fields are empty. Tenant names withheld. Not CoStar data."}
    text = (f"Floor record: {len(occupied)} occupied suites, about {totals['expected_people']} people expected (estimate, up to {totals['expected_high']}). "
            f"Phones: {totals['phones_opt_in']} inside reporting {totals['people_reported']} people, {totals['located']} located; "
            f"{totals['safe_outside']} reported safe outside. "
            f"{totals['suites_unscanned']} occupied suites have no puck in range.")
    if priorities:
        text += " First: " + priorities[0]["text"] + "."
    return {
        "source": {"costar_mode": costar.get("mode", "fixture"), "real_sponsor_data": bool(costar.get("real_sponsor_data")), "text": costar.get("text", "")},
        "floor": floor,
        "suites": rows,
        "totals": totals,
        "priorities": priorities,
        "next_puck": next_puck,
        "record_checks": record_checks,
        "assumptions": {"utilisation_open": UTILISATION_OPEN, "utilisation_closed": UTILISATION_CLOSED, "open_hours": list(OPEN_HOURS),
                        "load_factors_sf_per_person": {k: v["sf_per_person"] for k, v in LOAD_FACTORS_SF.items()},
                        "note": "expected people are estimates from floor area and building-code load factors unless the record carries a head count"},
        "text": text,
    }
