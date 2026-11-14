# Sandtable Beacon: the two-minute demo

One sentence a judge can retell: **"Two pucks heard two phones; the building's own walls ruled out the wrong place for one of them, the building record said who should be in that suite while the phones said who is, and the AI sent the rescue team and wrote the report."**

## What the judge is looking at (say it in the first ten seconds)

- **Data.** The owner's laptop in live mode if it is set up: every page then carries the header "LIVE SPONSOR DATA, PRIVATE", and the building is a real Los Angeles office building read privately from the CoStar data kit, the Matterport Model API and Vexcel (none of it is in the public repository). Otherwise the stand-in: real public data for a different building and a synthetic interior, labelled on screen as not the sponsor's. The README's "Sponsor data" section says the same in three parts; use its words.
- **Phones, pucks, crews.** Simulated, in both cases. Not run on hardware. The hands-free run replays a recorded run of lane L16's simulated Beacon module: the board shows "Beacon replay · SIM" and "Replay, module not live".

## Two ways to run it

**A. Hands-free (the default, and the video).** `bash run_demo.sh --storyboard` (Windows: `.venv\Scripts\python -m sandtable.demo --storyboard`), or click the exercise button on the board. It plays by itself in about a minute and a half: alarm, pre-plan, Engine 1 to the fire room, two pucks placed by hand, phone 1 resolved to a point because the twin rules out its mirror place outside the building, phone 2 left as two places, the agent's orders, the rescue team reaches both, all out, the report. For live mode, start it the way `docs/TOOLKIT_SWAP.md` says (keys and the private bundle) and check the header.

**B. With the owner's hardware, if it has been brought up.** Two DWM3001CDK pucks on power only (one on the UNO Q's battery bank, one on the Android phone over USB-OTG), on Qorvo's stock Nearby Interaction firmware; two iPhones (a 14 and a 15) running lane L16's app, which has to be built with Xcode first (its iOS source has not been built or run by us). Neither iPhone reports direction to these boards: everything is distance only.

1. Start the Beacon server the phones report to, per lane L16's guide in `vendor/beacon/` (port 1885, reachable over Wi-Fi from the phones).
2. Start the board against it: `python -m sandtable.demo --beacon-url http://127.0.0.1:1885` (Windows: `.venv\Scripts\python -m sandtable.demo --beacon-url http://127.0.0.1:1885`). Open <http://localhost:1810/>.
3. Stage it before the judge arrives. Stand the two pucks upright, 15 cm or more off the table, 2 to 3 m apart in a line parallel to an outside wall of the fire room at the demo's scale. A phone's mirror candidate is its reflection across that line: put **phone 1 farther from the line than the line is from the wall** (its mirror lands outside the building, so the twin deletes it) and **phone 2 close to the line** (both candidates stay inside the room). Tape-measure the pucks, click **Place pucks**, click the plan where each one stands (or type the measurements), **Send 2 to Beacon**.
4. Reset, sound on, 3D view. Phones on the table, app open, not yet pressed. The occupants press their status button at 0:32 below.

## The script (the same words for A and B)

| Time | You do | You say | On screen |
|---|---|---|---|
| 0:00 | Start the exercise | "A fire in an office building. The commander's first question: who is still inside, and where?" | Alarm. The agent's tool calls stream on the right, coloured by layer. |
| 0:08 | Point at the header or the footer, then the pre-plan | Live: "This is CoStar's record, Vexcel's imagery and a Matterport scan of a real building, run privately; none of it is in our public repository." Stand-in: "This is a stand-in: real public data for a different building and a synthetic interior, labelled as such." Then: "Before anyone arrives, the agent reads the three layers: who occupies which suite, the outside from the air, which it actually looks at, and the inside from the scan." | Pre-plan sections, each item tagged with its layer; stairs and routes in the twin. |
| 0:25 | Point at the plan | "A crew has placed two pucks in the fire room: plain UWB boards, power only, positions measured with a tape. Everything you see from here on is simulated." | P1 and P2 on the plan, "placed by hand". |
| 0:32 | Point at phone 1 | "Two people inside opened the building's emergency app and pressed a button. Each phone is two distances from two pucks, and two distances give two places, mirror images across the line between the pucks. That is all the physics gives." | Two rings per phone; two candidate places. |
| **0:45** | **Hold. Point at the crossed-out ghost** | **"But one of phone 1's places is outside the building. The scan knows the walls, so it deletes it. Phone 1 is here, in this suite. The map just did the job of a third puck."** | **THE AH-HA (0:45 to 0:58).** One candidate crossed out ("ruled out: outside the building outline"); the other becomes a point with its uncertainty. |
| 0:58 | Point at phone 2 | "Phone 2's two places are both inside the room. The board will not guess: two places, search both." | Phone 2: "cannot choose: 2 places", with weights. |
| 1:05 | **The CoStar minute.** Point at the accountability panel and read its headline | Live: "The record does not say who is in this suite: one occupancy entry, no tenant named, no head count. The phones say three people are in it. And five other tenancies on this storey have no pucks: about 25 to 62 people on record, nobody accounted for. That is who is still to find." Stand-in: "The record has a dental office in this suite, with a head count estimated from floor area. The phones report three people. The floor's other suites have no puck in range: not scanned, with the record's range." Say what the headline on screen says. | Accountability panel: what the record says next to what the phones say; the suites with no puck as a group with an expected range. |
| 1:25 | Point at the route and the radio | "The agent decides: two people trapped who cannot move go first. It sends the rescue team by a route through the scan, tells them what the record says about that room, and asks for EMS when they reach them. Phone 2 is searched until the crew finds them." | Route; radio orders; the EMS request; both phones reached and brought out. |
| 1:45 | Click **Report** | "The command post keeps this: who was found and when, who is still unaccounted for and why that is a range, and where every fact came from." | `/report.html`: people, accountability, plan, timeline, provenance per layer. |
| 1:55 | Look at the judge | "Phones, pucks and crews are simulated; nothing has run on our boards yet. The building data is [live, privately / a public stand-in], and the agent's decisions are real model output." | |

**45-second version** (a judge in a hurry): start with the pre-plan done and the pucks placed. Two rings per phone, two places each (0:00); the scan deletes the place outside the building: phone 1 is a point in the suite (0:12, the ah-ha); phone 2 stays two places, search both (0:20); the record against the phones in the accountability headline (0:28); the route and the report (0:38).

**Scout, in one line:** the same pucks as a radar that finds an untagged, breathing person; one board cannot hear its own echo, three or more can locate, and this mode is simulation only. **Crews, in one line:** tagged firefighters and a mayday exercise, simulation only on these boards.

## If it goes wrong

| Problem | Do |
|---|---|
| "Replay, module not live" | Normal for the hands-free run: the phones come from a recorded run of the simulated Beacon module. Say so |
| "Beacon module not connected" | The board is relaying its labelled sample. Say so, or restart with `--beacon-url` pointing at the server the phones report to |
| Live header missing when you expected live mode | The layers fell back to the stand-ins; say "this is the stand-in" and carry on |
| A phone shows nothing (hardware) | App open and screen on (as built it does not range in the background); Bluetooth on; the puck powered (some battery banks switch off at this low current: plug into the board's other USB port or use a wall adapter). Otherwise switch to the hands-free run |
| Pucks are heard but not placed (hardware) | Place them on lane L16's own board at <http://localhost:1885/> (its tray maps each Bluetooth name to a puck), then come back |
| Both phones become points, or both stay two places | Fine: explain what you see, it is the honest picture |
| Agent log shows `reflex-fallback` | Say so: "no recording for this exact situation, so a deterministic policy acted; with an API key the model decides live" |
| No sound | Read the top radio line aloud |

## Questions judges will ask, with straight answers

**"Is that our data?"** Three parts, the same everywhere. What ran, privately: on 14 November, with the owner's keys and the CoStar hackathon data kit, the same code ran on all three layers for one real 12-storey office building in Los Angeles (the record from the kit, a Matterport scan of one suite through the Model API, seven Vexcel images), including a phone-rescue exercise end to end with a live model. What the public repository holds: none of that data; it runs on real public data for a different building and a synthetic interior, labelled as not yours. What you see now: the header tells you which. Detail: `docs/LIVE_STATUS.md`.

**"What does the agent actually decide?"** Which phone goes first (from the occupants' own reports and the record), which crew, which route, what to say on the radio, when to ask for EMS, and how to search when it only has a ring or two candidates: it may never send a crew to "the phone's position" unless the geometry is a point. A deterministic reflex layer turns the feed into situations; the model acts on them with tools. On the stand-in, every turn you see is a replay of a recorded real model run, labelled `replay`; in live mode it is a live model; a situation with neither is handled by a deterministic policy labelled `reflex-fallback`.

**"What does the building record add?"** Who should be there. The phones say who is there now. Put side by side they give a finding neither has alone: a suite the record cannot put a name or a head count to, with people in it, and tenancies on the same storey that nobody has covered yet, with the range of people they imply.

**"Why phones?"** Nearly everyone inside carries one. A phone can say what a sensor cannot: "two of us, cannot move, smoke under the door", and the battery and whether it has moved. And the person is identified to the command post as a target (B1), not as a blob.

**"What about people without the app?"** They are never ranged, located or identified. The most a puck could do is count Bluetooth devices near it (a band, devices not people); the simulated pucks show such a count, but the stock puck firmware cannot collect one (the UNO Q's radio could: written by lane L16, never run). They show up in the accountability table as the record's expected range minus everyone accounted for. Getting people to install the app is the adoption problem; a building's own occupant app or an operating-system emergency feature would solve it, and neither exists today.

**"Privacy and consent?"** Opt-in by design: the phone's operating system will not range for an app the person did not open and permit. The app sends distances, the status the person chose, how many people, battery and pressure, under a random id; never a name, number or GPS position. A phone id that rotates every 10 minutes is a design, not implemented. Dropping a phone at once on "Stop sharing", and after 15 minutes of silence, happens in lane L16's module only. This board keeps what it received for the incident report and serves it without authentication on the local network. A real deployment needs transport security and an access model.

**"How accurate is a ring, and a fix? With what evidence?"** On real phones: unmeasured. Lane L16 found no controlled measurement of an iPhone ranging these boards; Qorvo's DW3000 datasheet quotes about plus or minus 6 cm after calibration and 15 cm without, in line of sight, and forum users report 40 to 50 cm errors between boards in some set-ups (sources in `vendor/beacon/docs/research/puck_hardware.md`). In simulation, the two-puck section of `results/RESULTS.md` gives how often two pucks give one answer, how often the twin decides it, and how often the answer is wrong. A ring is drawn as a ring because one distance is not a place.

**"Can one UWB board scan a room by radar?"** No. The board cannot transmit and receive at once (Qorvo staff say so about this module), and its shortest frame lasts 32.6 µs while a wall 3 m away answers in 20 ns. Two boards give an ellipse per echo; three or more locate where the ellipses cross. The result is a coarse echo map, never LiDAR, and the biggest risk is echo strength across a room. Scout is simulation only. Full answer: `docs/RADAR_FEASIBILITY.md`.

**"Why not cameras, or thermal imagers?"** Smoke scatters visible light and LiDAR. Thermal imagers do see through smoke and stay the better tool for fire and hot surfaces, but they need line of sight and fail in heat, steam and water spray. A phone answers from behind a door or under a desk and says who it is and what state they are in. This complements the imager; it does not replace it.

**"What is real tonight?"** Real: the board, the reflex layer, the agent and its recorded or live model turns, the report and the feeds; the public data of the stand-in building; and, privately, the three live sponsor layers for one real building. Simulated: the phones, the pucks, the radar, the crews. Not run on hardware.

**"Who would switch it on on Monday?"** The incident commander and the command post, for any building whose occupants have the app; a fire-prevention bureau, for a pre-plan draft with a verify-on-site list for any commercial address.

**"Why the Healthcare track?"** It is the pre-hospital end of it: finding trapped and injured people, carrying their own report (trapped, injured, can or cannot move) to the rescuer and into the EMS request. There is no clinical content beyond that report, and we say so.
