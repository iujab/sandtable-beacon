# Tasks and handoff

Last synced from the build server: **Sat Nov 14 2026, 1:22 PM CST**. This file is regenerated on every sync from the two teams' own status files, so it stays current. Agents on the build server are still working; pull again before you start a task.

Deadline: **Sunday Nov 15, 8:00 am Princeton time** (Devpost). Judging 9:30 am to 12:30 pm, in person, 2 minutes per judge.

## Where things stand

| Part | Folder | Status | Demo-ready (simulated) | Being worked on |
|---|---|---|---|---|
| Command board | `sandtable-live-fireground-twin/` | building | 80% | All three sponsor layers run live on this machine for one real building (private; nothing sponsor-derived is published): CoStar record from the kit (construction, sprinklers, 72 current tenants), Matterport twin of a scanned suite (real walls, doorways, sweep graph, Showcase SDK view on localhost), Vexcel imagery (3-6 cm ortho, four obliques, roof attributes; the agent vision tool counts parking stalls on it). The beacon flow ran end to end on the live twin. Now: all-three-live board run and verification, live board UI (obliques, scan view), replay fix and re-record for the public fixture demo, docs, judge. |
| Phones and pucks | `sandtable-beacon-victim-phones/` | building | 78% | simulated flow works end to end (two-puck walk-through, board, feeds, report); guides for the hardware are written; in progress: independent review of the iOS source, recorded command-agent runs, a skeptical-judge review. Nothing has touched hardware yet |

Everything runs today on a simulator and labelled stand-in data. **Nothing has run on the real boards or phones yet.** That is the work below.

## Run it on your laptop first (no hardware)

```bash
git clone https://github.com/iujab/sandtable-beacon.git
cd sandtable-beacon/sandtable-live-fireground-twin
bash run_demo.sh --storyboard      # command board on http://127.0.0.1:1810, plays the exercise hands-free
```

- macOS, Linux and WSL: the script creates `.venv` and installs the requirements on first run (Python 3.10 or newer).
- Windows without WSL: `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`, `.venv\Scripts\python -m sandtable.demo --storyboard`. This path has not been run on Windows yet; tell the agent what breaks.
- The phones part also runs alone: `cd sandtable-beacon-victim-phones && bash run_demo.sh --story` (board on http://127.0.0.1:1885).
- Stop either one with `bash run_demo.sh --stop`.

## Your tasks, in order

These need hands, hardware or accounts, so only you can do them. Each links to the step-by-step guide when one exists.

1. **Five-minute board check.** Install Qorvo's free "Qorvo Nearby Interaction" app from the App Store on an iPhone, power one DWM3001CDK over USB, and see whether the app finds it and shows a distance. If it does, the boards already carry the right firmware: skip step 2.
2. **Flash the boards** with Qorvo's Nearby Interaction firmware if step 1 failed. The firmware file sits behind an email form on qorvo.com, so the agents could not fetch it for you. Guide: `sandtable-beacon-victim-phones/hardware/FLASH.md`.
3. **Build the iPhone app in Xcode** and install it on the iPhone 14 and the iPhone 15 (switch on Developer Mode on both). It has never been compiled: the build server is Linux. Guide: `sandtable-beacon-victim-phones/docs/IOS_BUILD.md`. Paste any compiler errors back to the agent.
4. **Set up the two pucks.** Puck A: a board on a battery bank beside the UNO Q. Puck B: a board powered from the Android phone through a USB-OTG adapter (keep a second battery bank as the fallback). A puck needs power only; the distance comes back through the iPhone app. Guides: `sandtable-beacon-victim-phones/hardware/PUCK.md` and `hardware/BRINGUP.md`.
5. **Start the Beacon server where the phones can reach it** (laptop or UNO Q): `bash run_demo.sh --source live --lan` in `sandtable-beacon-victim-phones/`. Venue Wi-Fi may block device-to-device traffic; a phone hotspot is the usual answer. UNO Q guide: `sandtable-beacon-victim-phones/hardware/UNO_Q.md`.
6. **Place the pucks and tell the board where they are.** The stock firmware cannot range board to board, so tape-measure each puck's position and click it onto the room plan. Put both pucks along the same wall so the room's walls can rule out the mirror-image answer.
7. **First real range.** Open the app on one iPhone, walk the room, and check the ring, the two candidates and the fix on the board against where you actually stand. Write down what you measure: the write-ups currently say "simulated" everywhere and need real numbers.
8. **Keys on your laptop.** The command board runs on your laptop and needs the sponsor keys there; the UNO Q needs none. In `sandtable-live-fireground-twin/`, copy `.env.example` to `.env` (git-ignored) and paste the Vexcel API key and the three Matterport values (API key, API token, SDK key) from the CoStar track message in Discord. Unzip the CoStar hackathon kit somewhere outside the repository and point the board at it. Run `python -m sandtable.adapters.check` to see what is missing. Open the board at `http://localhost:1810`: the Matterport SDK key only works on `localhost`. Guide: `sandtable-live-fireground-twin/docs/TOOLKIT_SWAP.md`. **Never commit keys or anything fetched with them**: the repository keeps the stand-in data, and live sponsor data stays on your machine.
9. **An Anthropic API key** in `.env` (`ANTHROPIC_API_KEY`) for live agent decisions. Without it the command agent replays recorded decisions.
10. **Rehearse the 2-minute demo** from `sandtable-live-fireground-twin/PITCH.md`, on the real hardware, at least three times. Record a fallback video of it working.
11. **Submit.** Devpost text is drafted in `sandtable-live-fireground-twin/DEVPOST.md`. Choose one main track, and tick CoStar, Best Hardware Hack, Best Physical AI and Amazon Practical AI. Add the video (2 minutes at most) and your Discord usernames. This repository must stay public.

## Guides and documents

- [Flash the boards](sandtable-beacon-victim-phones/hardware/FLASH.md)
- [Build and install the iPhone app](sandtable-beacon-victim-phones/docs/IOS_BUILD.md)
- [What a puck is and how to power it](sandtable-beacon-victim-phones/hardware/PUCK.md)
- [First power-up, step by step](sandtable-beacon-victim-phones/hardware/BRINGUP.md)
- [Run the Beacon server on the UNO Q](sandtable-beacon-victim-phones/hardware/UNO_Q.md)
- [What phone ranging can and cannot do](sandtable-beacon-victim-phones/docs/PHONE_FEASIBILITY.md)
- [Privacy](sandtable-beacon-victim-phones/docs/PRIVACY.md)
- [The feed the phones part publishes](sandtable-beacon-victim-phones/docs/BEACON_FEED.md)
- [Pitch for this part](sandtable-beacon-victim-phones/PITCH.md)
- [Write-up for this part](sandtable-beacon-victim-phones/README.md)
- [Write-up for the command board](sandtable-live-fireground-twin/README.md)
- [The 2-minute demo script](sandtable-live-fireground-twin/PITCH.md)
- [Devpost draft](sandtable-live-fireground-twin/DEVPOST.md)
- [Plugging in the CoStar, Matterport and Vexcel toolkit](sandtable-live-fireground-twin/docs/TOOLKIT_SWAP.md)
- [Command-board hardware notes](sandtable-live-fireground-twin/hardware/BRINGUP.md)
- [Why one UWB board cannot scan a room (radar feasibility)](sandtable-live-fireground-twin/docs/RADAR_FEASIBILITY.md)
- [How the idea was chosen](sandtable-live-fireground-twin/BRIEF.md)

## What the two teams report

Taken from each part's `project.json` at sync time.

### Command board (`sandtable-live-fireground-twin/`)

**Being worked on now:** All three sponsor layers run live on this machine for one real building (private; nothing sponsor-derived is published): CoStar record from the kit (construction, sprinklers, 72 current tenants), Matterport twin of a scanned suite (real walls, doorways, sweep graph, Showcase SDK view on localhost), Vexcel imagery (3-6 cm ortho, four obliques, roof attributes; the agent vision tool counts parking stalls on it). The beacon flow ran end to end on the live twin. Now: all-three-live board run and verification, live board UI (obliques, scan view), replay fix and re-record for the public fixture demo, docs, judge.

**Left to build (agents):**

- Two-puck scene on the board: click-to-place pucks, two rings per phone, pruned mirror candidate, 'cannot choose' state (board builder, in progress)
- Hands-free beacon script on a frozen two-puck Beacon run so recorded agent turns replay exactly (server builder, in progress)
- Record the agent's turns for the beacon, scout and mayday scripts with a real model: bash scripts/record_all.sh, then bash run_tests.sh and python scripts/check_replay.py
- README, DEVPOST, PITCH re-cut around Beacon (write-ups builder, in progress)
- Independent skeptical-judge review and fixes
- Final vendoring of lane L16's Beacon module: python scripts/vendor_beacon.py

**Left to do with real hardware (you):**

1. 1. (Lane L16) Bring up the two DWM3001CDK pucks and the iPhone app with the Beacon module: follow sandtable-beacon-victim-phones/hardware (vendored here under vendor/beacon/hardware). Works when http://localhost:1885/healthz answers with source 'pucks'.
2. 2. Start this board next to it (about 2 min): python -m sandtable.demo --beacon-url http://localhost:1885 (Windows: .venv\Scripts\python -m sandtable.demo --beacon-url http://localhost:1885). Works when the board at http://localhost:1810/ shows the Beacon badge as connected and not SIM.
3. 3. Tape-measure the two pucks from two walls of the demo room, click 'Place pucks' and click them onto the room plan (fine-tune the metres in the dialog). Works when P1 and P2 show on the plan as 'placed by hand'.
4. 4. Open the occupant app on the two iPhones. Works when each phone appears as two rings crossing at two points; where the twin's walls remove the mirror point it becomes a fix, otherwise the board says it cannot choose.
5. 5. Click 'Dispatch alarm' before step 4 so the agent has the pre-plan; with ANTHROPIC_API_KEY set and LLM_BACKEND=anthropic the agent decides live, otherwise the labelled fallback policy routes the crew.
6. 6. Optional: the crew phone page http://localhost:1810/crew.html?unit=RIT on the Android phone (adb reverse tcp:1810 tcp:1810).
7. 7. No hardware for Scout (radar) or Crews (tags): both run simulated only on these boards.

**Needed from you:**

- On the laptop: unzip the CoStar kit OUTSIDE the repository and set COSTAR_KIT_DIR; copy .env.example to .env and paste the Matterport token id/secret, SDK key and the Vexcel key; open the board at http://localhost:1810 (the Matterport SDK key only works on localhost). Details: docs/TOOLKIT_SWAP.md (being rewritten)
- ANTHROPIC_API_KEY for live agent decisions with real phones and live sponsor data (recorded turns only cover the fixture-mode simulator scene)
- Tape-measured positions of the two pucks in the demo room, and which room of the twin the demo room stands in for

**Finished:**

- Crew tracking product with pre-plan agent, mayday exercise, replayed real-model runs (morning)
- Real open-data fixture for 7325 Medical Center Dr, footprint-fitted synthetic twin, spatial engine
- Scout radar engine in simulation (multistatic UWB: self-location, registration into the twin, breathing-person detection, twin-mismatch flags)
- GeoJSON and Cursor-on-Target feeds
- Beacon link: consumes lane L16 feed, phones as rescue targets, accountability against the building record, report
- Phones layer on the board (ring, candidates, fix, zone, device counts), mode switch Beacon | Scout | Crews
- Radar feasibility write-up with sources; puck enclosure (DWM3001CDK + cell, UNO Q host brick)

**Known limitations:**

- No phone and no puck has been in the loop of any number reported; the live path (stock Qorvo firmware, lane L16's iOS app) is written, and the real range error of an iPhone against these boards is unmeasured
- Opt-in only: a phone without the app is never ranged or identified; the most a puck could give is a Bluetooth device count, and devices are not people; the app must stay open with the screen on as built
- Two pucks often leave two candidates; the twin removes one only when the geometry allows
- All three sponsor layers are fixtures (real public data and NAIP imagery, synthetic interior); the live adapters were never called; the walls that prune a candidate are plausible walls, not surveyed ones
- Expected head counts and life hazards are estimates and inferences from the record
- Agent turns for the Beacon and Scout exercises fall back to a labelled deterministic policy until a real model run is recorded
- Scout (radar) and Crews (tags) are simulation-only on these boards; the radar simulator was written alongside its detector
- One floor modelled; feeds and the app link have no authentication; not a life-safety product


### Phones and pucks (`sandtable-beacon-victim-phones/`)

**Being worked on now:** simulated flow works end to end (two-puck walk-through, board, feeds, report); guides for the hardware are written; in progress: independent review of the iOS source, recorded command-agent runs, a skeptical-judge review. Nothing has touched hardware yet

**Left to build (agents):**

- Fix what the independent iOS review finds; then your first Xcode build will tell us the rest (paste the errors back)
- Recorded command-agent runs for the walk-through (so the board shows a real model's plan, not the labelled fallback)
- Fix what the skeptical-judge review finds
- After the first real ranges: tune the noise model and the exercise scale handling to what the boards really do

**Left to do with real hardware (you):**

1. 1. (5 min) hardware/BRINGUP.md step 1: power one DWM3001CDK and see whether it already ranges with Qorvo's free App Store app 'Qorvo Nearby Interaction'. If yes, skip flashing for that board.
2. 2. (15 to 30 min incl. the email wait) BRINGUP step 0.1: download Qorvo's Nearby Interaction firmware (DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex, or the QANI hex in the DW3_QM33 SDK) from qorvo.com/products/d/da008212 or /da008582; use a work or school email. Keep the .hex in data/ or outside the repository.
3. 3. (5 min first board, 2 min each after) hardware/FLASH.md: micro-USB DATA cable into port J9, SEGGER J-Flash Lite, device NRF52833_XXAA, erase, program, reset.
4. 4. (5 min) BRINGUP step 3: prove one board and one iPhone with Qorvo's app; compare the distance with a tape measure.
5. 5. (10 min) BRINGUP step 4: hotspot on the Android phone; laptop and both iPhones join it; `bash run_demo.sh --source live --lan` on the laptop.
6. 6. (30 to 45 min) docs/IOS_BUILD.md and BRINGUP step 5: open apps/ios/SandtableBeacon.xcodeproj, choose your team, run on the iPhone 14; scan the board's QR code to fill in the server address; press Trapped: a ring appears on the board. If it does not compile, paste the errors back.
7. 7. (20 min) BRINGUP step 6: puck A on a battery bank beside the UNO Q; puck B on the Android phone through USB-OTG into port J9; run the 10-minute soak test (does the phone or the bank cut the power?).
8. 8. (10 min) BRINGUP step 7 and hardware/LAYOUT.md: tape the rectangle, stand both pucks just inside the door-wall edge, click them onto the plan, set the exercise scale: one position.
9. 9. (10 min) BRINGUP step 8: second victim on the iPhone 15: 'Take turns' on both phones, or two more boards so each puck position has one board per phone.
10. 10. (15 min) BRINGUP steps 9 and 10: rehearse the run-through twice and read the fallback ladder.

**Needed from you:**

- Qorvo Nearby Interaction firmware for the DWM3001CDK (step 2 above): only you can submit Qorvo's download form
- SEGGER J-Link software on the Mac or laptop (free download, no account)
- Xcode with your Apple ID; Developer Mode on both iPhones (Settings > Privacy & Security > Developer Mode)
- A USB-C OTG adapter for the Android phone, two micro-USB DATA cables, one battery bank (a second as the fallback for puck B), tape and a tape measure
- Tell us what Qorvo's App Store app shows with your boards as they are now (advertising or not, distance or not)
- CoStar toolkit and keys if they exist (none on the build machine): the roster comparison runs on a public-data fixture until then

**Finished:**

- Simulated end to end: `bash run_demo.sh --story` plays ring -> cannot choose -> puck slid to the door wall -> one position -> second phone -> accountability, on the board at port 1885
- Live mode: `bash run_demo.sh --source live --lan` accepts the iPhone app's reports at POST /api/ingest (tested with example batches, not with a phone)
- Guides: hardware/FLASH.md, hardware/BRINGUP.md (first power-up, two-puck layout, networking, two phones, fallback ladder), hardware/LAYOUT.md, hardware/PUCK.md, hardware/UNO_Q.md, docs/IOS_BUILD.md
- iOS occupant app source and Xcode project in apps/ios (uncompiled; syntax-parsed; under independent review)
- Locator and tracker, two-puck layout, visible dismissal of a mirror spot on stated evidence
- Evaluation with figures: results/eval_two_puck.json (how often two pucks give one answer), eval_layouts.json (two boards on one device), eval_beacon.json (by method and puck count)
- Tenant roster against phones: accountability table, priorities, where the next puck goes; command agent and incident report
- Puck host for the UNO Q (python -m beacon.host): heartbeat, experimental debug-channel reader, optional Bluetooth device count; tested against stubs only
- Feed contract for the command board: docs/BEACON_FEED.md (v1 plus additions A1 to A9); research with sources in docs/research/; docs/PHONE_FEASIBILITY.md; docs/PRIVACY.md

**Known limitations:**

- Nothing has run on hardware. Every result is simulated, with the noise model stated next to it.
- The iOS app is uncompiled Swift source (written on a machine with no Mac); expect compiler errors on the first build.
- Qorvo's firmware is not in this repository and cannot be: the owner downloads it from Qorvo.
- Bring-up guides are written from documentation and forum reports, not from a board.
- With Qorvo's stock firmware one board serves one phone at a time, and the range is read by the phone app, not by the puck: the phone needs a network path to the server and the app must stay open.
- An iPhone 14 or 15 reports distance but no direction to an accessory.
- A phone whose owner installed nothing cannot be ranged or identified; at most a rough count of Bluetooth devices nearby.
- The building record is a public-data fixture (not CoStar data) and the twin is a synthetic interior (not a Matterport scan); expected head counts are estimates from floor area and building-code load factors.
- Not a life-safety product.


## Your questions, answered by the teams

**Can a UWB board range a victim's phone over UWB at all?**

Yes, with exactly your boards: a Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware ranges an iPhone 11 or later through Apple's accessory protocol, once the person has opened our app. Both your iPhones give distance (about 60 readings a second); neither gives direction to an accessory. The app reads the distance and reports it; the board itself only logs it on its debug channel.

**Can it be done cold, to a phone whose owner installed nothing?**

No. An iPhone's UWB radio only answers a session started by an app with the user's permission. Cold, a radio can only count Bluetooth advertisements nearby: a rough band of devices by signal strength, no identity, and devices are not people. The stock puck firmware cannot even do that; the UNO Q's own radio can (written, never run).

**One puck or two for angle?**

Two pucks, as you chose, give one answer if you place them right. One puck gives a ring; two give two mirror spots across the line between the pucks. Put both pucks just inside the wall the door is in (35 cm inside, about 4 m apart). Simulated over 800 phone positions in the room: one position in 77% of cases (48% by the twin's walls alone, 29% after the occupant's stated suite, shown as dismissed), median error 0.26 m, a wrong position in 1.1%; the rest stay a ring because only one puck reached the phone. Pucks dropped mid-room: one position in only 18%, and the board says 'cannot choose' in 57%. Better if you have it: a third puck gives one position in 88% with 0.20 m median error and no assumption. An iPhone 14 or 15 reports no direction to an accessory, so do not count on the phone's bearing.

**Will the phone keep answering with the screen off?**

Not as built tonight: keep the app open (it keeps the screen awake). Two ways exist and are not built: a Bluetooth-paired background session in which only the puck gets the measurements, and, on iOS 18.4 or later, a Live Activity that lets the app keep ranging.

**Can we put both UWB boards on one device we throw in?**

It works poorly. The boards have one antenna each, so bearing can only come from the difference of two ranges. Simulated: at 10, 30 and 50 cm spacing the bearing error is 22, 17 and 14 degrees (median), the sideways error 1.7, 1.3 and 1.3 m, the mirror ambiguity always remains, and the locator never publishes a position, only a ring. Two separate pucks 3 m apart: 5 degrees and 0.38 m sideways.

**Two pucks: one on a battery bank with the UNO Q, one powered by the Android phone?**

Yes, that is now the plan. Each board needs only 5 V. Puck A: battery bank, beside the UNO Q (which can run the server). Puck B: the Android phone over a USB-OTG adapter into the board's J-Link port (that port's chip shows up as a USB device, so the phone keeps the power on); keep a second bank as the fallback because some phones cut power to quiet devices. The phone cannot read the board (the stock firmware prints nothing on serial), and does not need to: the range returns through the iPhone app. The same phone can be the Wi-Fi hotspot for the laptop and both iPhones.

**What does a puck need?**

Power only (5 V on either micro-USB port). The range comes back through the iPhone app over Wi-Fi. Stand the board upright, at least 15 cm off the floor, component-free side toward the phone. Puck positions are measured with a tape and clicked onto the plan: the stock firmware cannot range board to board.

**Two victims' iPhones on two pucks at once?**

One iPhone can range both pucks at once. One board, on Qorvo's stock firmware, serves only one phone at a time (forum reports). So for two victims either the phones take turns (a switch in our app: each phone ranges a board for about 4 seconds and releases it; untested), or each puck position carries two boards, one per phone (you have five boards; continuous ranging, no turn-taking). Prove one phone on both pucks first.

**Where does the server run at the table, and how do the iPhones reach it?**

On the laptop, with the laptop and both iPhones joined to a phone hotspot (venue Wi-Fi usually blocks device-to-device traffic); the Android phone powering puck B can be that hotspot. The board shows a QR code; scanning it with the iPhone camera opens our app with the server address filled in. Fallbacks: the server on the UNO Q on the same hotspot, or this machine over the tailnet if the iPhones run the tailnet client.

**[Phones] Can a UWB board range a victim's phone over UWB at all?**

Yes, with exactly your boards: a Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware ranges an iPhone 11 or later through Apple's accessory protocol, once the person has opened our app. Both your iPhones give distance (about 60 readings a second); neither gives direction to an accessory. The app reads the distance and reports it; the board itself only logs it on its debug channel.

**[Phones] Can it be done cold, to a phone whose owner installed nothing?**

No. An iPhone's UWB radio only answers a session started by an app with the user's permission. Cold, a radio can only count Bluetooth advertisements nearby: a rough band of devices by signal strength, no identity, and devices are not people. The stock puck firmware cannot even do that; the UNO Q's own radio can (written, never run).

**[Phones] One puck or two for angle?**

One puck gives a ring. Two give two candidate points, mirror images across the line between the pucks; the twin's walls often delete one. Three pucks not in a line give a position. The phone's own direction would turn one ring into a point, but an iPhone 14 or 15 does not report direction to an accessory without camera assistance (a moving phone that can see), so do not count on it. Simulated numbers follow when the evaluation finishes.

**[Phones] Will the phone keep answering with the screen off?**

Not as built tonight: keep the app open (it keeps the screen awake). Two ways exist and are not built: a Bluetooth-paired background session in which only the puck gets the measurements, and, on iOS 18.4 or later, a Live Activity that lets the app keep ranging.

**[Phones] Can we put both UWB boards on one device we throw in?**

It works poorly. Bearing from two single-antenna boards comes from the difference of two ranges: at 10 cm spacing it is useless, at 30 cm about 14 to 38 degrees of error, at 50 cm about 8 to 23 degrees, and the mirror ambiguity remains. Simulated numbers follow.

**[Phones] Or one puck on the Android phone and one on the UNO Q, two separately powered pucks?**

Two separate pucks a few metres apart are far better than two boards on one device: tens of centimetres, with two candidates that the twin can often cut to one. The Android phone is only worth using as a power source: the stock firmware prints nothing a phone could read. Recommended for the live demo: three separate pucks on battery banks, 2 to 3 m apart, not in a line; the UNO Q can power one and run the server.

**[Phones] What does a puck need?**

Power only (5 V on either micro-USB port). The range comes back through the iPhone app over Wi-Fi. Stand the board upright, at least 15 cm off the floor, component-free side toward the phone. Puck positions are measured with a tape and clicked onto the plan: the stock firmware cannot range board to board.

**[Radar] Is one UWB sensor enough to scan a room?**

No. A single DWM3001CDK board cannot hear its own echoes. Qorvo's own staff say the module cannot transmit and receive at the same time. The smallest radar is two boards: one transmits, the other records the echoes that arrive after the direct pulse.

**[Radar] Why not?**

Its antenna is switched either to the transmitter or to the receiver. One frame lasts at least 32.6 µs, but a wall 3 m away answers after 20 ns, while the board is still transmitting. The receiver only switches on after the frame ends, by which time every room echo has already passed.

**[Radar] How do multiple boards fix it?**

One board transmits and the others listen. Each pair turns an echo's delay into an ellipse with the two boards as foci. More boards give more ellipses at different angles, and the target sits where they cross. The boards first range each other to learn their own positions, because every ellipse needs known foci.

**[Radar] How many are needed, and what does each added board buy?**

Two boards give presence and breathing but no position. Three give a 2-D position (3 ellipses), four add a redundant check (6), and five add better angles and outlier rejection (10). In the sample throw, the area that fits every pair shrinks from 4.01 m² (two boards) to 0.40 m² (three) and 0.15 m² (five).

**[Radar] What is the 'map', and what is it not?**

It is a coarse echo map: a few strong reflectors, a rough outline, what moved, and where the room disagrees with the stored twin. It is not a LiDAR-grade model, because echoes about 0.3 to 0.6 m apart merge and walls reflect like mirrors. The twin is the map; the boards add people, breathing and changes.

**[Radar] Why does this beat cameras and LiDAR in smoke, and where does it not?**

Smoke particles (0.01 to 1 µm) scatter light and LiDAR strongly but are negligible to a 46 mm radio wave. In fire tests, LiDAR erred below 4 m visibility while radar stayed within 1%. Thermal imagers also see through smoke and remain better at seeing fire. Flames, wet walls and metal cost radio signal; a still, non-breathing person stays invisible.

**[Radar] What is the biggest open risk?**

Signal strength. The sample victim's echoes arrive 25 to 36 dB below the direct pulse. In real recorded data, the empty room's own multipath already flickers at about 22 dB below. Expect detection within a few metres of the pucks. A breathing person across a 9.5 m room is unproven until measured.

**[Radar] What is simulated today?**

All of the radar. No board has run it, and the reused echo-reading firmware targets ESP32 boards, not the DWM3001CDK. Scout is a secondary, simulation-only mode of Sandtable Beacon. The timing and geometry arithmetic is exact; detection performance is not measured. On the one real recording available, the reused tracker followed clutter, not the person.


## Working alongside the agents

- The build server re-syncs this repository every 15 minutes while the agents work. Each sync is one commit.
- If you change files here, commit and push them, then tell the agent. A sync stops by itself when it sees commits it did not make, so that your changes are merged into the agents' trees and not overwritten.
- Never commit keys, the CoStar toolkit, or `.env` files.
