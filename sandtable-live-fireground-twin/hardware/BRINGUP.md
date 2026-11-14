# Sandtable at the venue: hardware bring-up

For one tired person at 2 am. Short on purpose.

**Honest status.** The owner's UWB boards are **Qorvo DWM3001CDK (nRF52833 + DW3110)**. There is no nRF toolchain on the bench, and the shared uwb-kit has no firmware or parser for these boards. So **Scout** (pucks as a radar) and **Crews** (tagged firefighters) are **simulated modes** this weekend. The real hardware this weekend is **Sandtable Beacon** (victims' phones), built by the sibling lane L16. No UWB board has ever been attached to anything in this repository.

Commands are Windows PowerShell. On Linux or the UNO Q, use `.venv/bin/python` in place of `.venv\Scripts\python`.

## A. What runs on real hardware: Beacon

> **POINTER (to be filled at packaging):** L16's hardware documents (bring-up, wiring, bill of materials, build logs) will be vendored into this repository under **`vendor/beacon/`**. Until then they live in the sibling project `../sandtable-beacon-victim-phones/` (`hardware/`, `docs/`). Follow them for the boards and the phones. This file does not repeat them and has not checked them.

## B. This command board next to a Beacon server (5 min)

1. Software, once and before you leave (no internet at the venue: `pip download -r requirements.txt -d wheels` beforehand):
   ```powershell
   py -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
2. Start the Beacon server per L16's documents (port **1885**), on the same laptop or on the UNO Q.
3. Start the board (port **1810**) and point it at Beacon:
   ```powershell
   $env:BEACON_URL = "http://127.0.0.1:1885"        # the UNO Q instead: http://<uno-q address>:1885
   .venv\Scripts\python -m sandtable.demo            # add --host 0.0.0.0 to reach it from other devices
   ```
   On Linux: `BEACON_URL=http://127.0.0.1:1885 .venv/bin/python -m sandtable.demo`. `BEACON_URL` is the variable the lead's integration defines. When this file was written, nothing in `sandtable/` read it yet. Confirm with `python -m sandtable.demo --help` or the README.
   **Works when** <http://localhost:1810/> opens. The UWB layers then show the orange `SIM` badge, because Scout and Crews stay simulated.
4. The phone crew page. Over USB: `adb reverse tcp:1810 tcp:1810`, then open `http://localhost:1810/crew.html?unit=RIT` in Chrome. Over Wi-Fi: start with `--host 0.0.0.0` and open `http://<laptop-ip>:1810/crew.html?unit=RIT`. Allow Python through the Windows firewall on private networks when asked. Speech works without a secure context; vibration may not.
5. The UNO Q as the box (optional; untested on a UNO Q). It runs Debian aarch64, and every dependency is a wheel. Copy the folder, do step 1 with `.venv/bin/python`, then run step 3 with `--host 0.0.0.0`. The laptop and the phone then only need browsers.

## C. Simulated modes

**Crews (tagged firefighters).**
- Run `.venv\Scripts\python -m sandtable.demo --storyboard`. The exercise plays by itself.
- On the board, start the original mayday exercise with **Run exercise** (scripts `scout` and `mayday`; `POST /api/storyboard {"action": "start", "script": "mayday"}`).
- Positions come from the uwb-kit simulator (`hardware/scenarios/sandtable_rig_*.json`) and carry the `SIM` badge.
- Real hardware would need four fixed anchors and one or two tags, each running ranging firmware that the kit hub can read. On the owner's DWM3001CDK boards that means firmware for the nRF52833, or a kit parser for Qorvo's stock firmware output. The kit has neither. Future work, not attempted. The steps for ESP32 boards are in the appendix.

**Scout (thrown pucks as a radar).**
- With the board running, click **Throw pucks** (or `POST /api/scout/deploy`; it needs an active incident). Running `--storyboard` plays the scout exercise.
- The scan comes from the scout simulator (`SANDTABLE_SCOUT_ENGINE=sim`, the default) and is marked simulated.
- `sandtable/scout/shadow.py` holds the no-CIR fallback, link shadowing: from which puck-to-puck links a body blocks, it gives presence and a coarse position between the pucks. It is simulation-only. Against the scout simulator (`hardware/shadow_eval.json`, made by `hardware/eval_shadow.py`, SIMULATED), it detected 14 of 14 people inside the pucks' outline. The median error was 0.58 m and the worst 1.05 m. It reported 0 of 8 people outside the outline and raised 0 false alarms in 8 empty rooms. Thrown from the door, the victim usually lands outside that outline.
- Real hardware would need CIR-capable firmware for the DW3110 on the nRF52833. That is an accumulator read plus first-path diagnostics on every frame the board overhears, streamed next to the pairwise ranging (all ten links of five pucks). The ESP32 version of exactly this, from lane L08, is kept in `hardware/firmware/esp32-reference/` with L08's `CIR_NOTES.md`. Future work, not attempted.

## Appendix: tagged-crew bring-up for ESP32 + DW3000 boards with the shared uwb-kit (not the owner's boards)

These steps apply only to Makerfabs-style ESP32 + DW3000 (or DW1000) boards running the shared kit firmware. They were run against the kit simulator and the kit's fake-board emulator only. Allow about 30 minutes on top of the kit's own `BRINGUP.md` (vendored at `vendor/uwbkit/BRINGUP.md`, about 45 min). You need: five boards, a tape measure, painter's tape and a marker, four chairs or tripods, and the laptop.

1. Do section B step 1. Dry run: `.venv\Scripts\python -m sandtable.demo --storyboard`. **Works when** the `SIM` badge shows and the exercise plays in about 80 seconds, ending in the after-action report.
2. Follow the kit's `BRINGUP.md` steps 0 to 5. Flash **four anchors and one tag**: `python -m uwbkit flash --variant dw3000 --all 4A1T`, run from the kit folder. The recorded demo script expects this layout, with the rescue team as a software unit. `3A2T` also works; a second person then carries the rescue-team tag.
3. Choose the scale. The numbers come from the simulator (`results/sim_eval.json`) and are a guide. **Floor rig:** a 3.6 m x 4.4 m anchor rectangle at scale 1:9.8, room-level location; this is the one to use. **Table rig:** 0.72 m x 0.88 m at 1:49, coarse. Any rectangle in the proportions 35.1 : 43.2 works, and bigger is better.
4. Tape the plan (10 min). `hardware/RIG_LAYOUT.md` lists the corners in cm from anchor A1; the drawing is `hardware/rig_layout.svg`. At minimum tape the outline, the corridor ring, Stairs A and B, and Suite 310 with its door. For another rectangle size, run `python scripts/make_rig_layout.py <scenario copy>`.
5. Place the anchors at the four corners, about 1.2 m up, antennas vertical. A1 goes at the corner nearest Suite 310 (it is the origin), A2 along the short side, then A3 and A4 counter-clockwise seen from above.
6. Start: `.venv\Scripts\python -m sandtable.demo --hub serial:auto --profile 4A1T`. This starts its own kit hub on 1910 and the board on 1810. For a hub already running elsewhere use `--hub url:ws://127.0.0.1:1800/stream`; for boards on Wi-Fi use `--hub udp:0.0.0.0:1801`.
7. Anchors, once (saved in `uwbkit_anchors.json`): `.venv\Scripts\python -m uwbkit anchors --url http://127.0.0.1:1910 rect 3.6 4.4 --z 1.2`. **Works when** the badge reads `UWB LIVE · 4A1T` with no `SIM` and the E1 dot appears.
8. Registration: stand the tag on the Stair A door mark, then on Suite 310. If the dot is off, run `.venv\Scripts\python -m sandtable.calibrate`. If it is mirrored, swap A2 and A4 in step 7.
9. Exercise: click **Guided exercise** and follow the `YOUR MOVE` cues. Hold the tag still for 8 s and the mayday fires. Keep to the script while in `LLM replay`; anything else gets the labelled `reflex-fallback`.

| What you see | Do |
|---|---|
| `UWB hub disconnected` | `python -m uwbkit doctor --url http://127.0.0.1:1910` |
| Hub up, no dot | anchors not set (step 7) |
| Dot jumps between rooms | rig too small: use the floor rig, raise the anchors, redo the kit's antenna-delay step |
| Mayday never fires / too early | `SANDTABLE_NO_MOTION_RADIUS_M=4` / `SANDTABLE_NO_MOTION_S=12`, or press Mayday |
| Boards will not range | `python -m sandtable.demo --storyboard` (simulated, say so) |

Scout on ESP32 kit boards (future work): the pucks would be flashed as `mesh5`. Their placement rules are in `hardware/SCOUT_LAYOUT.md`, and `python -m sandtable.scout.source_kit --url ws://127.0.0.1:1910/stream --watch` gives the link-shadow live view. The view was exercised only on a fake-board recording (`hardware/recordings/scout_mesh5_fakeboard.ndjson`, SIMULATED).
