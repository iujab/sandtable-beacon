# Beacon — internal contracts (lead, Sat Nov 14 2026, 12:10 CST)

The only coordination between the builders. You cannot see each other's work while you build, so code exactly to this. Additive changes are fine. If something here is wrong or impossible, make the smallest compatible choice and say so in your report.

Read first: the bench `../CLAUDE.md` (all of it), then `docs/BEACON_FEED.md` (frozen: what this module publishes; every shape named below that is not defined here is defined there).

## 0. The product in three sentences

Sandtable is an incident-command board for fire crews (sibling project `../sandtable-live-fireground-twin`, read-only: never edit it, never import from it; copy with credit). Beacon is its third sensing mode: the UWB and Bluetooth pucks a crew throws into a room find the phones of the people trapped inside: a cold device count with no app, an opt-in SOS beacon ranged by Bluetooth signal strength, and UWB ranging where the hardware can. A locator fuses rings, zones and bearings with the twin's walls into honest geometry (a ring stays a ring), and the building record's tenant roster is compared with the phones still inside.

There is no hardware on this machine. Everything is simulated and says so. Never scan, range or message any real device from this machine.

## 1. Tree, ownership, environment

```
beacon/                 the importable package (later vendored into Sandtable as sandtable/beacon/)
  twin.py               COPIED from Sandtable's spatial engine (Twin: rooms, locate, walls_crossed, to_geo). Lead-owned. Do not edit.
  config.py             paths, ports, thresholds                                  (W3)
  models.py             noise-model constants and forward models shared by sim and locator   (W1)
  locate.py, track.py   the locator and the per-phone tracker                     (W1)
  sim.py                the simulated world: pucks, phones, cold devices          (W2)
  session.py, feeds.py, server.py, story.py, demo.py, __init__.py                 (W3)
  roster.py, agent.py, llm.py, tools.py    building record, accountability, command-agent hand-off   (W4)
  source_kit.py         real pucks through the kit hub                            (W8)
web/                    board (index.html ...), simulated phone page (phone.html) (W5); web/sos.html (W8, Web Bluetooth)
apps/android/           Kotlin occupant app + Gradle build                        (W6)
apps/ios/               Swift/SwiftUI occupant app + XcodeGen spec (uncompiled)   (W7)
hardware/               firmware/beacon_puck/, build_logs/, PROTOCOL.md, BRINGUP.md   (W8)
fixtures/building/      twin.json, site.json: COPIED from Sandtable (lead). Read them: they are the schema by example. Never hard-code room ids, suite numbers or coordinates
fixtures/beacon/        sample_beacons.json (W3), scenarios/*.json (W2)
scripts/                eval_beacon.py (W2), shots.cjs (W5), stop_demo.sh (W3)
tests/                  test_<area>.py, owned by the builder of that area
results/, figures/      eval_* (W2), ui_* (W5)
docs/                   BEACON_FEED.md, CONTRACTS.md, PHONE_FEASIBILITY.md, research/ (lead)
```

- Python: the shared `.venv` in the project root (3.13; fastapi, uvicorn, websockets, numpy, httpx, pytest, matplotlib are installed). Run with `.venv/bin/python`. Do not create another venv. New dependency: append one line to `requirements.txt` (re-read it first) and run `uv pip install -p .venv/bin/python -r requirements.txt`. Wheels-only, cross-platform packages only (the demo must also run on Windows and Debian aarch64).
- No git commands that write: the lead commits. Edit only the files you own; put requests for others in your report.
- No absolute home paths, user names, host names or tailnet addresses in any file. No CDN at runtime.
- Ports: the lead's demo is 1885 (kit hub 1985). Builders use their own while testing and stop every process they start: W3 1886, W5 1887, W2 1888, W1 1889, W4 1890, W8 1891/1991, W6 1892, W7 none.
- Honesty: never present simulated or sample data as measured. Every screen shows a SIM badge while `simulated` is true. "Devices", not "people", for the cold count. Firmware is "compile-verified" only with a saved `arduino-cli` log. The iOS app is "uncompiled source". Numbers in any document come from files in `results/`.
- Keep tests fast (whole suite under 2 minutes, offline) and `nice` anything CPU-heavy; at most 4 worker processes.

## 2. Observations (what a puck, simulated or real, delivers to the session)

Plain dicts. `t` is Unix seconds. `eid` is the phone's rotating ephemeral id (8 hex digits).

```python
{"kind": "cold",    "t", "puck": "P1", "window_s": 10.0, "near": 2, "mid": 1, "far": 4}
        # cold Bluetooth scan: number of distinct advertising devices per signal-strength band in the window.
        # Bands (models.COLD_BANDS_DBM): near >= -60 dBm, mid -60..-75, far < -75. Counts only: no addresses, no ids, ever.
{"kind": "sos",     "t", "puck": "P1", "eid": "a3f19c02", "rssi": -71.0, "tx_dbm": -59.0, "report": {...}}
        # the opt-in app's SOS advertisement (or its Bluetooth connection) heard by a puck. tx_dbm = the app's calibrated
        # signal strength at 1 m (optional; default models.BLE_TX_1M_DBM). report = the compact status (any subset of the
        # report fields of BEACON_FEED 2.2; optional).
{"kind": "uwb",     "t", "puck": "P1", "eid": "a3f19c02", "d_m": 5.87, "rssi": -78.5, "fp": -80.1, "los": 0.9}
        # one UWB two-way-ranging result between a puck and an opt-in phone. rssi/fp/los optional (los: 0..1, as in the kit).
{"kind": "bearing", "t", "eid": "a3f19c02", "puck": "P1", "to_puck_compass_deg": 32.0, "sigma_deg": 15.0, "source": "phone-aoa+compass"}
        # computed ON THE PHONE and sent back over Bluetooth: the compass azimuth (clockwise from true north) of the
        # direction from the phone to the puck = the phone's UWB angle of arrival combined with its compass heading.
        # The session converts it into the building frame with the twin's georeference (models.compass_to_bldg_deg).
{"kind": "report",  "t", "eid": "a3f19c02", "prev_eid": "09be11d4" | None, "report": {...full report...}}
        # the full status over the encrypted connection; prev_eid links a rotated id to the same phone.
```

## 3. Python interfaces

All plain dicts and lists, JSON-serialisable. Raise `ValueError` for bad references; never return fake data on failure.

### 3.1 W1 — `beacon/models.py`, `beacon/locate.py`, `beacon/track.py`, `tests/test_locate.py`, `tests/test_track.py`

```python
from beacon import models
models.DEFAULT_NOISE -> dict     # THE stated noise model (also sent in the server's hello and quoted next to simulated numbers):
#   {"uwb": {"sigma_los_m": 0.10, "wall_bias_m": 0.25, "wall_extra_sigma_m": 0.20, "body_bias_m": 0.15, "outlier_prob": 0.03,
#            "outlier_m": 1.5, "dropout_prob": 0.05, "max_range_m": 25.0, "max_walls": 2},
#    "ble": {"tx_1m_dbm": -59.0, "tx_spread_db": 4.0, "n_los": 2.0, "wall_loss_db": 4.0, "shadow_sigma_db": 5.0,
#            "fast_fading_sigma_db": 3.0, "body_loss_db": 8.0, "sensitivity_dbm": -95.0},
#    "bearing": {"sigma_deg": 15.0, "fov_deg": 120.0, "available_only_when_pointing": True},
#    "puck": {"position_sigma_m": 0.08}}
#   Starting values; the lead replaces them with cited numbers from docs/research/ when the research lands. Read every
#   constant from this dict (never a literal elsewhere) so one edit changes sim and locator together.
models.uwb_expected(d_true_m, n_walls, noise) -> (mean_m, sigma_m)          # forward model: bias and spread of a UWB range
models.ble_expected_rssi(d_true_m, n_walls, noise, tx_dbm=None) -> (mean_dbm, sigma_db)
models.ble_distance(rssi_dbm, noise, tx_dbm=None) -> (d_m, d_lo_m, d_hi_m)  # point estimate and the 1-sigma band (log-normal: asymmetric)
models.cold_band(rssi_dbm) -> "near" | "mid" | "far"
models.compass_to_bldg_deg(to_puck_compass_deg, georef_bearing_deg) -> float   # -> BEACON_FEED "from_puck_deg" (bearing of the phone seen from the puck, building frame, CCW from +x)

from beacon import locate
ctx = locate.Context(twin, floor=None, cell_m=0.25, use_twin=True)
#   Precomputes the grid of the floor. use_twin=True: only cells inside the floor outline and not inside a wall are possible,
#   each cell knows its room, and walls between a puck and a cell are counted (cached per puck position) for the forward models.
#   use_twin=False (for the evaluation's "no twin" arm): a free-space disc of radius max range around the pucks, no rooms, no walls.
locate.estimate(ctx, pucks: list[dict], ranges: list[dict], bearing: dict | None = None, prior: dict | None = None, noise: dict | None = None) -> dict
#   pucks:  [{"id", "x", "y", "sigma_m"?}]
#   ranges: the smoothed latest measurement per puck for ONE phone:
#           {"puck", "kind": "uwb", "d_m", "los"?, "n"?: samples behind it}  or  {"puck", "kind": "ble", "rssi", "tx_dbm"?, "n"?}
#   bearing: {"puck", "from_puck_deg", "sigma_deg"} or None
#   prior:  {"room": "r310", "weight": 0.7} (the phone's user says they are in that suite) or None
#   Returns the spatial part of a BEACON_FEED Phone, exactly those names:
#     {"method", "geometry", "x", "y", "sigma_m", "cov", "confidence", "room", "room_label", "room_prob", "node",
#      "ring", "candidates", "pruned", "zone",
#      "hpd_area_m2": area of the 90% highest-density region (the universal uncertainty number for the evaluation),
#      "debug": {"n_components", ...}}
#   plus locate.posterior_png / a coarse "heat" dict {"origin", "cell_m", "nx", "ny", "values"} via estimate(..., want_heat=True) for the board.
locate.geometry_rules -> dict    # the thresholds, in one place, documented:
#   A grid Bayes posterior over the cells. Take the 90% highest-density region and split it into connected components.
#   "fix": one component, and the 1-sigma of its long axis <= FIX_SIGMA_MAX_M (1.0 m for UWB, 2.5 m for Bluetooth-only).
#   "candidates": 2..4 compact components (each passes the fix test); weights = their probability mass.
#   "ring": the region is an arc or band around one puck (ranging to one puck, or pucks in a line): r_m, sigma_m,
#           arcs = the angular intervals the posterior leaves possible. A Bluetooth-only ring has a sigma of metres.
#   "zone": Bluetooth-only and too wide for a ring to mean anything (band + most probable room).
#   "none": heard but nothing usable.
#   NEVER a fix when the posterior is an arc: x and y are null unless geometry == "fix".
#   "pruned": run the same estimate without the twin (cheaply: reuse the range-only solution); candidate points that the
#   twin removed (outside the outline, inside a wall, wrong room for a line-of-sight link) are listed with a reason.

from beacon.track import PhoneTrack
tr = PhoneTrack(eid)                       # one per opt-in phone
tr.add(obs)                                # sos | uwb | bearing | report observations of this phone
tr.inputs(now) -> (ranges, bearing)        # smoothed: median of the last UWB ranges per puck (outliers die), averaged RSSI per puck,
                                           # stale links dropped (LINK_STALE_S = 5 s), bearing only while fresh (3 s)
tr.estimate(ctx, pucks, now, noise=None) -> dict   # locate.estimate + hysteresis (a fix is not demoted by one noisy update; a geometry
                                           # must hold 2 consecutive estimates before it replaces the published one) + "track" {...}
tr.report -> dict (latest merged report);  tr.last_seen;  tr.moving
```

W1: `models.py` with the constants and forward models must exist within your first 10 minutes (W2 imports it); a first `locate.estimate` within 30.

### 3.2 W2 — `beacon/sim.py`, `fixtures/beacon/scenarios/*.json`, `scripts/eval_beacon.py`, `results/eval_*.json`, `figures/eval_*.png`, `tests/test_sim.py`

```python
from beacon.sim import World
w = World(twin, seed=310, noise=None)                 # noise defaults to models.DEFAULT_NOISE; deterministic for a seed
w.throw_pucks(room_id, n=3, seed=None) -> [{"id", "x", "y", "sigma_m"}]    # scattered within a few metres of the room's door, inside the room (like Scout)
w.set_pucks(pucks: list[dict])                        # or place them explicitly; w.pucks -> list (what the pucks believe: truth + position_sigma)
w.upsert_phone(key, x, y, floor=None, opt_in=True, uwb=True, pointing=False, report=None) -> dict
        # opt_in False: a phone with no app: it only contributes to cold counts.
        # uwb False: an opt-in phone with no UWB chip: Bluetooth only.
        # pointing True: the user is holding the phone up toward a puck: bearing observations are generated (within the field of view).
w.remove_phone(key);  w.add_cold_devices(room_id, n);  w.clear()
w.load_scenario(dict | path)                          # fixtures/beacon/scenarios/suite-310.json: pucks, phones, cold devices, reports
w.step(now: float) -> list[obs]                       # everything the pucks observed since the previous step (section 2 shapes):
        # uwb at about 5 Hz per puck-phone pair in range, sos at about 2 Hz per puck in Bluetooth range, bearing at 2 Hz while pointing,
        # report on change and every 5 s, cold every 5 s per puck. eids rotate every EID_ROTATE_S (600 s) with prev_eid in the next report.
w.truth() -> {"pucks": [...], "phones": [{"key", "eid", "x", "y", "floor", "opt_in", "uwb", "room"}], "cold": {...}}   # evaluation only
w.noise -> dict
```

Noise comes from `models` forward models with the twin's walls (`Twin.walls_crossed`) between puck and phone: UWB range = true + Gaussian + positive bias per wall + rare positive outliers + dropouts, nothing beyond `max_walls` or `max_range_m`; Bluetooth RSSI = log-distance path loss + per-wall loss + slow shadowing (fixed per puck-phone pair while the phone stays put) + fast fading per sample + a per-phone transmit-power offset; a cold device is a phone-class or wearable-class advertiser with its own address-rotation overcount (state the model).

`scripts/eval_beacon.py` (`.venv/bin/python scripts/eval_beacon.py [--quick]`, under 2 minutes with `nice`, at most 4 processes): Monte Carlo over phone positions in the fire room and its neighbours and over puck throws. Arms: method (`ble-rssi`, `uwb-twr`, `uwb-twr+bearing`) x pucks (1..5) x twin pruning (on, off). Report per arm: the share of each `geometry`; error of the point when a fix is published (median, p90); `hpd_area_m2` (median) for every geometry; **honesty metrics**: how often the truth lies inside the published geometry (fix within 3 sigma, on the ring within 3 sigma, among the candidates), and the wrong-fix rate; room-level accuracy; cold count error (count band coverage). Also a model-mismatch arm (the simulated world's noise 1.5x what the locator assumes). Outputs `results/eval_beacon.json` (with the noise model, seeds and trial counts inside) and figures `figures/eval_error_vs_pucks.png`, `eval_area_vs_pucks.png`, `eval_bearing.png`, `eval_twin_pruning.png`, `eval_geometry_share.png`, plus `figures/eval_storyboard.png` (four panels drawn from real locator output: one puck ring, two pucks two candidates, the twin prunes, three pucks fix). All titled "simulated".

### 3.3 W3 — `beacon/config.py`, `session.py`, `feeds.py`, `server.py`, `story.py`, `demo.py`, `__init__.py`, `run_demo.sh`, `run_tests.sh`, `scripts/stop_demo.sh`, `fixtures/beacon/sample_beacons.json`, `tests/test_feeds.py`, `tests/test_session.py`, `tests/test_server.py`

`BeaconSession`, `feeds` and the HTTP and stream surface exactly as in `docs/BEACON_FEED.md` sections 2 to 5 and 7. Additional rules:

- `config.py`: `ROOT`, `FIXTURES`, `TWIN_JSON`, `SITE_JSON`, `WEB_DIR`, `HOST` (env, default `127.0.0.1`), `PORT` (env, default 1885), `HUB_PORT` (PORT + 100), `UWBKIT_URL`, `BEACON_SOURCE` (`sim` | `fake` | `kit`), `STALE_S` 20, `RETENTION_S` 900, `STEP_HZ` 4, `SIM_HZ` 10, `labels()` (the fixture honesty labels, same texts as Sandtable's provider: record = public open data, not CoStar; twin = synthetic interior, not a Matterport scan).
- **Fake engine first** (within your first 20 minutes): `BEACON_SOURCE=fake` plays `fixtures/beacon/sample_beacons.json` through the phases cold -> band -> ring -> candidates -> fix over about 20 s, looping, every message marked `"sample": true`, so the board builder has a live source. Write `sample_beacons.json` first (hand-made, covering every geometry, a presence entry and the accountability object). The fake engine is also the fallback when the real engine fails to import (logged loudly, shown on screen as SAMPLE, never silently).
- Real engine (`sim`): an asyncio task steps `World` at `SIM_HZ`, feeds `session.ingest`, runs `session.step` at `STEP_HZ`. Heavy numpy work goes to a thread. Session keeps one `PhoneTrack` per phone (linking rotated eids through `prev_eid`), a per-puck cold aggregator (`presence[]`: per puck and room, `count` = near + mid, `count_range` from the stated over- and under-count model), calls `beacon.roster.accountability(...)` when `beacon.roster` imports (W4; until then omit `accountability`, never invent it), builds `text` lines from whole or half metres and room names ("2.5 m from the west wall").
- Phones whose report says `safe_outside` are published with `geometry: "none"` and counted as accounted for.
- `server.py`: FastAPI. `create_app()` and an `APIRouter` named `router` so Sandtable can mount the same routes under a prefix. Serves `web/` at `/`, `fixtures/` at `/fixtures/` (read-only), and `dist/` at `/download/` (the Android APK, when it exists). CORS `*`. `GET /api/debug/heat/{id}` returns the locator's coarse heat grid for one phone (board overlay, optional).
- `story.py`: the scripted walk-through for `POST /api/sim/story` (condition-based where possible): reset; puck 1 lands in the fire room (cold count appears); an opt-in phone starts its SOS (Bluetooth band); its UWB ranging starts (thin ring, clipped by the twin); puck 2 lands (two candidates, or one after the twin prunes); the occupant points the phone or puck 3 lands (fix); a second phone reports from the next suite; accountability and priorities settle. About 30 to 40 s. Emits `beacon_event`s with readable text.
- `demo.py`: `python -m beacon.demo [--host H] [--port P] [--tailnet] [--source sim|fake|kit] [--scenario NAME] [--story]`; pure Python, cross-platform; `--tailnet` binds to `tailscale ip -4` (first line) and to 127.0.0.1. `run_demo.sh`: thin wrapper that creates the venv and installs requirements when missing, then runs the launcher; `--tailnet` and `--daemon` (nohup setsid, pid in `.demo.pid`). `scripts/stop_demo.sh`. `run_tests.sh`: `.venv/bin/python -m pytest tests -q`.

### 3.4 W4 — `beacon/roster.py`, `agent.py`, `llm.py`, `tools.py`, `fixtures/llm_cache/`, `tests/test_roster.py`, `tests/test_agent.py`, `docs/COSTAR_TIE_IN.md`

```python
from beacon import roster
roster.suites(site: dict, twin, floor: str) -> [ {suite, room, room_label, tenant, industry, naics, vacant, life_hazard, sf, sf_source, expected{people, low, high, basis}} ]
roster.accountability(site, twin, floor, phones: list[Phone], presence: list[Presence], pucks: list[Puck], labels: dict | None, now_local_hour: float | None = None) -> Accountability   # BEACON_FEED 2.4
roster.app_directory(site, twin) -> [{"suite", "floor", "label"}]      # the suite pick-list the occupant app shows at set-up (served at GET /api/directory)
```

Expected people per suite: the record's own head count when it has one (live CoStar tenant fields), otherwise leased area (the record's `sf`, else the twin's room area, say which) divided by a stated load factor per use, scaled by a stated time-of-day factor; always with `basis` text and a band. Suites with no tenant on record are `vacant-on-record` (fixture: "no active business registration"). Priorities: deterministic ranking with a `why` for each (people reported who cannot move, then people reported, then cold devices in a life-hazard suite, then cold devices, then where to throw the next puck: unscanned suites ranked by expected people and life hazard). The agent hand-off: `tools.py` exposes tool specs + functions (`get_phones`, `suite_accountability`, `next_puck_plan`) in the same prompted-JSON style as Sandtable's agent, and `agent.py` runs one command-agent turn ("phones" situation -> search priorities and radio orders) through `llm.py` (`anthropic` | `claude-cli` | `replay`, default `replay`; record real model output with the Claude CLI into `fixtures/llm_cache/`, never hand-write one; deterministic labelled fallback on a replay miss).

### 3.5 W5 — `web/**` except `web/sos.html`, `scripts/shots.cjs`, `figures/ui_*.png`

- `web/index.html` (+ js, css): the standalone board on 1885. It must look like part of Sandtable: open `../sandtable-live-fireground-twin/web/style.css`, `index.html`, `plan2d.js` and `figures/ui_08_plan_2d.png`, and reuse its visual language (copy what you need, with a credit comment; its fonts are in `web/vendor/fonts`). One screen: the floor plan of the twin (2-D, from `GET /api/bundle`), the fire room in focus, pucks, per-phone geometry exactly as published (thin UWB ring clipped to its `arcs`, Bluetooth band, candidates with weights, pruned ghosts crossed out, fix with its sigma), cold presence as a count badge on the room (never dots), a phone card per opt-in phone (status, people, battery, floor, stillness, method, geometry), the accountability table and priorities, the capability ladder (three rungs, what each needs, which are active), a feeds panel (links to the GeoJSON and CoT feeds), a SIM badge and the fixture labels in the footer, and controls (Throw puck, Run walk-through, Reset). Consume the WebSocket. Until the real endpoints exist, develop against `BEACON_SOURCE=fake` or `GET /fixtures/beacon/sample_beacons.json` behind `?sample=1`, marked SAMPLE on screen.
- `web/phone.html`: the simulated phone. Looks like the occupant app (status buttons, how many people, can you move, a note), shows what the real app would show back (`ack`: "2 rescue beacons hear you; they know where you are to within 0.3 m"), and lets the presenter place the phone by dragging it on a small plan of the room and tick "phone has UWB" and "holding the phone up". Uses `POST /api/sim/phone` and `GET /api/sim/phone/{key}`. Works on a phone-sized screen. Marked SIMULATED PHONE.
- `scripts/shots.cjs` (Playwright through `npx playwright`, or the bench's MCP browser) and at least four screenshots in `figures/` 1200 to 1600 px wide.

### 3.6 Changes at 12:20 CST (the owner confirmed the hardware; see `docs/BEACON_FEED.md` "v1 additions")

The boards are Qorvo DWM3001CDK (nRF52833 + DW3110) running Qorvo's STOCK Nearby Interaction firmware; the phones are an iPhone 14 (U1) and an iPhone 15 (U2). Consequences:

- W6 (Android app) and the ESP32 puck firmware are dropped. No `arduino-cli` work. `web/sos.html` is dropped.
- W7 (iOS occupant app): `apps/ios/**`, `docs/IOS_BUILD.md`. Uncompiled here; the owner has a Mac with Xcode.
- W8 (puck host + bring-up): `beacon/host/**` (pure Python for the UNO Q and laptops), `hardware/**`, `tests/test_host.py`.
- `models.DEFAULT_NOISE["uwb"]` gains `"link_bias_sigma_m": 0.05`: a static bias per puck-phone link (antenna delay and orientation) that averaging does not remove. The simulator draws it once per link; the locator must not let the range sigma shrink below it however many samples it averages.
- New source `live` (W3): observations arrive through `POST /api/ingest` from the iOS app (feed additions A1 to A5): accessory-name-to-puck mapping, `unplaced_pucks`, the rig scale, `--lan` (bind 0.0.0.0) in the launcher.
- Board (W5): LIVE badge when `simulated` is false, a tray of unplaced pucks with click-to-place, the exercise-scale control and label; `web/app.html` is about the iOS app only.
- Evaluation (W2): two more experiments the owner asked for, in `results/eval_layouts.json` and `figures/eval_layouts.png`: (1) two boards on ONE thrown device at 0.1, 0.3 and 0.5 m spacing; (2) two separate pucks 2 to 4 m apart; each with and without the phone's bearing and with and without the twin; report bearing error, cross-range error, mirror-ambiguity rate and geometry shares against the ranging error.

### 3.7 Command agent and incident report (W4, added 12:40 CST; `beacon/roster.py` already exists, lead-written)

W4 owns `beacon/llm.py`, `beacon/tools.py`, `beacon/agent.py`, `beacon/report.py`, `beacon/agent_routes.py`, `fixtures/llm_cache/**`, `tests/test_roster.py`, `tests/test_agent.py`, `tests/test_report.py`, `docs/COSTAR_TIE_IN.md`.

```python
from beacon.agent import BeaconAgent
agent = BeaconAgent(session, twin, site, emit)      # emit(msg): the server's broadcaster
await agent.run(reason: str = "manual") -> dict     # one command-agent turn: plan -> tool calls -> actions; returns the AgentPlan; emits events while it runs
agent.busy -> bool;  agent.backend -> "replay" | "claude-cli" | "anthropic" | "reflex-fallback";  agent.last -> AgentPlan | None

from beacon import report
report.build(session, agent_plan: dict | None) -> dict      # the incident report object
report.to_html(report_obj: dict) -> str                     # printable, self-contained, labelled SIMULATED when it is

from beacon.agent_routes import register
register(router, get_session, get_agent)   # adds POST /api/agent/run, GET /api/agent, GET /api/report, GET /report.html
```

Stream messages (same style as Sandtable's command agent, so its board can show them unchanged):

```json
{"type": "agent", "run": "phones#1", "step": 3, "kind": "start|thought|tool_call|tool_result|action|final|error",
 "backend": "replay|claude-cli|anthropic|reflex-fallback", "text": "one readable line", "tool": "suite_accountability", "args": {}, "result": {}, "t": 0}
{"type": "agent_plan", "run": "phones#1", "backend": "replay", "summary": "two or three sentences",
 "orders": [{"to": "RIT", "text": "radio order", "priority": "routine|urgent|emergency", "target": "B1" | null}],
 "priorities": [{"rank": 1, "suite": "310", "target": "B1", "action": "rescue|search|scan", "why": "..."}],
 "next_puck": {"suite": "301", "room": "r301", "why": "..."} | null,
 "record_checks": [{"suite": "309", "text": "..."}],
 "limits_acknowledged": ["devices are not people", "..."]}
```

`GET /api/agent` -> `{"busy": bool, "backend": "...", "plan": AgentPlan | null, "log": [agent messages of the last run]}`. `POST /api/agent/run` -> `{"ok": true}` (409 while busy). The Beacons object gains `"agent": {"busy", "backend", "summary"}` when a plan exists (W3 passes it through if the session has one).

Tools offered to the model (prompted JSON, one object per reply: `{"thought", "tool", "args"}` or `{"thought", "final": {...}}`, as in Sandtable): `get_phones()` (the discrete brief of every opt-in phone: id, room, geometry in words, status, people, can_move, battery band), `suite_accountability(suite?)` (the roster table or one row, with the basis of each estimate), `next_puck_plan()` (unscanned suites ranked, with the door to throw through and the walking distance), `route_to(target)` (the twin's route from the attack stair to a phone id, a suite or a room: length in whole metres and the rooms crossed), `radio(to, text, priority)`, `assign(unit, task, target)`, `file_record_check(suite, text)`. Prompts are built only from discrete facts (`session.brief()`: no timestamps, no raw coordinates), so a recorded real run replays exactly; `fixtures/llm_cache/` holds only real model output recorded through the Claude CLI (`claude -p ... --model sonnet --output-format json < /dev/null`); a replay miss runs a deterministic policy labelled `reflex-fallback`.

## 4. Report at the end, concisely

What exists, what you ran and its outcome, what is unverified, deviations from this contract, requests for other builders.

### 3.8 Puck-to-puck baselines (lead, Sun Nov 15 2026, 02:00 CST; feed contract `docs/BEACON_FEED.md` A12)

```python
from beacon import baseline                       # pure, standard library only
baseline.make_baseline(pair, d_m, *, sigma_m=None, samples=None, source="manual", t) -> dict   # ValueError when invalid
baseline.anchor_point(ax, ay, tx, ty, d_plan_m) -> (x, y)      # ValueError when (tx, ty) is within 1 cm of the anchor
baseline.spacing_check(baseline, puck_a, puck_b, scale=1.0) -> warning dict | None   # 3 sigma + 0.2 m, physical metres
baseline.PLACED_BY == ("hand", "baseline", "layout", "sim");  baseline.PLACED_BY_API == ("hand", "baseline")

s = BeaconSession(...)
s.set_pucks(pucks, room=None, floor=None, merge=False, placed_by="hand")   # default for entries without "placed_by"
s.set_baseline(pair, d_m, *, sigma_m=None, samples=None, source="manual", now=None) -> baseline dict
s.anchor(anchor, x, y, toward={"x", "y"}, other, room=None, floor=None) -> {"pucks", "room", "floor", "baseline", "spacing_plan_m"}
s.baselines() -> list;  s.range_scale() -> float   # the exercise scale in live mode, else 1

from beacon.host.twr import parse_twr_line, BaselineEstimator   # the TWR firmware's RTT lines; robust spacing (stdlib only)
```

Who sets `placed_by`: `POST /api/pucks` entries ("hand" unless they say "baseline"), `/api/pucks/anchor` ("baseline"),
`POST /api/pucks {"n": 2}` ("layout"), other simulated throws, scenarios and the walk-through ("sim"). Baselines and
warnings never enter `brief()` or `limits` (recorded agent runs replay by prompt hash).
