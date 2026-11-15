# Runbook: running Sandtable Beacon

Everything needed to run the project, from a laptop demo with no hardware to the real pucks and phones. Each level builds on the one before; stop at the level you need.

| Level | What runs | You need | First time |
|---|---|---|---|
| **1. Stand-in demo** | The command board on open data and a synthetic interior; phones, pucks and crews simulated; the AI command agent replays recorded real model turns | A laptop with Python 3.10 or newer | 5 min |
| **2. Live building data** | The same board on the real building record (CoStar), its 3D scan (Matterport) and aerial imagery (Vexcel) | Level 1, plus two private files that never go through this repository | 15 min |
| **3. Real pucks and phones** | Qorvo DWM3001CDK pucks ranging iPhones that run the app; the board shows where each person is | Level 1 or 2, plus the boards, iPhones and a Mac with Xcode | 1 to 2 h |

## How the parts fit

```
  iPhone running the app, SOS on
     │ UWB ranging (Nearby Interaction session the app opens)
     ├──────────────► puck P1 ┐   boards on Qorvo's Nearby Interaction firmware
     └──────────────► puck P2 ┘
     │ the app sends each distance over Wi-Fi: POST /api/ingest
     ▼
  Beacon server :1885  (laptop or Arduino UNO Q)
     locator: one puck = a ring, two pucks = two mirror candidates,
     the building's walls prune one → a fix with its uncertainty
     │ Beacons feed: GET /api/beacons
     ▼
  Command board :1810  (laptop)
     the building before entry (CoStar record, Matterport twin, Vexcel aerial),
     the beacon and the people on the floor plan, the AI command agent, the report

  Spacing, separately: two boards on the board-to-board firmware range each other;
  the measured spacing goes to the Beacon server and positions the pucks on the plan.
```

**Only phones running the app are found.** A phone transmits no UWB until an app on it opens a ranging session, so phones without the app are not detected by the beacon. The board says so, and uses the building record to show which suites are expected to be occupied but have no report.

---

## Level 1: stand-in demo (no hardware, no keys)

macOS, Linux or WSL:

```bash
git clone https://github.com/iujab/sandtable-beacon.git
cd sandtable-beacon/sandtable-live-fireground-twin
bash run_demo.sh --storyboard        # creates .venv on first run, then plays the exercise hands-free
```

Windows (PowerShell):

```powershell
git clone https://github.com/iujab/sandtable-beacon.git
cd sandtable-beacon\sandtable-live-fireground-twin
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m sandtable.demo --storyboard
```

Open **http://127.0.0.1:1810**. Without `--storyboard`, press **Exercise** on the board to start it. Stop with Ctrl+C (or `bash run_demo.sh --stop`).

The phones part also runs on its own: `cd sandtable-beacon-victim-phones && bash run_demo.sh --story`, then open http://127.0.0.1:1885.

Everything on screen in this level is labelled: STAND-IN for the building layers, SIM for phones, pucks and crews, REPLAY for the agent's recorded turns.

## Level 2: the real building's data (private)

Two private files travel by hand from whoever holds the sponsor keys, never through this repository: a `.env` (the keys and the demo building's ids) and `sandtable-live-bundle.zip` (that building's record, scan, imagery and recorded agent turns). In `sandtable-live-fireground-twin/`, after Level 1:

```bash
cp <path>/laptop.env .env                                       # git-ignored
.venv/bin/python scripts/pack_live_bundle.py unpack <path>/sandtable-live-bundle.zip
.venv/bin/python -m sandtable.adapters.check --live             # all three layers must say live
SANDTABLE_DATA_MODE=live LLM_BACKEND=replay .venv/bin/python -m sandtable.demo
```

PowerShell: `$env:SANDTABLE_DATA_MODE="live"; $env:LLM_BACKEND="replay"; .venv\Scripts\python -m sandtable.demo`.

- Open **http://localhost:1810** (not an IP address): the Matterport 3D view's key only works on `localhost`.
- Every page then carries the header **LIVE SPONSOR DATA, PRIVATE**. Do not screenshot it into anything public.
- With an Anthropic key in `.env` (`ANTHROPIC_API_KEY`), use `LLM_BACKEND=anthropic` for live agent decisions. With `replay`, only the recorded exercise has recorded turns; anything else runs on a labelled fallback policy.
- If anything fails at the table, restart without the two variables: that is the Level 1 demo.
- Full detail: `sandtable-live-fireground-twin/docs/TOOLKIT_SWAP.md`.

---

## Level 3: real pucks and phones

### 3.1 Parts

- **Qorvo DWM3001CDK boards**: two for a basic beacon, four for a beacon that measures its own spacing while it ranges phones (3.2). Micro-USB **data** cables into the **J9** port (the on-board J-Link).
- **Power** per puck: a USB battery bank, the Arduino UNO Q's USB-C hub, or an Android phone through a USB-OTG adapter. A puck that only ranges phones needs power and nothing else.
- **iPhones** with UWB (iPhone 11 or newer), running the Sandtable Beacon app. iPhone 14 and 15 report distance only (no direction) to these boards.
- A **Mac with Xcode** and an Apple ID, to build the app.
- A **network** every device can join: a phone's hotspot is the reliable choice (venue Wi-Fi often blocks device-to-device traffic).
- Optional: an **Arduino UNO Q** to run the Beacon server at the table.

### 3.2 One board, one firmware

| Firmware | Talks to | Gives | Get it |
|---|---|---|---|
| Qorvo **Nearby Interaction** (QANI), stock | iPhones running the app | distance from each puck to each phone | Qorvo's DWM3001CDK product page (email form). Keep the `.hex` out of this repository |
| **Board-to-board ranging** (ours, SS-TWR) | the other puck's board | the spacing between the pucks | `sandtable-beacon-victim-phones/hardware/twr_firmware/` |

A board runs one firmware at a time: the stock phone firmware cannot range another board, and ours cannot talk to phones. Three ways to have both:

1. **Measure once, then switch (today, two boards).** Flash the board-to-board firmware, measure the spacing (3.5), flash the phone firmware, and keep the pucks at that spacing. Or tape-measure it.
2. **Four-board beacon (feasible with five boards).** Each puck is a pair: one board on the phone firmware, one on the board-to-board firmware. The spacing is measured continuously while phones are ranged. Untested: whether two boards side by side interfere; put them on different UWB channels (ours uses channel 5; check the phone firmware's channel in its RTT log).
3. **One firmware doing both (production).** Needs Qorvo's QM33 SDK and a multi-session build. Not built.

Detail and the procedure: `sandtable-beacon-victim-phones/hardware/BOARD_TO_BOARD.md`.

### 3.3 Flash the boards

**Phone firmware (QANI).** With J-Flash Lite on a Mac or Windows: `sandtable-beacon-victim-phones/hardware/FLASH.md`. With OpenOCD (Linux, the UNO Q, or a Mac with OpenOCD):

```bash
openocd -c "adapter driver jlink" -c "adapter serial <J-LINK SERIAL>" -c "transport select swd" \
  -f target/nrf52.cfg -c init -c "program <path>/DWM3001CDK-QANI-FreeRTOS.hex verify reset exit"
```

- The J-Link serial is in `ls /dev/serial/by-id/` (`usb-SEGGER_J-Link_<SERIAL>-if00`). On Linux the probe needs root or a udev rule.
- Close anything holding the probe first (an RTT reader or another OpenOCD), or it fails with `LIBUSB_ERROR_BUSY`.
- **Check:** the board advertises over Bluetooth as `DWM3001CDK (XXXXXXXX)`. Qorvo's free "Nearby Interaction" app on an iPhone connects to it and shows a distance (it holds one board at a time). The owner's iPhone did this with both boards on 2026-11-15.

**Board-to-board firmware.** The Decawave example code it is built from is marked "All rights reserved", so this repository ships only our changes (`hardware/twr_firmware/app.c.ed`) and a script that rebuilds the source from the public starter project at a pinned commit and checks the result by hash. You need `git`, `make`, `arm-none-eabi-gcc` with newlib-nano, and Nordic's nRF5 SDK 17.1.0 (free download, unzip anywhere):

```bash
cd sandtable-beacon-victim-phones
python3 hardware/twr_firmware/prepare.py --starter ../DWM3001C-starter-firmware   # clones it if missing, applies our changes, checks the hash
cd ../DWM3001C-starter-firmware
make -f Makefile.twr ROLE=INITIATOR NRF5_SDK=/path/to/nRF5_SDK_17.1.0_ddde560 -j4
make -f Makefile.twr ROLE=RESPONDER NRF5_SDK=/path/to/nRF5_SDK_17.1.0_ddde560 -j4
```

Flash `build/INITIATOR/app.hex` on one board and `build/RESPONDER/app.hex` on the other with the OpenOCD command above. This build path is compile-verified; the boards that were measured ran the same source built on the UNO Q. The line format, the long-range profile and antenna-delay calibration: `hardware/twr_firmware/README.md`.

### 3.4 Network

Turn on a phone hotspot and join the laptop, the UNO Q (if used) and every iPhone to it. Note the address of the machine that will run the Beacon server (`ipconfig getifaddr en0` on a Mac, `hostname -I` on Linux). If every device is on your Tailscale network, its tailnet address works too.

### 3.5 Measure the puck spacing

With the two spacing boards on the board-to-board firmware, powered and standing where the pucks will be:

Terminal 1: serve the initiator board's RTT log on port 19021 (this exact form read the firmware on the UNO Q; prefix `sudo` there):

```bash
openocd -c "adapter driver jlink" -c "adapter serial <INITIATOR SERIAL>" -c "transport select swd" -c "adapter speed 4000" \
  -f target/nrf52.cfg -c init -c "rtt setup 0x20000000 0x20000 {SEGGER RTT}" -c "rtt start" -c "rtt server start 19021 0"
```

Terminal 2: in `sandtable-beacon-victim-phones/`, turn the ranges into a spacing and send it to the Beacon server (3.6):

```bash
python3 -m beacon.host --server http://<server>:1885 --puck P1 --baseline-to P2 --rtt 127.0.0.1:19021 --learn
```

Within about 10 seconds it prints `baseline P1-P2: ... -> posted`, and the boards show the spacing with its uncertainty. No spacing boards? Tape-measure antenna to antenna and post it by hand:

```bash
curl -X POST http://<server>:1885/api/pucks/baseline -H 'content-type: application/json' \
  -d '{"pair": ["P1", "P2"], "d_m": 1.33, "sigma_m": 0.02, "source": "manual"}'
```

Then flash the phone firmware on both boards **without moving them** (or use the four-board beacon). Step by step: `hardware/BOARD_TO_BOARD.md`.

**Measured on the owner's two boards (2026-11-15, long-range profile, boards about 1.57 m apart):** 4.9 ranges a second, every one of the last 50 polls answered (99.85% over the whole run, 11 timeouts in 7,533), spread ±0.04 m (`sandtable-beacon-victim-phones/results/twr_measured_2026-11-15.json`). That is repeatability, not accuracy: no tape-measured reference was taken and the antenna delay is uncalibrated, so the absolute distance carries an unknown constant offset.

### 3.6 Start the Beacon server

On a laptop, in `sandtable-beacon-victim-phones/`:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt     # once
.venv/bin/python -m beacon.demo --source live --lan                              # real phones; nothing simulated
```

On the Arduino UNO Q (Debian), `python3 -m venv` fails as shipped (no `ensurepip`, and `sudo` needs the board's password). Use:

```bash
python3 -m venv --without-pip .venv
curl -sS -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py && .venv/bin/python /tmp/get-pip.py
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m beacon.demo --source live --lan        # or --tailnet
```

**Check:** `http://<server>:1885/healthz` answers `"source": "live"`. The server's own board is at `http://<server>:1885/`.

**With the real building's data (Levels 2 and 3 together)** the Beacon server must run on the same building as the board, or the board warns. Point it at the private beacon bundle, which the command board writes to `sandtable-live-fireground-twin/data/live/beacon_bundle/` the first time it starts in live mode (copy that folder by hand if the server runs on the UNO Q; it is sponsor data, keep it private):

```bash
BEACON_BUNDLE_DIR=<path>/beacon_bundle BEACON_DATA_DIR=<a private folder> COSTAR_MODE=live MATTERPORT_MODE=live \
  .venv/bin/python -m beacon.demo --source live --lan
```

Tested on the build machine: the board's same-building check passes against this server, and fails, as it should, against a stand-in one.

### 3.7 Start the command board against that server

```bash
cd sandtable-live-fireground-twin
.venv/bin/python -m sandtable.demo --beacon-url http://<server>:1885
```

The board checks the Beacon server is on the same building and warns if it is not. Add `SANDTABLE_DATA_MODE=live` for the real building (Level 2).

### 3.8 Build and install the iPhone app

On the Mac: open `sandtable-beacon-victim-phones/apps/ios/SandtableBeacon.xcodeproj`, sign with your Apple ID team, switch on **Developer Mode** on each iPhone (Settings, Privacy & Security), and run it on each phone. In the app's Setup screen enter `http://<server>:1885` (or scan the QR code on the Beacon server's board page). Build notes and likely errors: `docs/IOS_BUILD.md`; what has been verified against the real boards: `docs/IOS_HANDOFF.md`.

The app holds a session with **both** pucks at once and sends both distances; that is what makes a position. Qorvo's own app cannot do this.

### 3.9 On scene: place the beacon

Two things must be true: each puck is on the plan where it landed, and the server knows which board is which puck. It tells them apart by each board's Bluetooth name (`DWM3001CDK (XXXXXXXX)`), which is what a phone reports with every distance.

**Name the boards (once per beacon).** Open the Beacon server's own page, `http://<server>:1885/`, with the app open on one phone near the pucks. Both boards appear under **Pucks heard, not placed yet**, each by its Bluetooth name with a suggested id. Pick one and click the plan roughly where it lies; that ties the name to P1 or P2. Do the same for the other.

**Place them on the command board** (`http://localhost:1810`):

1. Press **Dispatch alarm**. Pucks can only be placed during an incident.
2. Press **Place pucks** (in the header or the plan's toolbar), or **Place the pucks** under step 2, *Beacon on scene*. The guide *Where did the beacon land?* opens over the plan.
3. **Pick the floor.** Only floors with an interior plan can take pucks.
4. **Tap where P1 landed.** The plan zooms to that room and reads back the distance from its walls.
5. **Tap toward P2.** If the pucks measured their spacing (3.5), a dashed circle shows it and P2 snaps onto it in the direction you tapped ("P2 goes 3.97 m from P1, the spacing the pucks measured"). Otherwise P2 goes where you tap. Drag to adjust, or type the distances from the west and south walls.
6. Press **Send 2 pucks**. *Beacon on scene* then shows the room, how each puck was placed ("placed by hand" or "spaced by the measured baseline") and the spacing. If the plan spacing and the measured spacing disagree, an orange warning says so, with a **Move the pucks** button.

Placing or moving the pucks keeps the names you tied to them (tested). For exact positions from a tape measure, use the API instead (`"merge": true` leaves the other puck where it is):

```bash
curl -X POST http://<server>:1885/api/pucks -H 'content-type: application/json' -d '{
  "room": "<room id>", "merge": true,
  "pucks": [{"id": "P1", "x": 9.15, "y": 6.70, "accessory": "DWM3001CDK (XXXXXXXX)"}]}'
```

Put both pucks along one wall: two pucks give two mirror-image candidates, and with the pucks on a wall one candidate falls outside the room and is ruled out.

### 3.10 Run it

1. Occupants open the app and press **SOS** (status, how many people, can they move).
2. Each phone's distances reach the Beacon server every half second; the board shows a ring, then two candidates, then a fix.
3. The command agent sets the phones against the building record, assigns a crew and a route through the twin, and writes the report.

**Write down** what you measure (the true position of each phone against the board's fix): every write-up still says "simulated" for phone positions until real numbers exist.

---

## Which room is the beacon in, and which room is a person in?

- **The beacon's room is set by the responder** when placing it on the plan (3.9). That is the most reliable source: the crew knows which window or door it went through. The floor plan comes from the building twin and the CoStar record.
- **A person's room** follows from their position: once the pucks are on the plan, the fix and the building's walls give the room. Two pucks along one wall usually resolve to a single spot; otherwise the board shows both candidates and the crew checks both.
- **Floor:** the app reports the occupant's stated floor and barometric pressure.
- **GPS** identifies the building and the side of it, but is unreliable indoors and does not give the room.
- **Sensing walls with UWB "radar"** (from the channel impulse response) is a research idea, not built: it needs its own boards or time slots and cannot run on boards that are ranging phones.

Detail: `sandtable-beacon-victim-phones/docs/ROOM_LOCATION.md`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Matterport 3D view blank | Open the board as `http://localhost:1810`, not an IP address |
| `LIBUSB_ERROR_BUSY` when flashing | Something holds the J-Link: stop the RTT reader or other OpenOCD, then retry |
| Qorvo's app: second board stuck "loading" | That app holds one board at a time; force-quit it and connect to one board |
| The app reaches no server | All devices on the same hotspot; type the server's IP address and port 1885; check `/healthz` from the phone's Safari |
| Server replies `unknown_accessories` | The puck was registered under a different name: use the board's exact Bluetooth name `DWM3001CDK (XXXXXXXX)` |
| A phone shows two spots, never one | Both pucks are not along one wall, or the mirror spot is still inside the room: move one puck along the wall |
| Board says "fallback" for agent turns | No recording for that situation and no live model: add `ANTHROPIC_API_KEY` and use `LLM_BACKEND=anthropic` |
| Anything fails at the table | Restart the command board with no live variables: the stand-in demo |

## What it does not do

- It finds only phones running the app with SOS on. Phones without it are not detected.
- Phone positions from real boards have not been measured yet; all position accuracy figures in the write-ups are simulated.
- It is a prototype, not a certified life-safety system.
