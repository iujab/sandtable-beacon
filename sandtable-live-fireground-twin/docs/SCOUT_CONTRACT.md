# Scout mode — interface contract (frozen by the lead, 11:50 CST)

Scout mode is the new headline of Sandtable. A crew throws a handful of UWB pucks into a room. The pucks range each other, work out their own geometry, register themselves into the building's twin, and run as a multistatic UWB radar: they find an untagged person and their breathing through smoke, produce a coarse echo outline, and flag where the room no longer matches the twin. The scan is handed to the command board, the command agent acts on it, and it is published as GeoJSON and Cursor-on-Target feeds. It sits on top of what already works (pre-plan from three data layers, tagged crews tracked in the twin, mayday exercise, replayed agent).

Read `README.md` and `docs/CONTRACTS.md` for the existing system. This file is the only coordination between the scout builders: you cannot see each other's work while you build, so code exactly to it. Additive changes are fine. If something here is wrong, make the smallest compatible choice and say so in your report.

## 0. What the physics allows (do not claim more anywhere)

- One board is not a radar: the DW3000 is half-duplex and cannot hear echoes of its own frame. The minimum is a pair (one transmits, the other reads the channel impulse response, CIR).
- One pair gives an echo's extra path length only: an ellipse with the two pucks as foci. Locating in 2-D needs several pairs: 3 pucks give 3 ellipses, 5 give 10.
- About 500 MHz of bandwidth: about 0.3 m range resolution (one CIR tap is about 1 ns, 0.3 m of path).
- A breathing or moving person stands out against the static scene. A person who neither moves nor breathes looks like furniture. Static walls give a coarse outline and echo map, **never a LiDAR-grade model**.
- The twin is the map. The pucks add: who is in the room without a tag, whether they move or breathe, and where the room differs from the twin. With no twin the scan falls back to a coarse box.
- Everything is **simulated** until boards exist. The radar code is adapted from lane L08's project `../tagless-uwb-echo-radar` (read-only; copy what you need into this tree, never import across projects, keep its noise model and its honesty, credit it in a header comment).

## 1. Frames and the scenario

All positions are in the twin's building frame (metres, x east-ish, y north-ish, z up) unless a field says `rel` (the pucks' own relative frame after self-location: arbitrary rotation, mirror and offset). The demo room is the exercise's fire room, chosen like the mayday script does (`Director._fire_room()`): Suite 310 in the fixture, polygon `[[9.5,0],[9.5,8.65],[0,8.65],[0,0]]`, door `d310` at `[9.5, 7.2]` on the corridor side. Never hard-code those: read the twin.

## 2. Python interfaces (all plain dicts, JSON-serialisable unless noted)

### 2.1 Radar engine — builder R1 owns `sandtable/scout/geometry.py`, `scene.py`, `sim.py`, `dsp.py`

```python
from sandtable.scout import geometry, scene, sim, dsp

scene.build_scene(twin, room_id: str, n_pucks: int = 5, seed: int = 310, victim: bool = True, changes: bool = True) -> dict
#   {"room": id, "room_label", "floor", "polygon": [[x, y], ...], "door": [x, y], "height_m": 2.7,
#    "pucks": [{"id": "P1", "x", "y", "z": 0.04}, ...],        # TRUTH: where the thrown pucks came to rest (scattered within a few metres of the door, inside the room)
#    "victim": {"x", "y", "z": 0.25, "posture": "lying", "breathing_bpm": 14.0, "moving": False} | None,   # TRUTH
#    "changes": [{"kind": "debris", "at": [x, y], "radius_m": 0.6, "note": "..."}],                    # TRUTH: where the room differs from the twin
#    "duration_s": 12.0, "frame_hz": 20.0, "seed": seed, "noise": {...stated noise model...}}
#   Deterministic for a given twin, room and seed.

sim.simulate(scene: dict) -> dict            # a "recording": what real pucks would deliver. NOT JSON (numpy arrays inside).
#   {"meta": {"simulated": True, "tap_ns", "bandwidth_mhz", "carrier_mhz", "frame_hz", "n_taps", "noise": {...}},
#    "pucks": ["P1", ...],
#    "ranges": [{"t", "a", "b", "d", "rssi", "fp", "los"}, ...],     # two-way ranging between every pair, kit `range` shape; fp/los drop when a body shadows the link
#    "frames": {("P1", "P2"): {"t": ndarray[T], "cir": complex ndarray[T, n_taps], "fp_index": ndarray[T]}, ...},   # per ordered pair tx -> rx
#    "truth": {...the scene...}}                # for evaluation only; never read by dsp, register, session or the UI

dsp.self_locate(rec, use: list[str] | None = None) -> {"pucks_rel": {"P1": [x, y], ...}, "rms_m": float, "n": int}   # MDS on the mesh ranges; `use` = subset of pucks
dsp.static_echoes(rec, use=None) -> [Echo]      # peaks of the time-averaged CIR beyond the direct path
#   Echo = {"tx", "rx", "path_m": total bistatic path, "excess_m": path - direct, "amp": float, "snr_db": float}
dsp.find_victims(rec, pucks_xy: dict[str, list[float]], use=None, bounds: list | None = None) -> {
#     "victims": [{"x", "y", "sigma_m", "cov": [sxx, sxy, syy], "confidence": 0..1, "n_links": int,
#                  "state": "breathing" | "moving" | "none", "moving": bool, "breathing_bpm": float | None, "breathing_conf": 0..1}],
#     "echo_map": {"origin": [x0, y0], "cell_m", "nx", "ny", "kind": "dynamic", "values": [row-major floats 0..1]},
#     "per_link": [{"tx", "rx", "excess_m", "snr_db", "used": bool}]}
#   Positions are in the frame of `pucks_xy` (pass registered twin-frame positions to get twin-frame victims). `bounds` = polygon to search inside.
#   With 2 pucks: presence and range only (victims have "x": None, "y": None, "excess_m" set). With 1 puck: {"victims": [], "why": "one puck cannot receive its own echo"}.

geometry.image_paths(tx_xy, rx_xy, polygon) -> [{"wall": index, "path_m": float, "point": [x, y]}]    # first-order specular wall reflections (image-source)
geometry.ellipse_points(f1, f2, path_m, n=64) -> [[x, y], ...]
```

Within R1's first 20 minutes: `geometry.image_paths`, `scene.build_scene` and a first `sim.simulate` must exist, because R2 imports them.

### 2.2 Registration and twin comparison — builder R2 owns `sandtable/scout/register.py`, `mismatch.py`

```python
from sandtable.scout import register, mismatch

register.register(pucks_rel: dict, echoes: list[Echo], room: {"polygon", "door"} | None, throw_max_m: float = 6.5) -> {
#     "ok": bool, "method": "twin-echo-match" | "door-prior" | "box-fit", "pucks": {"P1": [x, y], ...} (twin frame; puck frame when there is no twin),
#     "transform": {"rotation_deg", "mirror": bool, "tx", "ty"}, "residual_m": float, "ambiguity": 0..1 (score of the second-best pose / best; 1 = cannot tell them apart), "text": str}
#   Search rotation, mirror and offset so that (a) every puck is inside the room and within throw_max_m of the door, and (b) the measured static echoes agree with geometry.image_paths of the room.
register.fit_box(pucks_rel, echoes) -> {"polygon": [[x, y], ...], "confidence": 0..1, "text": str}     # no twin: a coarse rectangle from the strongest wall echoes
mismatch.find_mismatches(pucks: dict, echoes: list[Echo], polygon, tol_m: float = 0.45) -> [
#     {"id": "M1", "kind": "unexpected_reflector" | "missing_wall", "confidence": 0..1, "where": {"kind": "point", "at": [x, y], "radius_m"} | {"kind": "segment", "a", "b"}, "pairs": int, "text": str}]
#   unexpected_reflector: echoes on several links that no wall predicts and that cross at one place. missing_wall: a wall whose predicted echo is absent on the links that should see it.
```

### 2.3 Session, feeds, server, incident, exercise script — builder R3 owns `sandtable/scout/session.py`, `feeds.py`, `__init__.py`, and edits `sandtable/server.py`, `incident.py`, `director.py`

```python
from sandtable.scout.session import ScoutSession
s = ScoutSession(twin, provider, emit, incident)
await s.deploy(room_id: str | None = None, n_pucks: int = 5, seed: int = 310) -> dict   # runs the phases below, emits progress, returns the Scan; heavy numpy work goes to a thread
s.scan -> dict | None;  s.phase -> str;  s.truth() -> dict | None (evaluation only);  s.reset()
s.brief() -> dict     # DISCRETE summary for the agent: room ids and labels, victim room/nearest node/"2.5 m from the west wall" text, whole bpm, positions rounded to 0.5 m, confidences to 0.1; no timestamps
```
Engine selection: `SANDTABLE_SCOUT_ENGINE` = `sim` (default: R1 + R2) or `fake` (replays `fixtures/scout/sample_scan.json` through the same phases over about 6 s, every message marked `"sample": true`). The fake engine must work within R3's first 20 minutes so the UI and agent builders have a live source; it is also the fallback if the real engine import fails (logged loudly, never silently).

**The Scan object** (`sandtable.scan/1`): exactly the shape of `fixtures/scout/sample_scan.json` (open it; it is the schema by example). Top-level keys: `type: "scan"`, `schema`, `id`, `t`, `floor`, `room`, `room_label`, `simulated`, `rung` (`cir-radar` | `link-shadow` | `tags-only`), `n_pucks`, `pucks[]` (`id`, `x`, `y`, `sigma_m`, plus `geo {lat, lon}` added by the session), `links[]`, `registration{}`, `victims[]` (plus `id`, `text`, `room`, `node`, `geo` added by the session), `echo_map{}`, `outline{}` (`source`: `twin` | `box-fit`), `mismatches[]`, `resolution{}`, `limits[]`, `timing{}`.

**WebSocket** (added to `/ws`):
- `{"type": "scout", "phase": "thrown" | "ranging" | "located" | "registered" | "scanning" | "done" | "failed", "text": "one readable line", "t", "room", "pucks": [...] (once known), "sample": bool}` for progress; phases take about 1 to 3 s each in simulation so that people can follow.
- the Scan object itself (`type: "scan"`) when done; it is also sent to new clients after `hello`, and cleared by `reset`.
- `state` gains `"scout": {"phase", "room", "smoke": 0..1}` and units may have assignment targets that are victims.

**HTTP** (added):

| Method + path | Purpose |
|---|---|
| `POST /api/scout/deploy` `{"room": "r310"?, "n": 5?}` | throw the pucks: starts a scan (409 while one is running or when no incident is active) |
| `GET /api/scout` | `{"phase", "scan": Scan or null}` |
| `GET /api/scout/truth` | simulation ground truth, for evaluation scripts only (404 on real boards) |
| `GET /feed/scan.geojson` | GeoJSON FeatureCollection in WGS84: pucks, victim (with uncertainty radius and breathing rate), mismatch flags, room outline, and every crew unit; `properties.simulated` on each feature |
| `GET /feed/cot.xml` | the same as a list of Cursor-on-Target `<event>` elements (version 2.0: `uid`, `type`, `time`, `start`, `stale`, `how`, `<point lat lon hae ce le>`, `<detail><contact callsign>` and `<remarks>`), wrapped in `<events>`; `how="m-s"` marks simulated positions |
| `GET /feed/cot/stream` | Server-Sent Events: one CoT event per message as things change (the phone crew page consumes this or the GeoJSON to prove it is a feed) |

`feeds.py` exposes pure functions `scan_to_geojson(scan, units, twin) -> dict` and `scan_to_cot(scan, units, twin, now: float) -> str` so they can be tested without a server.

**Incident and agent hand-off:** when a scan is done the incident stores it, adds a timeline entry, and raises situation `scan` (`data` = `s.brief()`); when a rescuer reaches a located victim it raises `victim_reached`. `Incident.snapshot()` gains a `"scan"` key **only when a scan exists** (the mayday exercise's recordings must keep matching: with no scan the snapshot must be byte-identical to today's). `incident.assign(unit, task, target)` accepts a victim id (`"V1"`) as target and task `rescue`.

**Exercise script:** `POST /api/storyboard {"action": "start", "script": "scout" | "mayday", "mode": "auto" | "assist"}`; default script `scout`. Scout script, condition-based like the existing one: reset, alarm, wait for the pre-plan; Engine 1 goes to the door of the fire room; smoke rises (`state.scout.smoke`); Engine 1 throws the pucks (`deploy`); wait for the scan and for the agent's orders; the rescue team walks the agent's route to the victim; `victim_reached`; the agent plans the way out; all out; after-action report. The mayday script must keep working unchanged.

### 2.4 Agent — builder R4 owns `sandtable/agent/**`, `scripts/record_all.sh`, the agent tests

New tools: `get_scan()` (the brief plus the limits), `route` and `assign` accept `"V1"`; situation prompts for `scan` and `victim_reached`, written as goals with a reply budget like the existing ones (`sandtable/agent/prompts.py`); the after-action prompt gets the scan brief. Deterministic fallback policy for both situations. Prompts must stay built from discrete facts only. The existing prompts for `preplan`, `mayday`, `hazard`, `contact` must not change by a byte (their recordings stay valid). Recording the new turns with a real model is done at integration (`scripts/record_all.sh`), not by hand.

### 2.5 Board — builder R5 owns `web/**`, `figures/**`, `scripts/shots.cjs`

A scan layer over the twin in both the 3D view and the 2D plan: puck constellation with ids and mesh links, a sweep while scanning, the echo map as a heat layer, the victim marker with its uncertainty and breathing rate (pulsing at that rate), mismatch flags, the registration note; a "Throw pucks" control; a scout card (phase, rung, pucks, what the scan can and cannot tell: `limits`); a **smoke view** toggle that fogs the camera-like views (label it "illustration: what a camera or LiDAR sees") while the UWB layer stays; a feeds panel linking the GeoJSON and CoT feeds; and the phone page `crew.html` showing the victim and the order by reading `/feed/scan.geojson` (not the WebSocket) with a "feed" label. Until R3's endpoints exist, develop against `GET /fixtures/scout/sample_scan.json` behind a `?devscan=1` URL parameter that is clearly marked on screen as a sample.

### 2.6 Others

- R6 owns `docs/RADAR_FEASIBILITY.md`, `docs/owner_questions.json`, `docs/readme_radar_section.md`, `scripts/radar_arithmetic.py`, `results/radar_arithmetic.json`.
- R7 (after R1 and R2 land) owns `scripts/eval_scout.py`, `results/scout_*.json`, `figures/scout_*.png`.
- R8 owns `hardware/**`, `sandtable/scout/shadow.py` (link-shadow rung from the kit's `range` messages: `shadow.locate(ranges, pucks_xy) -> {"present": bool, "x", "y", "sigma_m", "links_down": [...], "confidence"}`), `sandtable/scout/source_kit.py` (real pucks: the kit's `mesh5` stream, and CIR frames when the firmware delta is flashed), `tests/test_scout_hardware.py`.
- R9 owns `hardware/puck/**` and `figures/puck_*.png`.

## 3. Rules for everyone

- Use the shared `.venv/bin/python` (numpy and pillow are there; add a wheels-only dependency by appending to `requirements.txt` and running `uv pip install -p .venv/bin/python -r requirements.txt`). No new virtualenv. No git commands that write: the lead commits.
- Edit only the files you own. Put requests for others in your report.
- Honesty: never present simulated or sample data as measured; never say "map" without "coarse"; the SIM badge and fixture labels stay; no LiDAR-grade claims; copy L08's caveats where you copy its code.
- The existing tests must keep passing: `bash run_tests.sh` (45 tests, about 30 s) includes replays of recorded model runs that fail if a prompt changes.
- Ports: the lead's demo is on 1810/1910. R3: 1811/1812. R4: 1913/1914. R5: 1911/1912. R8: 1813/1814. Stop every process you start (`PORT=<app> HUB_PORT=<hub> bash scripts/stop_demo.sh`).
- Report at the end, concisely: what exists, what you ran and its outcome, what is unverified, deviations from this contract, requests for other builders.
