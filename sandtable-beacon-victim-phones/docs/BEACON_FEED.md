# Beacon feed — v1 (FROZEN Sat Nov 14 2026, 12:05 CST)

What the Beacon module (Sandtable's third sensing mode: victims' phones) publishes, so the Sandtable command board can add a "Phones" layer against it. Additive changes only after the freeze (new optional fields, new message types); nothing here is renamed or removed. Unknown fields and unknown message types must be ignored by clients.

Style follows Sandtable's Scout feeds (`sandtable-live-fireground-twin/docs/SCOUT_CONTRACT.md`): JSON over HTTP, a WebSocket and an SSE stream, GeoJSON and Cursor-on-Target.

## 0. What the physics allows (do not claim more anywhere)

- A phone that installed nothing cannot be ranged over UWB and cannot be identified. Cold, a puck can only hear Bluetooth advertisements: **a count band of devices, a coarse signal-strength zone, no identity**. Devices are not people (watches, earbuds and laptops advertise too; some phones do not).
- An opt-in phone ranged by Bluetooth signal strength gives a **fat ring** (metres wide) around each puck.
- An opt-in phone ranged by UWB gives a **thin ring** per puck. One puck: a ring. Two pucks: two candidate points, mirror images across the line between the pucks. Three pucks not in a line: one point.
- The phone's own direction estimate to a puck (UWB angle of arrival turned into a compass bearing on the phone, sent back over Bluetooth) turns one ring into a point, when the phone is held up and pointed. A phone in a pocket gives no bearing.
- The twin prunes: nobody is outside the building's outline or inside a wall, and a strong line-of-sight link says "same room as that puck".
- **The feed never carries `x`, `y` for a phone unless `geometry` is `"fix"`.** A ring is published as a ring, two candidates as two candidates.
- Everything is **simulated** until the owner's hardware is in the loop; every object and every feature carries `simulated`.

## 1. Server, frames, units

| Thing | Value |
|---|---|
| Beacon server | `http://HOST:1885` (`HOST`/`PORT` from env; default `127.0.0.1:1885`). CORS is open (`*`) so the Sandtable board on 1810 can read it from the browser |
| Beacon's own uwb-kit hub (only when real pucks are attached) | port `1985` |
| Frame | the twin's building frame (`bldg`): metres, x and y in the floor plane, z up; every position has a `floor` id (`"f3"`). Same twin as Sandtable (`fixtures/building/twin.json`, copied) |
| `geo` | `{"lat", "lon"}` WGS84 through the twin's georeference, exactly as Sandtable computes it |
| Angles | degrees, counter-clockwise from the +x axis of the building frame, 0 to 360 |
| Time | `t` Unix seconds (float); ages in seconds |
| Ids | pucks `P1..P5` (the same pucks as Scout); opt-in phones `B1, B2, ...`; cold presence groups `C1, C2, ...` |

## 2. The Beacons object (`sandtable.beacons/1`)

`GET /api/beacons` returns the latest one; the streams push it. A full example is in `fixtures/beacon/sample_beacons.json` (hand-made sample, marked `"sample": true`; it is the schema by example).

```json
{
 "type": "beacons", "schema": "sandtable.beacons/1",
 "seq": 412, "t": 1794679200.5,
 "simulated": true,
 "source": "sim",
 "floor": "f3", "room": "r310", "room_label": "Suite 310",
 "rungs": {"cold": true, "ble": true, "uwb": true, "bearing": true},
 "pucks": [Puck], "phones": [Phone], "presence": [Presence],
 "accountability": Accountability,
 "limits": ["..."],
 "privacy": {"opt_in_only": true, "cold_identities_kept": false, "retention_s": 900, "text": "..."}
}
```

- `source`: `"sim"` | `"pucks"` (real boards through the kit hub) | `"replay"`. `simulated` is true for `sim` and for replays of simulated recordings: show the SIM badge.
- `room` / `room_label`: the room the pucks were thrown into (null before any puck exists).
- `rungs`: what the current source can deliver (section 6). The board should grey out what is false.

### 2.1 `Puck`

```json
{"id": "P1", "x": 7.9, "y": 6.7, "floor": "f3", "sigma_m": 0.08, "room": "r310",
 "radios": ["ble", "uwb"], "geo": {"lat": 34.2035, "lon": -118.6297}, "simulated": true}
```

Puck positions are an input, not something Beacon measures: in Sandtable they come from Scout's self-location and registration (`scan.pucks[]`); push them with `POST /api/pucks` (section 4).

### 2.2 `Phone` — one opt-in phone (victim-shaped: the fields it shares with Scout's `victims[]` have the same names and meanings)

```json
{
 "id": "B1",
 "eid": "a3f19c02",
 "opt_in": true,
 "simulated": true,
 "method": "uwb-twr",
 "geometry": "fix",

 "x": 2.4, "y": 2.1, "floor": "f3", "floor_source": "barometer",
 "sigma_m": 0.3, "cov": [0.09, 0.01, 0.08],
 "confidence": 0.86,
 "room": "r310", "room_label": "Suite 310", "room_prob": 0.97, "node": "n045",
 "geo": {"lat": 34.20351, "lon": -118.62981},

 "ring": null,
 "candidates": [],
 "pruned": [{"x": 11.6, "y": 2.1, "reason": "inside the wall between Suite 310 and the corridor"}],
 "zone": null,

 "ranges": [{"puck": "P1", "kind": "uwb", "d_m": 5.87, "sigma_m": 0.10, "rssi": -71.0, "los": 0.9, "age_s": 0.2}],
 "bearing": null,

 "report": {"status": "trapped", "text": "Two of us in the back office, smoke under the door",
            "people": 2, "can_move": false, "battery_pct": 41,
            "floor_reported": 3, "baro_hpa": 1002.31, "still": true, "still_s": 42,
            "platform": "android", "app": "0.1", "age_s": 1.2},
 "track": {"first_seen_s": 34.0, "last_seen_s": 0.2, "updates": 120, "moving": false, "speed": 0.0},
 "text": "Phone B1: 2 people, cannot move. UWB fix in Suite 310, 2.4 m from the west wall and 2.1 m from the south wall, within 0.3 m"
}
```

| Field | Meaning |
|---|---|
| `id` | stable for one opt-in phone during a session (`B1`...). Routable target for the command agent, like Scout's `V1` |
| `eid` | the rotating ephemeral id the app currently broadcasts (8 hex digits). Never a MAC address, never a name. The app links consecutive ids over its encrypted status channel, so `id` survives a rotation |
| `opt_in` | always true for a `Phone` (cold devices never appear here) |
| `method` | `"uwb-twr"` (UWB two-way ranging), `"uwb-twr+bearing"` (plus the phone's direction estimate), `"ble-rssi"` (Bluetooth signal strength of the SOS beacon), `"ble-presence"` (heard, too weak or too few samples to range) |
| `geometry` | what the measurements support: `"fix"` (one point), `"candidates"` (two or more separate points), `"ring"` (range to one puck, or pucks in a line), `"zone"` (room-level only), `"none"` (heard, nothing spatial) |
| `x`, `y`, `sigma_m`, `cov`, `geo`, `node` | **non-null only when `geometry` is `"fix"`.** `sigma_m` = 1-sigma radius in metres (square root of the larger eigenvalue of `cov`); `cov` = `[sxx, sxy, syy]` in m2 |
| `floor`, `floor_source` | floor id and where it came from: `"barometer"` (phone pressure against the puck's or the command post's), `"self-report"` (typed in the app), `"puck"` (assumed: the floor the pucks are on) |
| `confidence` | 0 to 1: how much of the probability mass is inside the published geometry |
| `room`, `room_label`, `room_prob` | most probable room of the twin and its probability (present for `fix`, `candidates` when all candidates agree, `zone`; otherwise null) |
| `ring` | when `geometry` is `"ring"`: `{"puck": "P1", "cx", "cy", "r_m", "sigma_m", "arcs": [[a0_deg, a1_deg], ...], "kind": "uwb" \| "ble", "geo": {centre}}`. `arcs` are the parts of the circle the twin leaves possible (inside the building, not in a wall, on the declared floor); `[[0, 360]]` when nothing is pruned. A Bluetooth ring has `sigma_m` of metres: draw it as a band |
| `candidates` | when `geometry` is `"candidates"`: `[{"x", "y", "sigma_m", "weight", "room", "room_label", "geo"}]`, weights sum to 1, most probable first |
| `pruned` | candidate points the twin or the bearing removed, each with a `reason`; for drawing a crossed-out ghost. May be empty |
| `zone` | when `geometry` is `"zone"`: `{"room", "room_label", "prob", "band": "near" \| "mid" \| "far", "puck", "r_min_m", "r_max_m", "polygon": [[x, y], ...], "geo": {centroid}}` |
| `ranges` | the latest range per puck: `kind` `"uwb"` or `"ble"`; `d_m`; `sigma_m` (the model's 1-sigma for that link); `rssi` dBm; `los` 0 to 1 (UWB only); `age_s` |
| `bearing` | the phone's direction estimate when it has one: `{"puck": "P1", "from_puck_deg": 212.0, "sigma_deg": 15.0, "source": "phone-aoa+compass", "age_s": 0.6}`: the bearing of the phone as seen from the puck, in the building frame. Null when the phone is not being pointed |
| `report` | what the occupant app said. `status`: `"trapped"` \| `"injured"` \| `"sheltering"` \| `"ok"` \| `"unknown"`; `text` (at most 120 characters, typed or picked); `people` (how many are with this phone); `can_move`; `battery_pct`; `floor_reported` (the storey number typed in the app, or null); `baro_hpa`; `still` and `still_s` (the phone's accelerometer says it has not moved for that long); `platform`: `"ios"` \| `"android"` \| `"web"` \| `"sim"`; `home_suite` (the suite the user picked when setting up the app, from the building record's tenant list, or null); `safe_outside` (true when the user pressed "I am out": the phone then counts as accounted for, not as a victim, and has `geometry: "none"`); `age_s` |
| `track` | tracker bookkeeping: `first_seen_s` and `last_seen_s` ago, `updates`, `moving`, `speed` m/s |
| `text` | one readable line with the same content, built only from whole or half metres and room names (safe for agent prompts) |

A phone not heard for `stale_s` (default 20 s) gets `"stale": true` and keeps its last geometry with growing `sigma_m`; after `retention_s` it is dropped.

Shapes by `geometry`:

```json
{"id": "B2", "method": "uwb-twr", "geometry": "ring", "x": null, "y": null, "sigma_m": null, "cov": null, "geo": null,
 "ring": {"puck": "P1", "cx": 7.9, "cy": 6.7, "r_m": 5.87, "sigma_m": 0.12, "arcs": [[118.0, 247.0]], "kind": "uwb", "geo": {"lat": 0, "lon": 0}}}

{"id": "B2", "method": "uwb-twr", "geometry": "candidates", "x": null, "y": null,
 "candidates": [{"x": 2.4, "y": 2.1, "sigma_m": 0.3, "weight": 0.5, "room": "r310", "room_label": "Suite 310", "geo": {"lat": 0, "lon": 0}},
                {"x": 4.9, "y": 0.9, "sigma_m": 0.3, "weight": 0.5, "room": "r310", "room_label": "Suite 310", "geo": {"lat": 0, "lon": 0}}]}

{"id": "B3", "method": "ble-rssi", "geometry": "zone", "x": null, "y": null,
 "zone": {"room": "r310", "room_label": "Suite 310", "prob": 0.7, "band": "mid", "puck": "P1", "r_min_m": 2.0, "r_max_m": 8.0, "polygon": [[9.5, 0], [9.5, 8.65], [0, 8.65], [0, 0]], "geo": {"lat": 0, "lon": 0}}}
```

### 2.3 `Presence` — cold: devices heard with no app, per puck and room

```json
{"id": "C1", "method": "ble-presence", "opt_in": false, "simulated": true,
 "puck": "P1", "floor": "f3", "room": "r310", "room_label": "Suite 310",
 "count": 3, "count_range": [2, 5],
 "zones": {"near": 2, "mid": 1, "far": 4},
 "window_s": 30, "confidence": 0.5,
 "geo": {"lat": 34.2035, "lon": -118.6298},
 "text": "About 3 devices advertising within about 5 m of puck P1 in Suite 310; 4 more farther away. Devices, not people; no identities kept"}
```

`count` = devices in the `near` and `mid` bands (attributed to the puck's room); `count_range` = the honest band after the known over- and under-count causes; `zones` = devices per signal-strength band (`near` under about 2 m, `mid` 2 to 6 m, `far` beyond or through a wall). Never a position, never an id. `x`, `y` do not exist on a `Presence`.

### 2.4 `Accountability` — the building record against the phones (the CoStar tie-in)

```json
{"source": {"costar_mode": "fixture", "real_sponsor_data": false, "text": "Building record: public open data + synthetic fields. Not CoStar data."},
 "floor": "f3",
 "suites": [
  {"suite": "310", "room": "r310", "room_label": "Suite 310",
   "tenant": "Dentists (name withheld)", "industry": "Offices of dentists", "vacant": false,
   "life_hazard": "patients may be sedated",
   "expected": {"people": 9, "low": 4, "high": 14, "basis": "882 sf at 100 sf per person (outpatient load factor), open hours"},
   "app_registered": 4, "safe_outside": 1,
   "phones_opt_in": 2, "people_reported": 3, "located": 1, "cold_devices": 3, "cold_range": [2, 5],
   "unaccounted": {"low": 0, "high": 10},
   "state": "people-reported",
   "priority": 1,
   "why": "3 people reported by 2 phones, 1 located to 0.3 m; tenant's patients may be sedated"}
 ],
 "totals": {"expected_people": 61, "phones_opt_in": 3, "people_reported": 5, "located": 2, "cold_devices": 9, "suites_unscanned": 7},
 "priorities": [{"rank": 1, "suite": "310", "room": "r310", "target": "B1", "text": "..."}],
 "text": "one readable paragraph for the command agent"}
```

Per suite: `app_registered` = app users whose `home_suite` is this suite; `safe_outside` = of those, how many pressed "I am out"; `phones_opt_in` / `people_reported` / `located` = opt-in phones heard inside the suite, the people they report, and how many have a `fix`; `cold_devices` = devices heard with no app; `unaccounted` = `expected` minus everybody accounted for (inside and reported, or outside), as a band.

`state`: `"people-reported"` (an opt-in phone says so) | `"devices-heard"` (cold devices only) | `"silent"` (a puck is in range and hears nothing: weak evidence of empty, never proof) | `"vacant-on-record"` (the record lists the suite as available) | `"unscanned"` (no puck in range). `expected` always states its `basis`; a number that is an estimate is called an estimate.

## 3. Streams

**WebSocket `GET /ws`** (server to client, one JSON object per text frame) and **SSE `GET /events`** (the same objects, one per `data:` line):

- `{"type": "hello", "module": "sandtable-beacon", "v": 1, "simulated": true, "source": "sim", "rungs": {...}, "labels": {...Sandtable provider labels...}, "noise": {...stated noise model when simulated...}}` first, and again whenever any of it changes.
- the Beacons object (`type: "beacons"`), on connect and then whenever it changes, at most 4 per second.
- `{"type": "beacon_event", "t", "name": "phone_new" | "phone_fix" | "phone_lost" | "status_changed" | "pucks_changed" | "reset", "id": "B1" | null, "text": "one readable line"}` for timelines.
- `{"type": "reset"}`.

A text frame `ping` is answered with `{"type": "pong"}`.

## 4. HTTP

| Method + path | Purpose |
|---|---|
| `GET /healthz` | `{"ok": true, "simulated": bool, "source": "sim", "phones": n, "pucks": n}` |
| `GET /api/beacons` | the latest Beacons object |
| `GET /api/brief` | DISCRETE summary for the command agent (no timestamps, positions rounded to 0.5 m, confidences to 0.1, room ids and labels, the accountability table and priorities, `limits`): prompts built from it replay exactly |
| `GET /api/accountability` | the `accountability` object alone |
| `GET /feed/beacons.geojson` | GeoJSON FeatureCollection, WGS84 (section 5) |
| `GET /feed/cot.xml` | the same as Cursor-on-Target 2.0 events wrapped in `<events>` (section 5) |
| `GET /feed/cot/stream` | SSE, one CoT `<event>` per message as things change |
| `GET /api/bundle` | `{"twin", "site", "labels", "config"}` used by the standalone board |
| `POST /api/pucks` | `{"pucks": [{"id": "P1", "x", "y", "sigma_m"?}], "room": "r310"?, "floor": "f3"?}`: set the puck positions (Sandtable pushes Scout's `scan.pucks[]` here), or `{"n": 1..5, "room": "r310"?, "seed"?}` to throw simulated pucks |
| `POST /api/reset` | clear phones, presence and pucks |

Simulator only (409 when the source is real pucks):

| Method + path | Purpose |
|---|---|
| `POST /api/sim/phone` | `{"key": "any string", "x", "y", "floor"?, "opt_in": true, "uwb": true, "pointing": false, "report": {...}}` create or move a simulated phone (the "simulated phone" page uses this); `{"key", "remove": true}` deletes it |
| `POST /api/sim/scenario` | `{"name": "suite-310"}`: load a scripted scene |
| `GET /api/sim/phone/{key}` | what the simulated phone page shows back: `{"key", "truth": {"x", "y", "floor"}, "phone": Phone or null, "ack": {"heard_by": ["P1"], "geometry": "ring", "sigma_m": null, "text": "..."}}` (on real hardware the puck sends the same `ack` to the app over Bluetooth) |
| `POST /api/sim/story` | `{"action": "start" \| "stop", "script": "suite-310"}`: the scripted 30-second walk-through (pucks land one by one, the ring collapses to a fix) |
| `GET /api/sim/truth` | ground truth, for evaluation scripts only, never for a board |

Real pucks: the server reads the kit hub's stream (`UWBKIT_URL`, default `ws://127.0.0.1:1985/stream`) and takes the puck firmware's `event` messages (`ble_cold`, `sos`, `uwb_phone`; `hardware/PROTOCOL.md`). `POST /api/ingest` accepts the same observations as JSON for gateways that are not the kit hub.

## 5. GeoJSON and Cursor-on-Target

`/feed/beacons.geojson`: `{"type": "FeatureCollection", "schema": "sandtable.beacons/1", "simulated": true, "t": ..., "features": [...]}`. Every feature has `properties.kind`, `properties.id`, `properties.simulated`, `properties.floor`, `properties.text`.

| `properties.kind` | Geometry | Extra properties |
|---|---|---|
| `puck` | Point | `radios` |
| `phone` (only `geometry: "fix"`) | Point | `method`, `sigma_m`, `confidence`, `room`, `opt_in`, `status`, `people`, `battery_pct`, `still` |
| `phone-ring` | MultiLineString (the arcs the twin leaves possible, 5-degree steps) | `method`, `puck`, `r_m`, `sigma_m`, plus the report fields |
| `phone-candidate` | Point, one feature per candidate | `candidate`: 1-based rank, `of`, `weight`, `sigma_m`, plus the report fields |
| `phone-zone` | Polygon (the room) | `method`, `band`, `prob`, plus the report fields |
| `presence` | Polygon (the room) | `count`, `count_range`, `zones`, `opt_in: false` |

`/feed/cot.xml`: one `<event version="2.0">` per item with `uid` (`sandtable.beacon.<id>`), `type`, `time`, `start`, `stale`, `how`, `<point lat lon hae ce le>`, `<detail><contact callsign="..."/><remarks>...</remarks></detail>`. `how="m-s"` marks simulated positions (as in Scout); real ones use `m-f`.

| Item | `type` | `point` and `ce` |
|---|---|---|
| puck | `a-f-G-E-S` | puck position, `ce` = `sigma_m` |
| phone, `fix` | `a-u-G` | the fix, `ce` = 2 x `sigma_m` |
| phone, `ring` | `a-u-G` | **the puck's position with `ce` = ring radius + 2 x `sigma_m`** (a circular error that honestly contains the ring); remarks say "range only" |
| phone, `candidates` | `a-u-G`, one event per candidate, uid `sandtable.beacon.B2.c1` | candidate point; remarks "candidate 1 of 2" |
| phone, `zone`; presence | `b-m-p-s-m` | room centroid, `ce` = radius of the room's bounding circle; remarks carry the count or the band |

## 6. The capability ladder (`rungs`, and `method` on each phone)

| Rung | `method` | Needs on the victim's phone | Needs on the puck | Gives |
|---|---|---|---|---|
| a. Cold presence | `ble-presence` (in `presence[]`) | nothing | any ESP32 radio (Bluetooth scan) | a device count band and a signal-strength zone per puck; no identity |
| b. SOS beacon over Bluetooth | `ble-rssi` | the opt-in occupant app (or its web page) | any ESP32 radio | status, people, battery, floor; a fat ring per puck, a zone or a coarse fix from several |
| c. UWB precise ranging | `uwb-twr`, `uwb-twr+bearing` | the app, on a phone with a UWB chip | a UWB radio that speaks the phone's protocol (see `docs/PHONE_FEASIBILITY.md` for which boards can) | thin rings; candidates from two pucks; a fix from three, or from one plus the phone's bearing |

## 7. Python (for vendoring into Sandtable as `sandtable/beacon/`)

```python
from beacon.session import BeaconSession
s = BeaconSession(twin_dict, site_dict, labels=None, emit=None)   # emit(msg: dict) -> None is called with every stream message
s.set_pucks([{"id": "P1", "x": 7.9, "y": 6.7}], room="r310", floor="f3")
s.ingest(obs: dict)                 # one observation from a puck (sim or real), section 4 of docs/CONTRACTS.md
s.step(now: float) -> dict          # run the locator and tracker; returns the Beacons object (also emitted when changed)
s.beacons() -> dict                 # the latest Beacons object
s.brief() -> dict                   # discrete summary for agent prompts
s.reset()

from beacon import feeds
feeds.beacons_to_geojson(beacons: dict) -> dict
feeds.beacons_to_cot(beacons: dict, now: float) -> str

from beacon.server import create_app, router    # FastAPI app for port 1885; `router` is an APIRouter Sandtable can mount under a prefix
```

---

# v1 additions (12:20 CST, after the owner confirmed the hardware; additive only: nothing above was renamed or removed)

The owner's boards are **Qorvo DWM3001CDK** (nRF52833 + DW3110, Bluetooth on board), the victims' phones are an **iPhone 14 (U1)** and an **iPhone 15 (U2)**. UWB ranging to the iPhones through Apple's Nearby Interaction accessory protocol, on Qorvo's stock firmware, is now the headline rung. The Android occupant app and the ESP32 puck firmware are dropped. Section 6's rung c therefore reads: needs the iOS occupant app on an iPhone 11 or later, and a DWM3001CDK running Qorvo's stock Nearby Interaction firmware.

## A1. A third source: `live`

`source` is `"sim"` | `"live"` | `"replay"` | `"fake"`. `live` = real phones reporting through `POST /api/ingest`; then `simulated` is false and the SIM badge goes away by itself. `"pucks"` (the kit hub) is not used with these boards.

## A2. How a real range reaches the server tonight

With the stock firmware the range is measured in a Nearby Interaction session between the iPhone and each puck, and the iOS app reads it (`NINearbyObject.distance`, and the direction when the phone has one). **The app is the reporter**: it posts batches to the Beacon server over Wi-Fi.

```json
POST /api/ingest
{"device": {"eid": "a3f19c02", "platform": "ios", "model": "iPhone 14", "chip": "U1", "app": "0.1"},
 "obs": [
  {"kind": "uwb",     "t": 1794679200.5, "accessory": "DWM3001CDK (1A2B3C4D)", "d_m": 2.31},
  {"kind": "sos",     "t": 1794679200.5, "accessory": "DWM3001CDK (1A2B3C4D)", "rssi": -61.0},
  {"kind": "bearing", "t": 1794679200.5, "accessory": "DWM3001CDK (1A2B3C4D)", "to_puck_compass_deg": 32.0, "sigma_deg": 20.0, "source": "ni-direction+compass"},
  {"kind": "report",  "t": 1794679200.5, "prev_eid": null, "report": {"status": "trapped", "people": 2, "can_move": false, "battery_pct": 41, "floor_reported": 3, "baro_hpa": 1002.31, "still": true, "still_s": 42, "home_suite": "310", "safe_outside": false, "text": "..."}}
 ]}
-> {"ok": true, "accepted": 4, "unknown_accessories": ["DWM3001CDK (9F8E7D6C)"], "ack": {"heard_by": ["P1", "P2"], "geometry": "candidates", "sigma_m": null, "text": "2 rescue beacons hear you"}}
```

- `accessory` = the Bluetooth name of the puck as the phone sees it; the server maps it to a puck id. An observation may carry `puck` (an id) instead. `kind: "sos"` with an `accessory` is the Bluetooth signal strength of that puck measured on the phone (the link is symmetric enough for a zone).
- `bearing.source`: `"ni-direction+compass"` (U1 phones: Nearby Interaction's direction vector rotated by the phone's attitude) or `"camera-assist+compass"` (U2 phones and anything else: the camera-assisted horizontal angle).
- The response's `ack` is what the app shows the occupant.
- The same route accepts a single observation object or a list (puck hosts use it too).

## A3. Pucks that are heard before they are placed

`POST /api/pucks` entries may carry `"accessory": "DWM3001CDK (1A2B3C4D)"`. An accessory name that arrives in an ingest batch and is not mapped yet appears in a new top-level list, never in `pucks[]`:

```json
"unplaced_pucks": [{"accessory": "DWM3001CDK (9F8E7D6C)", "suggested_id": "P3", "heard_by": 1, "last_seen_s": 0.4}]
```

The board lets the operator click the plan to place it (`POST /api/pucks {"pucks": [{"id": "P3", "x", "y", "accessory": "..."}], "merge": true}`; `merge: true` adds or moves one puck without replacing the others). `Puck` gains optional `accessory` and `host` (`"uno-q"` | `"battery"` | `"android"` | null).

## A4. Exercise scale

`GET /api/rig` and `POST /api/rig {"scale": 3.0}`: building metres per metre of the physical demo area (1.0 = the real room, the default). In `live` mode every UWB range is multiplied by it before the locator runs, exactly like Sandtable's exercise rig. `hello` and the Beacons object carry `"rig": {"scale": 3.0}`; a board must show "exercise scale 1 : 3" whenever it is not 1.

## A5. Report and phone extras

`report.model` (`"iPhone 14"`), `report.chip` (`"U1"` | `"U2"` | null), `report.network` (`"wifi"` | `"cell"` | null). `Phone.two_board` is reserved for pucks that carry two boards (see `docs/PHONE_FEASIBILITY.md`); clients ignore it.

## A6. Leaving, and accessory keys (12:30 CST)

- An ingest batch may contain `{"kind": "bye", "t": ...}`: the occupant pressed "Stop sharing". The server drops that phone at once (a `beacon_event` `phone_lost` with text "stopped sharing"), and keeps nothing about it.
- The iOS app reports each puck as `"<advertised Bluetooth name>#<4 hex digits>"` (the digits are the last two bytes of the board's accessory configuration data, which follow its UWB short address, so the key is the same on every phone). Treat `accessory` as an opaque string.

## A7. Puck hosts (12:35 CST)

A puck host is a small computer that powers a puck and may read it (the UNO Q; `python -m beacon.host`). It talks to the same route, `POST /api/ingest`, with a list of observations:

```json
[{"kind": "host", "t": 1794679200.5, "puck": "P1", "host": "uno-q", "uptime_s": 312, "rtt": true, "cold_scan": false, "version": "0.1"},
 {"kind": "uwb_puck", "t": 1794679200.5, "puck": "P1", "d_m": 2.29},
 {"kind": "cold", "t": 1794679200.5, "puck": "P1", "window_s": 10.0, "near": 2, "mid": 1, "far": 4}]
```

- `host`: a heartbeat every 5 s. The puck's `host` field becomes that name and it gains `host_seen_s`; a host silent for 20 s is dropped from the puck.
- `uwb_puck`: a range read on the PUCK side (the stock firmware's debug log), with no phone id. The session attributes it to the phone that last reported a range to that puck within 30 s; when that phone has gone quiet (screen off), these keep its ring or fix alive, and the phone's `ranges[]` entry gets `"reporter": "puck"`. Experimental: the log format of the stock firmware is not confirmed.
- `cold`: section 2 of `docs/CONTRACTS.md`; counts only.

## A8. Two pucks, one answer; stated evidence (12:50 CST: the owner chose a two-puck live demo)

- Two pucks leave two mirror candidates. The twin deletes one by itself only when it is impossible (outside the building, inside a wall). A candidate that is possible but contradicts something that was said is dismissed **visibly** (`beacon/prune.py`): it moves to `pruned` with `"by": "occupant-statement"` (the app's "Which suite are you in?") or `"by": "search-room"` (command's decision), and the phone gains `"assumptions": [{"kind", "room", "text"}]`, which a board must show next to the position. When nothing decides, `geometry` stays `"candidates"` and `text` says so plainly ("cannot choose between two spots").
- `report.home_suite` is the suite the occupant says they are in now.
- `GET /api/search_room` and `POST /api/search_room {"room": "r310" | null}`: command's "we are searching this room" assumption (default null = off). The Beacons object carries `"search_room": {"room", "room_label"} | null`.
- `GET /api/layout?room=r310&n=2` returns the recommended puck layout for a room: `{"pucks": [{"id", "x", "y"}], "wall": "...", "why": "...", "mirror_falls": "..."}` (drawn as ghost markers: "place the pucks here").
- `GET /api/app_link.svg?url=<server base url>` returns a QR code (SVG) of `sandtablebeacon://server?url=...`; scanning it with the iPhone's camera opens the Beacon app and fills in the server address.
- The stock puck firmware serves one phone at a time. A phone that takes turns on a board reports each puck only every 10 to 20 s; the session keeps a still phone's last range for 25 s.

## A9. Ingest details (13:00 CST)

- The iOS app re-sends each board's latest `uwb` and `sos` reading, with its original `t`, in every batch for up to 25 s (boards are shared by taking turns). The server counts a reading once: an observation whose (`eid`, `kind`, `accessory`) was already received with the same or a later `t` is skipped and counted in the response's `"skipped"`.
- An observation carrying `"dry_run": true` (a puck host started with `--dry-run`) is skipped.
- Cold bands are signal-strength thresholds, not distances: `near` is stronger than -60 dBm (within about 1 m in the open with the stated model), `mid` is -60 to -75 dBm (to about 3.5 m), `far` is weaker (farther, or through a wall or a body). The "2 m / 6 m" wording in section 2.3 was written before the path-loss model was fixed from sources.
