# Sandtable Beacon — Devpost draft

**Tagline:** Set two pucks down inside the door: they range the phones of the people trapped in the room, the building's twin turns two rings into one position, and the tenant roster says who is still missing.

**Tracks:** Education (main) · CoStar "Inside and Out: AI for the Built World" · Best Hardware Hack · Best Physical AI Hack · Amazon Best Practical AI Innovation

## Inspiration

A search crew in smoke cannot see a person three metres away, and neither can a camera or a LiDAR. But almost everyone trapped in an office building is holding an ultra-wideband radio that measures distance to a hand's width: their phone. We were already building Sandtable, a command board that tracks fire crews inside a building's 3D twin with UWB pucks. The obvious next question was the one our teammate asked out loud: can the puck range the victim's phone?

## What it does

- An occupant opens the Beacon app and presses Trapped, Injured, Sheltering or I am out. No account and no name: a random id that rotates, and the screen shows exactly what is sent.
- Each puck (a Qorvo DWM3001CDK on Qorvo's stock firmware) ranges that iPhone over ultra-wideband through Apple's Nearby Interaction accessory protocol.
- A locator fuses the ranges with the walls of the building's twin and refuses to overclaim. One puck is a ring. Two pucks are two mirror spots. Put both pucks just inside the wall the door is in and the mirror spot falls outside the room: the twin rules it out, or it is dismissed on the occupant's stated suite and shown as dismissed. Put them mid-room and the board says "cannot choose".
- The building record's tenant roster is joined to the twin's rooms and to the phones: expected people per suite, people reported inside, people who pressed "I am out", who is unaccounted for.
- A command agent turns that into radio orders, a search order, where the next puck should go, and checks to file against the record.
- With no app at all, the most a radio can honestly do is count Bluetooth devices nearby. That is all we show: a count, never a dot, never an identity.

## How we built it

- **Research first.** We read Apple's and Qorvo's documentation and forums to settle what is possible: which boards an iPhone will range with, what the iPhone 14 and 15 report (distance, no direction), that one board serves one phone at a time, that nothing works without an app. The answers and their sources are in `docs/PHONE_FEASIBILITY.md`.
- **Locator.** A grid posterior over the twin's floor in Python and numpy, with the twin's walls in the forward model, and a classifier that publishes a fix, candidates, a ring or a zone according to what the posterior supports.
- **Simulator and evaluation.** A simulated world with a noise model built from published measurements, and an evaluation that reports how often the board is right, wrong or undecided for each puck layout.
- **Server and board.** FastAPI, WebSocket, GeoJSON and Cursor-on-Target feeds; a board in Sandtable's visual language; a simulated phone page.
- **iOS app.** Swift and SwiftUI, Core Bluetooth and Nearby Interaction, modelled on Apple's accessory sample.
- **Record and agent.** A roster join over the building record and the twin, and a tool-using command agent whose recorded runs replay offline.

## Challenges we ran into

- The boards we assumed (ESP32 with a DW3000) cannot range an iPhone at all: the two libraries that make it work exist only for other processors. The boards we actually had were the right ones.
- An iPhone 14 or 15 gives distance but no direction to an accessory, so one puck can never give a position. That forced the question of how to make two pucks enough, and the answer came from the floor plan.
- The stock firmware serves one phone per board and reports ranges only to the phone, so the app has to be the reporter and two victims have to take turns.
- No Mac on the build machine: the iOS app was written without a compiler and reviewed line by line against Apple's documentation.

## Accomplishments that we're proud of

- A board that says "cannot choose" when it cannot, and shows its reason when it can.
- An evaluation that reports its own wrong answers, including what happens when the occupant's statement is false.
- A hardware path for phones that needs no custom firmware, and a small one of our own for the pucks' spacing: two of our boards range each other with SS-TWR at about five readings a second on a long-range radio profile (measured on the bench, uncalibrated; `hardware/twr_firmware/`).

## What we learned

- Honest geometry is a feature: a ring, two candidates and a position are different things and a rescuer should be told which one they have.
- The building's data is what turns a ranging gadget into something a commander can use: walls make two pucks enough, and the tenant roster makes a dot into an accountability picture.

## What's next

- Real iPhones against real boards, and measured error instead of simulated.
- Puck firmware that reports ranges itself, so the phone can go dark, and one firmware that ranges phones and the other puck at once, so a thrown pair measures its own spacing on landing (today it takes two firmwares: `hardware/BOARD_TO_BOARD.md`).
- A dual-antenna radio for bearing from a single puck.
- Live CoStar and Matterport data for a Houston building.

## Built with

Python, FastAPI, numpy, Swift, SwiftUI, Core Bluetooth, Nearby Interaction, Qorvo DWM3001CDK, Arduino UNO Q, Claude (command agent), GeoJSON, Cursor-on-Target

## Honest status at submission

TODO(owner): replace this paragraph with what was actually run on hardware. As of Sunday 02:20 CST: every phone result is simulated and labelled; the iOS app is uncompiled source in this repository; the phone-ranging firmware is Qorvo's stock image, not ours; our board-to-board firmware was measured on two boards (hand-transcribed summary in `results/twr_measured_2026-11-15.json`, uncalibrated), but the host's baseline mode and the anchor flow have not run against the boards; the building record and twin are labelled fixtures, not CoStar or Matterport data.
