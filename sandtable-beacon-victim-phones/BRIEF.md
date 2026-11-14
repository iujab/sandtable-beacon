# Beacon — lead's brief (lane L16)

Lead: Claude Fable 5.2. Sat Nov 14 2026. Target: CoStar "Inside and Out" (30% use of the data, 30% agentic depth, 25% usefulness, 15% demo and polish). Also entered: Best Hardware Hack, Best Physical AI, Amazon Practical AI. Main track: Education (the same as Sandtable, which this module is merged into).

## 1. The assignment

The product was given, not ideated: Sandtable's third sensing mode, in the owner's words "having the UWB board link and range a victim's phone". The open ideation tournament of the playbook was skipped. What the lead had to decide was what is physically possible, what the honest product is, and how the building data makes it more than a locator.

## 2. How the plan changed during the day (and why)

| Time (CST) | What changed | Consequence |
|---|---|---|
| 12:00 | Feed contract frozen (`docs/BEACON_FEED.md`) against assumed ESP32 + DW3000 boards | Three rungs: cold Bluetooth count, Bluetooth SOS beacon, UWB where hardware allows |
| 12:10 | The owner confirmed the boards are Qorvo DWM3001CDK, the phones an iPhone 14 and an iPhone 15, and a Mac is available | UWB to the iPhone becomes the headline. Stock Qorvo firmware (no toolchain here), our own iOS app, the app as the reporter of ranges. Android app and ESP32 firmware dropped |
| 12:20 | Research: iPhone 14 and 15 report no direction to an accessory; one board serves one phone; ranges leave the board only on its debug channel | No bearing rung to rely on; position must come from several pucks and the twin; "take turns" in the app |
| 12:35 | The owner chose two pucks (battery bank + UNO Q, Android phone over USB-OTG) | The design question becomes "how do two rings give one answer": a layout (`beacon/layout.py`), honest pruning (`beacon/prune.py`), and an evaluation of how often it works |
| 12:42 | The repository went public | Guides written for an owner following them from GitHub; no vendor files in the tree |

## 3. Design decisions

1. **Geometry is published as what it is.** `fix`, `candidates`, `ring`, `zone`, `none`. Coordinates exist only for a fix. This is the product's character and the first thing a skeptical judge can test.
2. **Two kinds of pruning, kept apart.** The twin removes what is impossible (outside the building, inside a wall, beyond what the range model allows). Something that was said (the occupant's suite; command's search room) may dismiss a possible spot, but only visibly, with the reason, and the evaluation reports how often that is wrong.
3. **The CoStar tie-in is the roster, and it is quantitative.** Expected people per suite with a stated basis, against people reported inside and people safe outside; priorities; the next puck; record checks. Head counts are estimates and are labelled so.
4. **Stock firmware.** No firmware risk tonight, at the price of the phone being the reporter. Said plainly everywhere.
5. **The simulator's noise model is cited**, and the evaluation includes a model-mismatch arm and a false-statement arm.

## 4. Self-assessment against the CoStar rubric

| Criterion | How Beacon meets it | Weak point |
|---|---|---|
| Use of the data | Record (tenants by suite, use, area, vacancy) x twin (rooms, walls, doors, routes) x live phones; the walls change the answer, the roster changes the priorities | Fixture data, no Vexcel layer in this module (Sandtable has it) |
| Agentic depth | Tool-using agent: accountability, next-puck plan, routes, radio orders, record checks | One situation type; recorded runs |
| Usefulness | Drill tool on Monday; building roll call; record feedback | Needs an app installed in advance |
| Demo and polish | Ring, cannot choose, slide the puck, one position: physical and visible in 30 seconds | Live path untested until tonight |

## 5. Work packages (all Opus 5.5 builders; the lead wrote roster, prune, layout, the feed and the documents)

R1 iOS and Qorvo research · R2 Android, Bluetooth and noise-model research · R3 board, host and geometry research · W1 models, locator, tracker · W2 simulator, scenarios, evaluation · W3 session, feeds, server, walk-through, launcher · W4 command agent, tools, report · W5 board and simulated phone · W7 iOS app · W8 puck host and hardware guides · an independent iOS reviewer · an independent skeptical judge.

## 6. The independent review, and what it changed

A fresh Opus builder judged the README and the running demo with no knowledge of the build (about 13:10 to 13:25 CST). Scores it gave: CoStar use of the data 3/10, agentic depth 3, usefulness 5, demo and polish 6; creativity 7, utility 4, charity 8, avidity 8; hardware fit 2; CoStar shortlist: no. Its five most damaging findings and what was done:

| Finding | What was done |
|---|---|
| No sponsor data: the record is open data, the twin synthetic | The module now runs unchanged on the command board's private live bundle (CoStar record and Matterport scan of a real suite) through `BEACON_BUNDLE_DIR`; verified structurally, no data copied. The fixture stays the public default and is labelled |
| Confident wrong positions: 3.6 m off after a puck moved; flips in the mid-room layout; a stale phone drawn as a fix with a 45 m error bar | Ranges are forgotten when a puck moves; a two-puck position is published only when its mirror spot is explained; ranges used together must describe the same moment; a stale phone has no geometry. An adversarial self-check (`scripts/honesty_check.py`) now hunts for such cases: 9 scenes, pass |
| The twin's rule-out is partly circular (a one-wall assumption shared by simulator and locator; a line-of-sight value an iPhone never gives) | The simulator no longer gives a line-of-sight value; the assumption is named in the README, the pitch and the limitations; the door-wall result is now reported as twin alone versus occupant's statement |
| The agent in the demo was a deterministic fallback | The walk-through's agent turn is a recorded real model run again after the scene settled; the fallback is labelled; the README says exactly when each applies |
| Nothing physical; the simulator ignored one phone per board | Turn-taking is modelled and evaluated; the walk-through says it assumes one board per phone at each puck position. Hardware: still nothing run |

Also fixed from its list: wrong-position rates are given as a share of the positions shown; the Cursor-on-Target feed says RANGE ONLY for a ring; the simulated phone page no longer leaves ghost phones; "stop sharing" removes the phone at once; the phone page no longer tells an iPhone 14 or 15 user to point the phone.
