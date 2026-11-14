# Tasks and handoff

Last synced from the build server: **Sat Nov 14 2026, 12:39 PM CST**. This file is regenerated on every sync from the two teams' own status files, so it stays current. Agents on the build server are still working; pull again before you start a task.

Deadline: **Sunday Nov 15, 8:00 am Princeton time** (Devpost). Judging 9:30 am to 12:30 pm, in person, 2 minutes per judge.

## Where things stand

| Part | Folder | Status | Demo-ready (simulated) | Being worked on |
|---|---|---|---|---|
| Command board | `sandtable-live-fireground-twin/` | building | 68% | Beacon runs end to end on the board against the vendored Beacon module (live, simulated): phones resolve zone -> ring -> fix, the agent routes the rescue team, report written. Now: two-puck setup (click-to-place pucks, two rings -> two candidates, the twin deletes the mirror), replay determinism for recordings, write-ups, radar ablation, then recordings and an independent judge. |
| Phones and pucks | `sandtable-beacon-victim-phones/` | building | 32% | re-planned for the owner's two-puck live demo: layout that makes two rings give one answer, turn-taking for two iPhones, hotspot networking with a QR code; all builders updated |

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
8. **CoStar toolkit and keys.** Until they are plugged in, the building record, the twin and the aerial view are labelled stand-ins, and CoStar scores use of their data at 30%. Guide: `sandtable-live-fireground-twin/docs/TOOLKIT_SWAP.md`. Keys go in a local `.env` file, never in git. Check CoStar's terms before committing anything recorded from their APIs.
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

**Being worked on now:** Beacon runs end to end on the board against the vendored Beacon module (live, simulated): phones resolve zone -> ring -> fix, the agent routes the rescue team, report written. Now: two-puck setup (click-to-place pucks, two rings -> two candidates, the twin deletes the mirror), replay determinism for recordings, write-ups, radar ablation, then recordings and an independent judge.

**Left to build (agents):**

- Beacon link consuming lane L16 feed (sample first, live when its server is up)
- Phones layer, accountability panel, mode switch, reporting page
- Agent situations and tools for located phones; new recordings
- Scout radar engine landing as a simulated mode; ablation for the feasibility answer
- Re-cut README, DEVPOST, PITCH; vendor L16 module; independent judge

**Left to do with real hardware (you):**

1. Beacon hardware bring-up is in lane L16 (DWM3001CDK pucks, iPhone app); this board only needs the Beacon server URL

**Needed from you:**

- CoStar toolkit and keys (COSTAR_API_KEY, Matterport token id/secret, VEXCEL_API_KEY)
- ANTHROPIC_API_KEY for live model decisions outside the recorded script

**Finished:**

- Crew tracking product with pre-plan agent, mayday exercise, replayed real-model runs (morning)
- Real open-data fixture for 7325 Medical Center Dr, footprint-fitted synthetic twin, spatial engine
- Scout radar engine in simulation (multistatic UWB: self-location, registration into the twin, breathing-person detection, twin-mismatch flags)
- GeoJSON and Cursor-on-Target feeds
- Beacon link: consumes lane L16 feed, phones as rescue targets, accountability against the building record, report
- Phones layer on the board (ring, candidates, fix, zone, device counts), mode switch Beacon | Scout | Crews
- Radar feasibility write-up with sources; puck enclosure (DWM3001CDK + cell, UNO Q host brick)

**Known limitations:**

- Sponsor layers run in fixture mode (no keys on this machine)
- UWB positions are simulated


### Phones and pucks (`sandtable-beacon-victim-phones/`)

**Being worked on now:** re-planned for the owner's two-puck live demo: layout that makes two rings give one answer, turn-taking for two iPhones, hotspot networking with a QR code; all builders updated

**Left to build (agents):**

- locator, simulator, server, board (building)
- iOS occupant app (uncompiled Swift, close to Apple's and Qorvo's samples)
- UNO Q puck host
- tenant roster accountability and command-agent hand-off
- evaluation incl. the owner's two-boards-on-one-device question
- feasibility write-up, privacy, tests, review, README, PITCH

**Left to do with real hardware (you):**

1. to be written: flash stock Qorvo firmware, build the iOS app in Xcode, place pucks, first range

**Needed from you:**

- Qorvo Nearby Interaction firmware for the DWM3001CDK: file DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex (or the QANI hex inside the DW3_QM33 SDK), from qorvo.com/products/d/da008212 or da008582: an email form, a work or school address works better than Gmail. First test whether the boards already range with Qorvo's free App Store app 'Qorvo Nearby Interaction'
- SEGGER J-Link software (J-Flash Lite) on the Mac, free from segger.com
- Xcode on the Mac with an Apple ID for signing; Developer Mode switched on on both iPhones
- one USB battery bank (or wall adapter) and a micro-USB DATA cable per puck

**Finished:**

- docs/BEACON_FEED.md v1 frozen plus additions for the live iPhone path
- docs/CONTRACTS.md
- research notes with sources: docs/research/ios_ni.md, puck_hardware.md, android_ble_models.md
- beacon/roster.py: tenant roster against phones (accountability table, priorities, next puck), smoke-tested
- locator and tracker (grid Bayes over the twin's floor; ring / candidates / fix / zone with honest geometry), 37 tests

**Known limitations:**

- Nothing has run on hardware; all results are simulated.
- The iOS app is uncompiled source (no Mac on the build machine).


## Your questions, answered by the teams

**Can a UWB board range a victim's phone over UWB at all?**

Yes, with exactly your boards: a Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware ranges an iPhone 11 or later through Apple's accessory protocol, once the person has opened our app. Both your iPhones give distance (about 60 readings a second); neither gives direction to an accessory. The app reads the distance and reports it; the board itself only logs it on its debug channel.

**Can it be done cold, to a phone whose owner installed nothing?**

No. An iPhone's UWB radio only answers a session started by an app with the user's permission. Cold, a radio can only count Bluetooth advertisements nearby: a rough band of devices by signal strength, no identity, and devices are not people. The stock puck firmware cannot even do that; the UNO Q's own radio can (written, never run).

**One puck or two for angle?**

Two pucks, as you chose, can give one answer if you place them right. One puck gives a ring; two give two mirror points across the line between the pucks. Put both pucks just inside the wall the door is in (35 cm inside, about 4 m apart): the mirror point then falls outside the room, and the board dismisses it, by the twin's walls when it is two walls away, otherwise on the occupant's stated suite, shown as dismissed. Pucks mid-room leave two points nothing can decide, and the board says so. Better if you have it: a third puck gives a position with no assumption. An iPhone 14 or 15 does not report direction to an accessory, so do not count on the phone's bearing. Simulated success rates follow when the evaluation finishes.

**Will the phone keep answering with the screen off?**

Not as built tonight: keep the app open (it keeps the screen awake). Two ways exist and are not built: a Bluetooth-paired background session in which only the puck gets the measurements, and, on iOS 18.4 or later, a Live Activity that lets the app keep ranging.

**Can we put both UWB boards on one device we throw in?**

It works poorly. Bearing from two single-antenna boards comes from the difference of two ranges: at 10 cm spacing it is useless, at 30 cm about 14 to 38 degrees of error, at 50 cm about 8 to 23 degrees, and the mirror ambiguity remains. Simulated numbers follow.

**Two pucks: one on a battery bank with the UNO Q, one powered by the Android phone?**

Yes, that is now the plan. Each board needs only 5 V. Puck A: battery bank, beside the UNO Q (which can run the server). Puck B: the Android phone over a USB-OTG adapter into the board's J-Link port (that port's chip shows up as a USB device, so the phone keeps the power on); keep a second bank as the fallback because some phones cut power to quiet devices. The phone cannot read the board (the stock firmware prints nothing on serial), and does not need to: the range returns through the iPhone app. The same phone can be the Wi-Fi hotspot for the laptop and both iPhones.

**What does a puck need?**

Power only (5 V on either micro-USB port). The range comes back through the iPhone app over Wi-Fi. Stand the board upright, at least 15 cm off the floor, component-free side toward the phone. Puck positions are measured with a tape and clicked onto the plan: the stock firmware cannot range board to board.

**Two victims' iPhones on two pucks at once?**

One iPhone can range both pucks at once. One board, on Qorvo's stock firmware, serves only one phone at a time (forum reports). So for two victims either the phones take turns (a switch in our app: each phone ranges a board for about 4 seconds and releases it; untested), or each puck position carries two boards, one per phone (you have five boards; continuous ranging, no turn-taking). Prove one phone on both pucks first.

**Where does the server run at the table, and how do the iPhones reach it?**

On the laptop, with the laptop and both iPhones joined to a phone hotspot (venue Wi-Fi usually blocks device-to-device traffic); the Android phone powering puck B can be that hotspot. The board shows a QR code; scanning it with the iPhone camera opens our app with the server address filled in. Fallbacks: the server on the UNO Q on the same hotspot, or this machine over the tailnet if the iPhones run the tailnet client.

**Is one UWB sensor enough to scan a room?**

No. A single DWM3001CDK board cannot hear its own echoes. Qorvo's own staff say the module cannot transmit and receive at the same time. The smallest radar is two boards: one transmits, the other records the echoes that arrive after the direct pulse.

**Why not?**

Its antenna is switched either to the transmitter or to the receiver. One frame lasts at least 32.6 µs, but a wall 3 m away answers after 20 ns, while the board is still transmitting. The receiver only switches on after the frame ends, by which time every room echo has already passed.

**How do multiple boards fix it?**

One board transmits and the others listen. Each pair turns an echo's delay into an ellipse with the two boards as foci. More boards give more ellipses at different angles, and the target sits where they cross. The boards first range each other to learn their own positions, because every ellipse needs known foci.

**How many are needed, and what does each added board buy?**

Two boards give presence and breathing but no position. Three give a 2-D position (3 ellipses), four add a redundant check (6), and five add better angles and outlier rejection (10). In the sample throw, the area that fits every pair shrinks from 4.01 m² (two boards) to 0.40 m² (three) and 0.15 m² (five).

**What is the 'map', and what is it not?**

It is a coarse echo map: a few strong reflectors, a rough outline, what moved, and where the room disagrees with the stored twin. It is not a LiDAR-grade model, because echoes about 0.3 to 0.6 m apart merge and walls reflect like mirrors. The twin is the map; the boards add people, breathing and changes.

**Why does this beat cameras and LiDAR in smoke, and where does it not?**

Smoke particles (0.01 to 1 µm) scatter light and LiDAR strongly but are negligible to a 46 mm radio wave. In fire tests, LiDAR erred below 4 m visibility while radar stayed within 1%. Thermal imagers also see through smoke and remain better at seeing fire. Flames, wet walls and metal cost radio signal; a still, non-breathing person stays invisible.

**What is the biggest open risk?**

Signal strength. The sample victim's echoes arrive 25 to 36 dB below the direct pulse. In real recorded data, the empty room's own multipath already flickers at about 22 dB below. Expect detection within a few metres of the pucks. A breathing person across a 9.5 m room is unproven until measured.

**What is simulated today?**

All of the radar. No board has run it, and the reused echo-reading firmware targets ESP32 boards, not the DWM3001CDK. Scout is a secondary, simulation-only mode of Sandtable Beacon. The timing and geometry arithmetic is exact; detection performance is not measured. On the one real recording available, the reused tracker followed clutter, not the person.


## Working alongside the agents

- The build server re-syncs this repository every 15 minutes while the agents work. Each sync is one commit.
- If you change files here, commit and push them, then tell the agent. A sync stops by itself when it sees commits it did not make, so that your changes are merged into the agents' trees and not overwritten.
- Never commit keys, the CoStar toolkit, or `.env` files.
