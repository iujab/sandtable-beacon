# Tasks and handoff

Last synced from the build server: **Sun Nov 15 2026, 9:57 AM CST**. This file is regenerated on every sync from the two parts' own status files.

Deadline: **Sunday Nov 15, 8:00 am Princeton time** (Devpost). Judging 9:30 am to 12:30 pm, in person, 2 minutes per judge.

**How to run anything, at any level (laptop demo, live building data, real boards and phones): [RUNBOOK.md](RUNBOOK.md).**

## Where things stand

| Part | Folder | Status | Demo-ready (simulated) | Being worked on |
|---|---|---|---|---|
| Command board | `sandtable-live-fireground-twin/` | partial | 95% | Software finished for the stand-in demo and the private live run, after a full sweep: the board was rebuilt around the responder's story (know before you go, beacon on scene, command), the backend cleaned up with 8 bugs fixed, and the puck spacing measured by the boards now positions the pucks on the plan. Hands-free beacon, scout and mayday exercises replay recorded real model turns with zero fallbacks (stand-in), and the beacon exercise does on the live building too; 198 tests pass. Not done: no phone has been ranged by our app on real boards yet. |
| Phones and pucks | `sandtable-beacon-victim-phones/` | partial | 92% | Sun 15 Nov: the pucks' spacing is now measured, not guessed: our board-to-board SS-TWR firmware was measured on two of the owner's boards; the server takes the spacing (POST /api/pucks/baseline) and places a pair from it (POST /api/pucks/anchor), every puck says how it was placed, and the board flags a placement that disagrees with the measurement. Code-quality sweep with bug fixes. 334 tests pass. The host's baseline mode and the anchor flow have not run against the boards (the UNO Q was off). The demo left running on port 1885 was started on Saturday and runs that code until it is restarted. |

The repository runs on a simulator and labelled stand-in data. The command board has also run privately on live CoStar, Matterport and Vexcel data for one real building; the keys and that data stay off this repository. On real hardware so far: two DWM3001CDK boards ranged each other with the board-to-board firmware, and the owner's iPhone connected to each board on Qorvo's phone firmware in Qorvo's app. No phone position from real boards has been measured yet.

## Already done on the hardware

- The two boards on the UNO Q carry Qorvo's Nearby Interaction (phone) firmware; the owner's iPhone connected to both in Qorvo's app (2026-11-15).
- Board-to-board ranging was measured on the same two boards before they were switched to the phone firmware (see the board-to-board guide below).

## Your tasks, in order

These need hands, hardware or accounts. Section numbers refer to [RUNBOOK.md](RUNBOOK.md).

1. **Build the iPhone app** in Xcode and run it on both iPhones (Developer Mode on). A Mac-based agent is on it; what it needs is in `sandtable-beacon-victim-phones/docs/IOS_HANDOFF.md`. Paste compiler errors back. (Runbook 3.8)
2. **Choose the beacon:** two boards (measure the spacing once with the board-to-board firmware, or tape-measure it, then flash the phone firmware) or four boards (each puck pairs a phone board with a spacing board). (Runbook 3.2)
3. **Measure the spacing:** flash the board-to-board firmware on the spacing boards and send the measured spacing to the Beacon server. (Runbook 3.3 and 3.5)
4. **Network and Beacon server:** a phone hotspot every device joins; the Beacon server in live mode on the laptop or the UNO Q. (Runbook 3.4 and 3.6)
5. **Command board on your laptop**, pointed at that server, with the real building's data if you have the two private files. (Runbook 3.7 and Level 2)
6. **Place the beacon and take the first real ranges.** Mark where it landed on the plan, open the app on a phone with SOS on, and compare the board's fix with where the phone actually is. **Write down the numbers**: every phone-position figure in the write-ups is simulated until then. (Runbook 3.9 and 3.10)
7. **An Anthropic API key** in `.env` (`ANTHROPIC_API_KEY`) and `LLM_BACKEND=anthropic`, so the command agent decides live on real phones. Without it, only the recorded exercise has recorded turns; anything else runs on a labelled fallback policy.
8. **Rehearse the 2-minute demo** from `sandtable-live-fireground-twin/PITCH.md` at least three times, and record a fallback video.
9. **Submit.** The Devpost draft is `sandtable-live-fireground-twin/DEVPOST.md`. Choose one main track; tick CoStar, Best Hardware Hack, Best Physical AI and Amazon Practical AI; add the video (2 minutes at most) and your Discord usernames. This repository must stay public.

## Guides and documents

- [Runbook: how to run everything, from the laptop demo to the real boards](RUNBOOK.md)
- [Range the two boards to each other (spacing and on-landing calibration)](sandtable-beacon-victim-phones/hardware/BOARD_TO_BOARD.md)
- [Board-to-board ranging firmware](sandtable-beacon-victim-phones/hardware/twr_firmware/README.md)
- [How the board knows which room the beacon and the people are in](sandtable-beacon-victim-phones/docs/ROOM_LOCATION.md)
- [iPhone app: build it against the real pucks](sandtable-beacon-victim-phones/docs/IOS_HANDOFF.md)
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

## What the two parts report

Taken from each part's `project.json` at sync time.

### Command board (`sandtable-live-fireground-twin/`)

**Being worked on now:** Software finished for the stand-in demo and the private live run, after a full sweep: the board was rebuilt around the responder's story (know before you go, beacon on scene, command), the backend cleaned up with 8 bugs fixed, and the puck spacing measured by the boards now positions the pucks on the plan. Hands-free beacon, scout and mayday exercises replay recorded real model turns with zero fallbacks (stand-in), and the beacon exercise does on the live building too; 198 tests pass. Not done: no phone has been ranged by our app on real boards yet.

**Left to build (agents):**

- Optional, with an Anthropic key on the laptop: run the live exercise with LLM_BACKEND=anthropic (written, never exercised: no key on the build machine)
- Optional: re-run python scripts/vendor_beacon.py if lane L16 changes its module again, then bash run_tests.sh

**Left to do with real hardware (you):**

1. 1. (Lane L16) Bring up the two DWM3001CDK pucks and the iPhone app with the Beacon module: follow vendor/beacon/hardware/BRINGUP.md (lane L16's guide, vendored; flashing in vendor/beacon/hardware/FLASH.md, the iOS app in vendor/beacon/docs/IOS_BUILD.md). Works when http://localhost:1885/healthz answers with source 'pucks'.
2. 2. Start this board next to it (about 2 min): python -m sandtable.demo --beacon-url http://localhost:1885 (Windows: .venv\Scripts\python -m sandtable.demo --beacon-url http://localhost:1885). Works when the board at http://localhost:1810/ shows the Beacon badge as connected and not SIM.
3. 3. Tape-measure the two pucks from two walls of the demo room, click 'Place pucks' and click them onto the room plan (fine-tune the metres in the dialog). Works when P1 and P2 show on the plan as 'placed by hand'.
4. 4. Open the occupant app on the two iPhones. Works when each phone appears as two rings crossing at two points; where the twin's walls remove the mirror point it becomes a fix, otherwise the board says it cannot choose.
5. 5. Click 'Dispatch alarm' before step 4 so the agent has the pre-plan; with ANTHROPIC_API_KEY set and LLM_BACKEND=anthropic the agent decides live, otherwise the labelled fallback policy routes the crew.
6. 6. Optional: the crew phone page http://localhost:1810/crew.html?unit=RIT on the Android phone (adb reverse tcp:1810 tcp:1810).
7. 7. No hardware for Scout (radar) or Crews (tags): both run simulated only on these boards.

**Needed from you:**

- On the laptop (about 15 min, docs/TOOLKIT_SWAP.md 'Laptop setup'): clone, venv, pip install -r requirements.txt, copy the private .env, python scripts/pack_live_bundle.py unpack <the private bundle zip>, python -m sandtable.adapters.check --live, then SANDTABLE_DATA_MODE=live LLM_BACKEND=replay python -m sandtable.demo and press Exercise. Open the board at http://localhost:1810 (the Matterport SDK key only works on localhost). The bundle and the .env travel by hand, never through the repository
- The hardware run (left_hardware steps): two pucks, two iPhones, lane L16's module; until then say 'not run on hardware'
- ANTHROPIC_API_KEY for live agent decisions with real phones (recorded turns cover only the recorded exercises)
- Tape-measured positions of the two pucks in the demo room, and which room of the twin the demo room stands in for

**Finished:**

- Board, reflex layer, agent with tools, report page, GeoJSON and Cursor-on-Target feeds; three modes (Beacon phones, Scout radar, Crews tags) on one board
- Stand-in data for the public repository: real open data for a Los Angeles medical office building, footprint-fitted synthetic twin, NAIP imagery
- Beacon link to lane L16's module (vendored at its final commit): phones as rescue targets, two-puck story (the twin rules out a mirror place; a crew's search rules out the other), accountability against the record, true closing line
- Recorded real model turns for the beacon, scout and mayday scripts; all replay hands-free with zero fallbacks on a real-time server (results/replay_check_*_auto_3A2T.json)
- Live adapters for CoStar (data kit), Matterport (Model API, Showcase SDK view) and Vexcel (API v2), run privately on one real building; privacy guard and publish check keep every sponsor-derived byte out of the tree
- Live accountability: the record against the phones, the storey's other tenancies as not covered; private recorded hands-free live run, replayed four times with zero fallbacks; private laptop bundle and laptop steps (docs/TOOLKIT_SWAP.md)
- Scout radar engine in simulation with ablation; radar feasibility write-up with sources; puck enclosure design
- Independent judge review and fixes; README, PITCH, DEVPOST truth pass (what ran live, what is stand-in, what the hardware does and does not do)
- Board rebuilt around the responder's story: know before you go (building facts, floor-by-floor tenant stack with life hazards and people on record, access, aerial, scanned interior), beacon on scene (where it landed, how each puck was placed, measured or hand spacing, every phone with its geometry as measured, the opt-in note, per-floor accountability), command (agent, radio, crews, report); the plan is the hero, every layer carries its source stamp, status by word and shape, tablet and phone widths, night theme
- Place-the-beacon guide: pick the floor, tap where P1 landed, tap toward P2; P2 snaps to the spacing the pucks measured (Beacon feed A12)
- Backend sweep: 8 bugs fixed with tests (a reset now ends the old incident's agent turn and pre-plan, a resolved mayday no longer leaks into the next report, lossless board catch-up, safe teleports and exercise starts, orphaned CLI turns killed, unique hazard ids); faster anchor plan, twin geometry and beacon polling
- The relay to the Beacon module now forwards how each puck was placed and its Bluetooth name; the module keeps a board's name when its puck is re-placed by position (both bugs found while integrating, each with a test)
- Final verification on the integrated tree: replay checks beacon, scout and mayday (stand-in) and beacon (live) all with zero fallback runs; privacy scan passes

**Known limitations:**

- No phone and no puck has been in the loop of any number reported; the live path (stock Qorvo firmware, lane L16's iOS app) is written, and the real range error of an iPhone against these boards is unmeasured
- Opt-in only: a phone without the app is never ranged or identified; the most a puck could give is a Bluetooth device count, and devices are not people; the app must stay open with the screen on as built
- Two pucks often leave two candidates; the twin removes one only when the geometry allows
- The public repository runs on stand-in data for all three sponsor layers (real public data and NAIP imagery for a different building, a synthetic interior whose walls are plausible, not surveyed). With the owner's keys and the CoStar data kit the same code ran live and privately on Nov 14 2026 on one real Los Angeles office building (CoStar record, a Matterport scan of one suite, Vexcel imagery); none of that data is published, the scan covers one suite, and its georeference is about 94 m from the record's point (flagged, not corrected)
- Expected head counts and life hazards are estimates and inferences from the record
- Agent turns in the hands-free demo are replays of recorded real model runs (beacon, scout and mayday scripts: every turn replayed, 0 fallbacks, results/replay_check_*_auto_3A2T.json); anything outside the recorded scene runs on a live model when one is configured, otherwise on a labelled deterministic policy that always finishes the exercise
- Scout (radar) and Crews (tags) are simulation-only on these boards; the radar simulator was written alongside its detector
- One floor modelled; feeds and the app link have no authentication; the board keeps what it received from phones for the incident report (stop-sharing deletion and retention limits exist only in lane L16's module); not a life-safety product


### Phones and pucks (`sandtable-beacon-victim-phones/`)

**Being worked on now:** Sun 15 Nov: the pucks' spacing is now measured, not guessed: our board-to-board SS-TWR firmware was measured on two of the owner's boards; the server takes the spacing (POST /api/pucks/baseline) and places a pair from it (POST /api/pucks/anchor), every puck says how it was placed, and the board flags a placement that disagrees with the measurement. Code-quality sweep with bug fixes. 334 tests pass. The host's baseline mode and the anchor flow have not run against the boards (the UNO Q was off). The demo left running on port 1885 was started on Saturday and runs that code until it is restarted.

**Left to build (agents):**

- Run the host's baseline mode against the two TWR boards once (hardware/BOARD_TO_BOARD.md step 2) and record what it posts
- Calibrate the TWR antenna delay at 1.000 m with a tape (hardware/twr_firmware/README.md); until then the spacing carries an unknown constant offset
- Your first Xcode build of apps/ios will tell us what the syntax check could not: paste the compiler errors back (docs/IOS_BUILD.md section 11 lists the likeliest places)
- After the first real ranges: replace the simulated noise model with what an iPhone 14 and 15 really measure against these boards, and re-run scripts/eval_beacon.py and scripts/honesty_check.py
- Decide at the table how two phones share two boards (take turns in the app, or two boards per puck position) and tell us which, so the walk-through and the pitch match
- Not built: background ranging (Live Activity or paired background session), puck firmware that reports ranges itself, one firmware that ranges phones and the other puck at once (FiRa multi-session), an Android occupant app

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
11. 11. (30 min, optional, after step 7) hardware/BOARD_TO_BOARD.md: flash the TWR firmware on both boards (OpenOCD form that worked on the UNO Q), post the spacing with the host, anchor P1 and tap toward P2 on the board, then flash QANI back WITHOUT moving the boards. No TWR boards: tape-measure the spacing and post it with source manual

**Needed from you:**

- Qorvo Nearby Interaction firmware for the DWM3001CDK (step 2 above): only you can submit Qorvo's download form
- SEGGER J-Link software on the Mac or laptop (free download, no account)
- Xcode with your Apple ID; Developer Mode on both iPhones (Settings > Privacy & Security > Developer Mode)
- A USB-C OTG adapter for the Android phone, two micro-USB DATA cables, one battery bank (a second as the fallback for puck B), tape and a tape measure
- Tell us what Qorvo's App Store app shows with your boards as they are now (advertising or not, distance or not)
- CoStar extract and sponsor keys have arrived and stay private: Beacon uses them only through the command board's private live bundle (BEACON_BUNDLE_DIR); nothing to do for Beacon

**Finished:**

- Board to board (feed A12): our SS-TWR firmware for the DWM3001CDK, measured on the owner's two boards on 15 Nov (about 4.9 ranges a second, 4 to 6 cm spread, antenna delays uncalibrated; results/twr_measured_2026-11-15.json, hand-transcribed by the bench agent). Kept as a script of our changes against the public starter project, because the Decawave examples it derives from carry no redistribution licence; prepare.py rebuilds and hash-checks app.c; the build path compiles and links for both roles (hardware/twr_firmware/build_logs/)
- Server: POST /api/pucks/baseline (latest spacing per pair), POST /api/pucks/anchor (anchor one puck, tap toward the other, the other snaps to the measured spacing), placed_by on every puck (hand | baseline | layout | sim), a warning when a placed pair disagrees with its measured spacing by more than 3 sigma + 0.2 m; none of it reaches the agent's prompts; documented in docs/BEACON_FEED.md A12 and docs/CONTRACTS.md 3.8; shown on the board
- Puck host: --baseline-to P2 reads the TWR firmware's RTT lines, keeps a robust spacing (median, MAD sigma, outliers dropped, a minimum sample count) and posts it; a board-to-board range is never sent as a phone range; --twr-offset-m for a measured bias
- hardware/BOARD_TO_BOARD.md (the procedure, the three design options, measured versus untested); docs/ROOM_LOCATION.md (where floor and room come from); BRINGUP 7.3, PUCK, UNO_Q and PHONE_FEASIBILITY corrected: board to board is impossible with QANI and possible with our firmware
- Code-quality sweep: 14 genuine bugs fixed, each with a regression test (server: malformed room, floor, n or seed gave HTTP 500; session: the stub tracker could not forget a moved puck, one non-object observation rejected a whole batch, ingest de-duplication was cleared wholesale; locator: the wall cache could serve another puck's walls; simulator: a slowly dragged phone stayed still, battery drain depended on the clock origin; report counted one state twice and kept unbounded memory; agent lost its plan on a list-valued target; tools resolved ring rooms by label; CoT crashed on half a position; a restarted walk-through lost its running flag; host printed a traceback for a bad --rtt), ring estimates about 25% faster and the accountability table's routes cached (identical outputs); recorded agent runs still replay identically
- Runs unchanged on another building: BEACON_BUNDLE_DIR (or BEACON_TWIN_JSON / BEACON_SITE_JSON) points the module at a twin and record kept outside the repository; verified on the command board's private live bundle (structure only, no data copied) and by tests on a synthetic external bundle
- Simulated end to end: `bash run_demo.sh --story` plays ring -> cannot choose -> puck slid to the door wall -> one position -> second phone -> accountability, on the board at port 1885
- Independent review and fix round: a two-puck position is published only when its mirror spot is explained; ranges are forgotten when a puck moves; stale phones are never dots; the simulator gives only what a real iPhone gives (distance); scripts/honesty_check.py hunts for confident wrong positions (9 scenes, passes)
- Command agent: the walk-through shows a recorded real model run (Claude Sonnet 5.5, replayed); other situations get a labelled deterministic fallback unless a live model is configured
- Live mode: `bash run_demo.sh --source live --lan` accepts the iPhone app's reports at POST /api/ingest (tested with example batches, not with a phone)
- Guides: hardware/FLASH.md, hardware/BRINGUP.md (first power-up, two-puck layout, networking, two phones, fallback ladder), hardware/LAYOUT.md, hardware/PUCK.md, hardware/UNO_Q.md, docs/IOS_BUILD.md
- iOS occupant app source and Xcode project in apps/ios (uncompiled; syntax-parsed; under independent review)
- Locator and tracker, two-puck layout, visible dismissal of a mirror spot on stated evidence
- Evaluation with figures: results/eval_two_puck.json (how often two pucks give one answer), eval_layouts.json (two boards on one device), eval_beacon.json (by method and puck count)
- Tenant roster against phones: accountability table, priorities, where the next puck goes; command agent and incident report
- Puck host for the UNO Q (python -m beacon.host): heartbeat, experimental debug-channel reader, optional Bluetooth device count; tested against stubs only
- Feed contract for the command board: docs/BEACON_FEED.md (v1 plus additions A1 to A9); research with sources in docs/research/; docs/PHONE_FEASIBILITY.md; docs/PRIVACY.md
- Checked on the owner's UNO Q over SSH on 14 Nov: the requirements install without sudo in 83 s; the simulated walk-through runs to the end there (both simulated phones located; the server uses about half of one core); 257 of 261 tests pass there, the 4 failures being time limits written for a faster machine; a USB-C hub with two DWM3001CDK boards enumerates as two serial ports. Live mode and the puck host were not run there (hardware/UNO_Q.md, first section)

**Known limitations:**

- No phone has been ranged through our app yet: every phone result is simulated, with the noise model stated next to it. On the owner's UNO Q two boards have run Qorvo's phone firmware (advertising seen) and our board-to-board firmware (ranges measured).
- An independent review found two faults that drew confident wrong positions (ranges kept after a puck moved; a two-puck position published without accounting for its mirror image). Both are fixed and a self-check now hunts for such cases; real hardware may reveal others.
- The iOS app is uncompiled Swift source (written on a machine with no Mac); expect compiler errors on the first build.
- Qorvo's firmware is not in this repository and cannot be: the owner downloads it from Qorvo.
- Bring-up guides are written from documentation and forum reports, not from a board.
- With Qorvo's stock firmware one board serves one phone at a time, and the range is read by the phone app, not by the puck: the phone needs a network path to the server and the app must stay open.
- An iPhone 14 or 15 reports distance but no direction to an accessory.
- A phone whose owner installed nothing cannot be ranged or identified; at most a rough count of Bluetooth devices nearby.
- The building record is a public-data fixture (not CoStar data) and the twin is a synthetic interior (not a Matterport scan); expected head counts are estimates from floor area and building-code load factors.
- Not a life-safety product.
- The twin's rule-out of a spot 'two walls away' rests on an assumption of the range model (ranging through one wall at most) that the simulator shares; it has not been tested against real walls.
- The command agent's plan in the walk-through is a recorded real model run; any other situation without a live model gets a deterministic fallback, labelled as such.
- UNO Q: the simulated walk-through and the tests ran there (4 time-limit tests fail); live mode, the puck host and RTT reading have not run there. Its two boards last carried our board-to-board firmware, so an iPhone cannot range them until they are flashed with Qorvo's firmware again
- Board to board: our TWR firmware and Qorvo's phone firmware cannot run on one board at once, so the spacing is measured at setup (or taped) and the boards must not move afterwards; the measured ranges are uncalibrated (unknown constant offset); running a TWR board next to a phone-ranging board (two boards per puck) is untested; one firmware doing both is not built; the host's baseline mode and the anchor flow have not run against the boards
- Room and floor come from the responder's placement first; the phone's barometric pressure is carried but not turned into a floor; GPS is not used; UWB wall sensing is a research idea, not built (docs/ROOM_LOCATION.md)


## Your questions, answered

**Can a UWB board range a victim's phone over UWB at all?**

Yes, with exactly your boards: a Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware ranges an iPhone 11 or later through Apple's accessory protocol, once the person has opened our app. Both your iPhones give distance (about 60 readings a second); neither gives direction to an accessory. The app reads the distance and reports it; the board itself only logs it on its debug channel.

**Can it be done cold, to a phone whose owner installed nothing?**

No. An iPhone's UWB radio only answers a session started by an app with the user's permission. Cold, a radio can only count Bluetooth advertisements nearby: a rough band of devices by signal strength, no identity, and devices are not people. The stock puck firmware cannot even do that; the UNO Q's own radio can (written, never run).

**One puck or two for angle?**

Two pucks, as you chose, give one answer if you place them right. One puck gives a ring; two give two mirror spots across the line between the pucks, and an iPhone 14 or 15 reports no direction that could choose. Put both pucks just inside the wall the door is in (35 cm inside, about 4 m apart). Simulated over 800 phone positions in the room, with what a real iPhone delivers (distance only): one position in 76% of cases (35% because the twin rules the mirror spot out, 42% after the occupant's stated suite, shown as dismissed), median error 0.26 m, 1.5% of the positions shown wrong; the rest stay a ring (one puck did not reach the phone) or two spots. Pucks dropped mid-room: one position in only 4%, and the board says 'cannot choose' in 86%. Caveat: if the occupant states the wrong suite, the few positions that then appear are wrong. Better if you have it: a third puck gives one position in 84% with 0.20 m median error.

**Will the phone keep answering with the screen off?**

Not as built tonight: keep the app open (it keeps the screen awake). Two ways exist and are not built: a Bluetooth-paired background session in which only the puck gets the measurements, and, on iOS 18.4 or later, a Live Activity that lets the app keep ranging.

**Can we put both UWB boards on one device we throw in?**

It works poorly. The boards have one antenna each, so bearing can only come from the difference of two ranges. Simulated: at 10, 30 and 50 cm spacing the median bearing error is 37, 19 and 22 degrees (10 cm is no better than guessing), the sideways error 2.2, 1.5 and 1.4 m, the mirror ambiguity always remains, and the locator never publishes a position, only a ring. Two separate pucks 3 m apart: 7 degrees and 0.47 m sideways.

**Two pucks: one on a battery bank with the UNO Q, one powered by the Android phone?**

Yes, that is now the plan. Each board needs only 5 V. Puck A: battery bank, beside the UNO Q (which can run the server). Puck B: the Android phone over a USB-OTG adapter into the board's J-Link port (that port's chip shows up as a USB device, so the phone keeps the power on); keep a second bank as the fallback because some phones cut power to quiet devices. The phone cannot read the board (the stock firmware prints nothing on serial), and does not need to: the range returns through the iPhone app. The same phone can be the Wi-Fi hotspot for the laptop and both iPhones.

**What does a puck need?**

Power only (5 V on either micro-USB port). The range comes back through the iPhone app over Wi-Fi. Stand the board upright, at least 15 cm off the floor, component-free side toward the phone. Puck positions are measured with a tape and clicked onto the plan: the stock firmware cannot range board to board.

**Two victims' iPhones on two pucks at once?**

One iPhone can range both pucks at once. One board, on Qorvo's stock firmware, serves only one phone at a time (forum reports). So for two victims either the phones take turns (a switch in our app; simulated: a position after 3.5 s in the median, 9 s at the 90th percentile, and 23% of phones still without one after 24 s; untested on hardware), or each puck position carries two boards, one per phone (you have five boards; continuous ranging, no turn-taking). Prove one phone on both pucks first.

**Where does the server run at the table, and how do the iPhones reach it?**

On the laptop, with the laptop and both iPhones joined to a phone hotspot (venue Wi-Fi usually blocks device-to-device traffic); the Android phone powering puck B can be that hotspot. The board shows a QR code; scanning it with the iPhone camera opens our app with the server address filled in. Fallbacks: the server on the UNO Q on the same hotspot, or this machine over the tailnet if the iPhones run the tailnet client.

**[Phones] Can a UWB board range a victim's phone over UWB at all?**

Yes, with exactly your boards: a Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware ranges an iPhone 11 or later through Apple's accessory protocol, once the person has opened our app. Both your iPhones give distance (about 60 readings a second); neither gives direction to an accessory. The app reads the distance and reports it; the board itself only logs it on its debug channel.

**[Phones] Can it be done cold, to a phone whose owner installed nothing?**

No. An iPhone's UWB radio only answers a session started by an app with the user's permission. Cold, a radio can only count Bluetooth advertisements nearby: a rough band of devices by signal strength, no identity, and devices are not people. The stock puck firmware cannot even do that; the UNO Q's own radio can (written, never run).

**[Phones] One puck or two for angle?**

Two pucks, as you chose, give one answer if you place them right. One puck gives a ring; two give two mirror spots across the line between the pucks, and an iPhone 14 or 15 reports no direction that could choose. Put both pucks just inside the wall the door is in (35 cm inside, about 4 m apart). Simulated over 800 phone positions in the room, with what a real iPhone delivers (distance only): one position in 76% of cases (35% because the twin rules the mirror spot out, 42% after the occupant's stated suite, shown as dismissed), median error 0.26 m, 1.5% of the positions shown wrong; the rest stay a ring (one puck did not reach the phone) or two spots. Pucks dropped mid-room: one position in only 4%, and the board says 'cannot choose' in 86%. Caveat: if the occupant states the wrong suite, the few positions that then appear are wrong. Better if you have it: a third puck gives one position in 84% with 0.20 m median error.

**[Phones] Will the phone keep answering with the screen off?**

Not as built tonight: keep the app open (it keeps the screen awake). Two ways exist and are not built: a Bluetooth-paired background session in which only the puck gets the measurements, and, on iOS 18.4 or later, a Live Activity that lets the app keep ranging.

**[Phones] Can we put both UWB boards on one device we throw in?**

It works poorly. The boards have one antenna each, so bearing can only come from the difference of two ranges. Simulated: at 10, 30 and 50 cm spacing the median bearing error is 37, 19 and 22 degrees (10 cm is no better than guessing), the sideways error 2.2, 1.5 and 1.4 m, the mirror ambiguity always remains, and the locator never publishes a position, only a ring. Two separate pucks 3 m apart: 7 degrees and 0.47 m sideways.

**[Phones] Two pucks: one on a battery bank with the UNO Q, one powered by the Android phone?**

Yes, that is now the plan. Each board needs only 5 V. Puck A: battery bank, beside the UNO Q (which can run the server). Puck B: the Android phone over a USB-OTG adapter into the board's J-Link port (that port's chip shows up as a USB device, so the phone keeps the power on); keep a second bank as the fallback because some phones cut power to quiet devices. The phone cannot read the board (the stock firmware prints nothing on serial), and does not need to: the range returns through the iPhone app. The same phone can be the Wi-Fi hotspot for the laptop and both iPhones.

**[Phones] What does a puck need?**

Power only (5 V on either micro-USB port). The range comes back through the iPhone app over Wi-Fi. Stand the board upright, at least 15 cm off the floor, component-free side toward the phone. Puck positions are measured with a tape and clicked onto the plan: the stock firmware cannot range board to board.

**[Phones] Two victims' iPhones on two pucks at once?**

One iPhone can range both pucks at once. One board, on Qorvo's stock firmware, serves only one phone at a time (forum reports). So for two victims either the phones take turns (a switch in our app; simulated: a position after 3.5 s in the median, 9 s at the 90th percentile, and 23% of phones still without one after 24 s; untested on hardware), or each puck position carries two boards, one per phone (you have five boards; continuous ranging, no turn-taking). Prove one phone on both pucks first.

**[Phones] Where does the server run at the table, and how do the iPhones reach it?**

On the laptop, with the laptop and both iPhones joined to a phone hotspot (venue Wi-Fi usually blocks device-to-device traffic); the Android phone powering puck B can be that hotspot. The board shows a QR code; scanning it with the iPhone camera opens our app with the server address filled in. Fallbacks: the server on the UNO Q on the same hotspot, or this machine over the tailnet if the iPhones run the tailnet client.

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

- The build server publishes this repository from its own working copies, after its checks pass (no keys, no sponsor data, no machine addresses).
- If you change files here, commit and push them, then tell the agent: a sync stops by itself when it sees commits it did not make, so your changes are merged rather than overwritten.
- Never commit keys, the CoStar toolkit, the private bundle, or `.env` files.
