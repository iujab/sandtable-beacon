# Sandtable Beacon

**Throw a puck into the smoke: it ranges the phones of the people trapped inside, the building's twin turns rings into positions, and the tenant roster says who is still missing.**

Beacon is the headline mode of Sandtable, an incident-command board for fire crews (the board itself is in `../sandtable-live-fireground-twin`). Small UWB pucks (Qorvo DWM3001CDK boards on Qorvo's stock firmware) are set down inside a room. A person who opened the Beacon app on their iPhone is ranged by each puck over ultra-wideband, and the app sends a status with it: how many people, whether they can move, battery, which suite. A locator fuses the ranges with the walls of the building's 3D twin and never draws a dot where the geometry only supports a ring. The building record's tenant roster is compared with the phones still inside.

> **Status, Saturday afternoon: work in progress. Nothing has run on hardware. Every number and every screen is simulated and says so. The iOS app is uncompiled source.** This README is an interim one; `project.json` holds the live task list.

## If you have the hardware in your hands

| Step | Guide |
|---|---|
| Flash Qorvo's stock firmware onto a DWM3001CDK | `hardware/FLASH.md` |
| First power-up, two-puck layout, networking, fallbacks | `hardware/BRINGUP.md`, `hardware/LAYOUT.md` |
| What a puck needs (power, mounting) | `hardware/PUCK.md` |
| The UNO Q as puck host or as the whole command post | `hardware/UNO_Q.md` |
| Build the iPhone app in Xcode | `docs/IOS_BUILD.md` |
| What works, what does not, and why (with sources) | `docs/PHONE_FEASIBILITY.md` |

## Run it without hardware

Python 3.10 or newer; wheels-only dependencies.

```bash
bash run_demo.sh --story             # Linux / macOS / UNO Q: simulated world, plays the walk-through
```
```powershell
py -m venv .venv                     # Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m beacon.demo --story
```

Open <http://localhost:1885/>. `/phone.html` is a simulated victim's phone you can move around the room.

## Run it with real iPhones

```bash
bash run_demo.sh --source live --lan     # binds 0.0.0.0 and prints the address to give the app
```

The iPhone app posts its ranges to `POST /api/ingest`; the board shows a QR code that fills in the server address in the app. Put the laptop and the iPhones on one phone hotspot (venue Wi-Fi usually blocks device-to-device traffic).

## One puck, two pucks

| Pucks ranging a phone | What the board shows |
|---|---|
| 1 | a ring around the puck, clipped by the twin's walls |
| 2 | two possible spots, mirror images across the line between the pucks. With both pucks just inside the wall the door is in, one spot falls outside the room and is dismissed, visibly and with its reason; with pucks mid-room the board says it cannot choose |
| 3, not in a line | a position, with its uncertainty |

## What is real and what is not

| Thing | Status |
|---|---|
| Ranges and positions | Simulated (noise model in `beacon/models.py`, with sources) until real iPhones report |
| Puck firmware | Qorvo's stock Nearby Interaction image; not in this repository (download from Qorvo) |
| iOS app (`apps/ios`) | Uncompiled Swift source, modelled on Apple's accessory sample |
| Building record | Fixture: public open data for a real Los Angeles address, tenant names withheld. Not CoStar data |
| Twin | Fixture: synthetic interior fitted to the real footprint. Not a Matterport scan |
| Expected head counts | Estimates from floor area and building-code load factors, with the basis stated |

## Layout

```
beacon/        locator, tracker, simulator, session, feeds, server, roster, layout, puck host
web/           the board, the simulated phone
apps/ios/      the occupant app (Swift / SwiftUI)
hardware/      flashing, bring-up, puck and UNO Q guides
docs/          feed contract (BEACON_FEED.md), feasibility, privacy, research notes
fixtures/      building record and twin (labelled fixtures), scenarios
tests/ results/ figures/ scripts/
```

Privacy: opt-in by design, no identities from cold scanning, what is stored and for how long: `docs/PRIVACY.md`.

Not a life-safety product.
