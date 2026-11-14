# uwb-kit stream interface — v1 (FROZEN)

Written by the bench orchestrator before the kit existed, so every UWB project can build in parallel against it. The uwb-kit team implements this exactly. Additive changes only (new optional fields, new message types); never rename or remove. If something here is impossible on the real boards, the kit team documents the deviation in `uwb-kit/DEVIATIONS.md` and keeps the wire format.

## Picture

```
 UWB boards ──(USB serial | Wi-Fi UDP | BLE)──▶  hub  ──▶ WebSocket  ws://HOST:1800/stream     (JSON text frames)
 simulator  ─────────────────────────────────▶        ──▶ SSE        http://HOST:1800/events     (same JSON, one per event)
 replay file ────────────────────────────────▶        ──▶ REST       http://HOST:1800/state ...  (snapshots + control)
```

The hub runs on a laptop or on the UNO Q. Projects never talk to boards directly. `HOST` defaults to `127.0.0.1`, port `1800` (override with `UWBKIT_URL`, e.g. `ws://127.0.0.1:1800/stream`). CORS is open (`*`) so a browser page on any port can connect.

## Coordinates and units

- Metres, seconds. Site frame is right-handed, **z up**, origin and axes defined by the anchor layout in the `anchors` message. 2-D projects ignore `z`.
- `t` is Unix time in seconds (float) on the hub's clock.
- Node ids are short strings: anchors `A1..A4`, tags `T1..T2`, mesh nodes `N1..N5`. A project must not assume how many exist; read `hello.nodes`.

## Messages (one JSON object per WebSocket text frame / SSE `data:` line / NDJSON line)

Every message has `type` and (except `hello`/`anchors`) `t`. Unknown types and unknown fields must be ignored by clients.

### `hello` — first message on every connection
```json
{"type":"hello","kit":"uwb-kit","v":1,
 "simulated":true,
 "source":"sim:office-walk",
 "profile":"4A1T",
 "rate_hz":10,
 "nodes":{"A1":{"role":"anchor"},"A2":{"role":"anchor"},"A3":{"role":"anchor"},"A4":{"role":"anchor"},"T1":{"role":"tag","label":"visitor"}},
 "frame":"site","dims":2}
```
`simulated` is true for the simulator and for replays of simulated recordings. **Any UI that displays kit data must show a visible SIM badge while it is true.** `profile` is one of `4A1T` (4 anchors, 1 tag), `3A2T`, `mesh5` (anchor-free, all pairwise ranges), or a free string.

### `anchors` — sent right after `hello`, and again whenever calibration changes
```json
{"type":"anchors","frame":"site",
 "anchors":{"A1":{"x":0.0,"y":0.0,"z":2.0},"A2":{"x":6.0,"y":0.0,"z":2.0},"A3":{"x":6.0,"y":4.5,"z":2.0},"A4":{"x":0.0,"y":4.5,"z":2.0}},
 "calibrated":"manual|auto|sim"}
```

### `range` — one two-way-ranging result between two nodes
```json
{"type":"range","t":1794668400.123,"seq":412,"a":"T1","b":"A2","d":3.412,
 "raw":3.447,"rssi":-78.5,"fp":-80.1,"los":0.93}
```
`d` is the bias-corrected distance in metres. Optional: `raw` (uncorrected), `rssi` and `fp` (total and first-path receive power, dBm), `los` (0-1 line-of-sight confidence; low means probably through a body/wall). In `mesh5`, both ends can be any node.

### `pos` — a solved position for one node (tags, or every node in `mesh5`)
```json
{"type":"pos","t":1794668400.130,"id":"T1","x":2.31,"y":4.02,"z":1.10,
 "vx":0.42,"vy":-0.10,"vz":0.0,"speed":0.43,"heading":-0.23,
 "cov":[0.012,0.001,0.015],"n":4,"q":0.87,"src":"ekf"}
```
`cov` = `[σxx, σxy, σyy]` in m². `n` = ranges used. `q` = 0-1 fix quality. `heading` (radians, from velocity; absent when nearly stationary). `src` = `ekf|lsq|mds|sim-truth`. In `mesh5` the frame is relative: `N1` at the origin, `N2` on +x, `N3` with y > 0.

### `event` — discrete things the kit detects
```json
{"type":"event","t":1794668401.0,"id":"T1","name":"motion_start","data":{}}
```
Kit-level names: `button` (data `{"n":1}` if the board has a button), `motion_start`, `motion_stop`, `node_online`, `node_offline`, `proximity` (data `{"other":"T2","d":0.6,"state":"enter|exit"}` using the kit's default 1.0 m threshold). Zones, gestures and everything domain-specific are computed **by the project**, not the kit.

### `status` — about once per second
```json
{"type":"status","t":1794668401.0,"rate_hz":9.8,"nodes_online":["A1","A2","A3","A4","T1"],"dropped":0,"battery":{"T1":3.91}}
```

## HTTP (same port)

| Method + path | Purpose |
|---|---|
| `GET /state` | `{"hello":{...},"anchors":{...},"pos":{"T1":{...last pos...}},"ranges":{"T1-A1":{...last range...}},"status":{...}}` |
| `GET /events` | Server-Sent Events stream of the same messages (for clients without WebSocket) |
| `GET /healthz` | `{"ok":true}` |
| `POST /anchors` | Body = an `anchors` message; sets anchor coordinates (manual calibration) |
| `POST /calibrate` | Start anchor self-calibration (anchors range each other; kit solves the layout); result arrives as a new `anchors` message |
| `GET /sim/scenarios` | List built-in scenario names (sim source only) |
| `POST /sim/scenario` | `{"name":"office-walk"}` or `{"scenario":{...inline scenario...}}`; restarts the sim with it |
| `POST /sim/control` | `{"cmd":"pause|resume|restart|speed","value":2.0}` |
| `POST /sim/teleport` | `{"id":"T1","x":1.0,"y":2.0}`; sim only, for interactive demos (drag a tag in a UI) |
| `POST /record` | `{"cmd":"start|stop","path":"session.ndjson"}`; NDJSON of every message, replayable |

## Running the hub (the kit team fills in the exact command in `uwb-kit/READY`)

```
uwbkit hub --source sim --scenario <name-or-path.json> [--profile 4A1T] [--port 1800] [--host 127.0.0.1]
uwbkit hub --source serial:/dev/ttyUSB0          # a gateway board on USB
uwbkit hub --source udp:0.0.0.0:1801             # boards send over Wi-Fi
uwbkit hub --source replay:session.ndjson [--loop]
```

Several projects may run their own hub instance on **their own port** (e.g. project port + 100) with their own scenario; port 1800 is the shared default instance.

## Simulator scenario file (projects write these)

```json
{
  "name": "clinic-hallway",
  "dims": 2,
  "profile": "4A1T",
  "rate_hz": 10,
  "duration_s": 120, "loop": true,
  "room": {"outline": [[0,0],[8,0],[8,5],[0,5]],
           "walls": [{"a":[4,0],"b":[4,3],"nlos_bias_m":0.35,"attenuation_db":8}],
           "labels": [{"name":"door","at":[0,2.5]}]},
  "anchors": {"A1":{"x":0,"y":0,"z":2.0},"A2":{"x":8,"y":0,"z":2.0},"A3":{"x":8,"y":5,"z":2.0},"A4":{"x":0,"y":5,"z":2.0}},
  "nodes": {
    "T1": {"role":"tag","label":"patient","z":1.0,
           "path":[{"at":[1,1],"t":0},{"at":[7,1],"t":12},{"at":[7,4],"hold_s":3},{"at":[1,4],"speed":0.8}],
           "path_mode":"linear|spline", "jitter_m":0.02,
           "events":[{"t":20,"name":"button","data":{"n":1}}]}
  },
  "noise": {"sigma_m":0.08, "nlos_extra_sigma_m":0.15, "outlier_prob":0.02, "outlier_m":1.5, "dropout_prob":0.03},
  "seed": 7
}
```
A waypoint gives either an absolute time `t`, a `speed` (m/s) from the previous waypoint, or `hold_s`. The simulator publishes noisy `range` messages, the solver's `pos` (so solver error is realistic), and exposes ground truth only via `GET /sim/truth` (for computing error metrics in `results/`, never for the demo UI).

## Clients (shipped in the kit; vendor a copy into your project when done)

Python:
```python
from uwbkit.client import Stream          # sync
for msg in Stream("ws://127.0.0.1:1800/stream"):   # reconnects automatically
    if msg["type"] == "pos": ...
# asyncio: `async for msg in AsyncStream(url): ...`
```
JavaScript (browser or Node, single file `uwbkit-client.js`, no dependencies):
```js
const kit = new UwbKit("ws://localhost:1800/stream");
kit.on("hello", h => showSimBadge(h.simulated));
kit.on("pos", p => draw(p));
```

## If the kit is not ready yet

Write a 40-line mock that emits `hello`, `anchors`, then `range`/`pos` at 10 Hz from a scripted path, on your own port, in exactly this format. Delete it (or keep it as a test fixture) once `uwb-kit/READY` exists.

---

# v1 additions (kit team; additive only: nothing above was renamed or removed)

Everything in this section is optional for clients. Code written against the frozen text above keeps working.

## Messages

- **`hello` can arrive again on a live connection** when the configuration changes (`POST /sim/scenario`, a new board appears, a fake-board emulator is detected). Replace your node list when it does; a fresh `anchors` follows it.
- `hello` extra fields: `kit_version`; `room` (the scenario's `outline` / `walls` / `labels`, so a UI can draw the room without a REST call); `sim` = `{"scenario", "noise", "description"}` when simulated (`noise` is the effective noise model: quote it next to simulated numbers); `recorded_source` on replays. `rate_hz` is the nominal rate.
- `hello.frame` is `"mesh"` (not `"site"`) for `mesh5`, where the frame is relative (N1 at the origin, N2 on +x, N3 with y > 0; with `dims: 3` additionally N4 with z > 0).
- `hello.simulated` is also true when the boards are the fake-board emulator (`uwbkit fakeboard`, banner `chip=FAKE`).
- `anchors.calibrated` can also be `"none"`: real boards whose anchor coordinates are not set yet (`anchors` is then `{}` and no `pos` is published until `POST /anchors` or `POST /calibrate`). In `mesh5`, `anchors` is `{}`.
- `range.seq` is assigned by the hub and increases by one per published range.
- `event` extra names: `calibration` (`id: "hub"`, data `{"ok":true,"rms_m":0.021,"pairs":6,"anchors":[...]}` or `{"ok":false,"error":"..."}`), `replay_end` (`id: "hub"`). `motion_start` / `motion_stop` data carries `speed`. `proximity` is emitted once per pair with `id` = the lower node id.
- `status` extra fields: `clients`, `uptime_s`, `recording` (path, while recording), `sim` = `{"paused","speed","t_sim"}`. `dropped` = cumulative count of range messages the hub rejected plus board lines that failed to parse (bad checksum / malformed).

## Stream options

- `ws://HOST:PORT/stream?types=pos,event` and `/events?types=...`: server-side filter (`hello` and `anchors` are always sent first).
- A text frame `ping` from the client is answered with `{"type":"pong"}`. The hub also sends WebSocket ping frames every 20 s.

## HTTP

| Method + path | Purpose |
|---|---|
| `GET /sim/truth` | Simulator ground truth `{"t","t_sim","nodes":{"T1":{"x","y","z","vx","vy","vz"},...}}` (plus `nodes_rel` in the mesh frame for `mesh5`). For error metrics in `results/` only, never for a demo UI |
| `GET /sim/scenarios` | Response shape: `{"scenarios":[names],"current":"office-walk","details":{name:{"profile","dims","description"}},"simulated":true}` |
| `POST /sim/teleport` | Also `{"id":"T1","release":true}`: the node resumes its scripted path. A teleported node stays put until released or the sim restarts |
| `POST /calibrate` | Optional body `{"duration_s":5,"z":2.0}` (`z`: anchor height, number or per-anchor map; default: keep current). Returns `{"ok":true,"started":true}` at once; the result is a new `anchors` message (`calibrated:"auto"`, frame: A1 at the origin, A2 on +x, A3 with y > 0) plus a `calibration` event |
| `POST /record` | Also `{"cmd":"status"}`. `start` answers `{"ok":true,"recording":true,"path":...}`, `stop` answers `{"ok":true,"recording":false,"path":...,"messages":N}`. Paths are confined to the hub's `--record-dir` (default: its working directory) |
| `GET /diag` | Bring-up table: `{"nodes":{id:{role,online,age_s,battery,chip,fw,...}},"links":{"T1-A1":{count,rate_hz,d_mean,d_std,d,rssi,fp,los,age_s}},"source":{...},"logs":[...],"bias":{...}}` |
| `GET /room` | `{"room":{outline,walls,labels},"anchors":{...},"dims","profile"}` |
| `POST /bias` | Hub-side range correction for real boards: `{"offsets":{"T1-A1":0.07,"A2":0.03},"scale":1.0}`; `d = d * scale - offsets`, the uncorrected value is kept in `raw` |
| `POST /command` | `{"line":"ANTDLY 16385","node":"A1"}`: send a firmware command line to a board (serial / UDP sources) |
| `GET /uwbkit-client.js`, `GET /` | The JS client, and the web console |

Errors are `{"ok":false,"error":"..."}` with a 4xx status; `/sim/*` answers 409 on a non-simulator source.

## Running the hub: extra forms

```
uwbkit hub --source serial:auto                        # scan USB ports for UWB boards, hot-plug tolerant
uwbkit hub --source serial:COM5,COM7@115200            # several ports
uwbkit hub --source serial:COM5+udp:0.0.0.0:1801       # several sources joined with +
uwbkit hub ... --anchors anchors.json --tag-z 1.0 --record run.ndjson --host 127.0.0.1,<other address>
```

Real-board hubs persist anchor coordinates in `uwbkit_anchors.json` (working directory) across restarts.
`--source sim --profile mesh5` (no `--scenario`) starts the first built-in scenario with that profile.
`--id-map 1786=A1,1787=A2` maps a vendor firmware's device ids to kit ids; `--proto` forces a board format.

## Simulator scenario: extra keys

`description` (string); node `"at": [x, y]` without a `path` (static node); node `speed` (default m/s for waypoints without timing), `bias_m`, `close_loop`; 3-element `at` for 3-D motion; per-wall `nlos_extra_sigma_m`. An unrecognised `path_mode` (such as the `"linear|spline"` shorthand above) means `linear`. A path that does not end where it started gets a closing leg so looping never jumps.

## Board line protocol

What the firmware prints over USB serial / UDP and how the hub parses it: `docs/CONTRACTS.md` section 4. Boards running a vendor's stock firmware (Makerfabs MaUWB `AT+RANGE`, DWM1001 PANS `les` / `lec`, Makerfabs DW1000 example sketches) are auto-detected by the same parser.

## Limits and edge cases (v1 additions)

- The simulator hub steps a scenario at no more than 100 Hz; a larger `rate_hz` is accepted and reported as 100.
- `POST /sim/teleport` rejects coordinates more than 1000 m from the origin and unknown ids (HTTP 400). Teleporting an anchor moves its true position but not the `anchors` message: a way to simulate a bumped anchor.
- Ranges longer than 150 m or not finite are dropped by the hub and counted in `status.dropped`.
- A handler failure answers `{"ok":false,"error":"internal error: ..."}` with HTTP 500; the stream keeps running.
