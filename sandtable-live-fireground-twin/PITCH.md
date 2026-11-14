# Sandtable Beacon: the two-minute demo

One sentence a judge can retell: **"Two pucks heard two phones; for one phone the building's own walls ruled out the wrong place and it landed in the dentist's office the record said would be full, and the AI sent the rescue team there and wrote the report."**

## Two ways to run it (same board, same story)

**A. With the owner's hardware (preferred).** Two DWM3001CDK pucks on power only (one on the UNO Q's battery bank, one on the Android phone over USB-OTG), on Qorvo's stock Nearby Interaction firmware. Two iPhones (a 14 and a 15) with lane L16's app open, screen on. Neither iPhone reports direction to these boards: everything below is distance only.

1. Start the Beacon server the phones report to, per lane L16's guide in `vendor/beacon/` (port 1885, reachable over Wi-Fi from the phones).
2. Start the board against it: `python -m sandtable.demo --beacon-url http://127.0.0.1:1885` (Windows: `.venv\Scripts\python -m sandtable.demo --beacon-url http://127.0.0.1:1885`). Open <http://localhost:1810/>.
3. Stage it before the judge arrives. Suite 310 is the south-west corner of the floor, so its west and south walls are the building's outside walls. Stand the two pucks upright, 15 cm or more off the table, 2 to 3 m apart in a line parallel to one outside wall at the demo's scale. A phone's mirror candidate is its reflection across that line: put **phone 1 farther from the line than the line is from the wall** (its mirror lands outside the building, so the twin deletes it) and **phone 2 close to the line** (both candidates stay inside the room). Tape-measure the pucks, click **Place pucks**, click the plan where each one stands (or type the measurements), **Send 2 to Beacon**.
4. Reset, sound on, 3D twin view. Phones on the table, app open, not yet pressed.

**B. Hands-free on the simulator (fallback, and the video).** `bash run_demo.sh --storyboard` (Windows: `.venv\Scripts\python -m sandtable.demo --storyboard`). The exercise plays by itself in about a minute: pre-plan, Engine 1 to the fire room, pucks thrown, lane L16's simulator plays its scripted phones (Engine 1 places two pucks by hand; each phone then has two possible positions, mirror images across the line between the pucks; one phone's mirror falls outside the building, so the twin deletes it and that phone becomes a point; the other phone's two positions are both inside the room, and the board says it cannot choose), the agent routes the rescue, all out, the report. The `SIMULATED` badges are on screen; say "this run is the simulator" once.

## The script (live version; the times assume the replayed pre-plan)

| Time | You do | You say | On screen |
|---|---|---|---|
| 0:00 | Click **Dispatch alarm** | "A fire in a real medical office building in Los Angeles. The commander's first question: who is still inside, and where?" | Alarm. The agent's tool calls stream on the right, coloured by layer. |
| 0:08 | Point at the pre-plan, then the aerial | "Before anyone arrives, the agent reads three layers for this address. The building record: three floors, and on the third a dentist whose patients may be sedated. The aerial photo, which it actually looks at: where the engine sets up. The 3D twin: the stairs, the routes." | Pre-plan sections; the attack and evacuation stairs light up in the twin. |
| 0:22 | Point at the two pucks on the table, then the plan | "A crew has put two pucks in the fire room. They are plain UWB boards, power only. We measured where they stand and clicked them onto the plan." | P1 and P2 in Suite 310. |
| 0:30 | Hand phone 1 and phone 2 to their "occupants"; they press **Trapped** / a status | "These two phones belong to people inside. They opened the building's emergency app and pressed a button." | Each phone gets a ring around each puck: two rings per phone. |
| 0:42 | Point at phone 1's rings | "Two distances give two places: the rings cross twice, mirror images across the line between the pucks. With two pucks that is all the physics gives." | Two candidate points with their weights. |
| **0:50** | **Hold. Point at the crossed-out ghost** | **"But one of those places is outside the building. The twin knows the walls, so it deletes it. Phone 1 is here: Suite 310, the dentist's office, beside the people the record expects there. The map just did the job of a third puck."** | **THE AH-HA (0:50 to 1:05).** One candidate crossed out, the other becomes a point with its uncertainty, in Suite 310; the accountability row for 310 turns to people reported, with the tenant and its life hazard. |
| 1:05 | Point at phone 2 | "Phone 2's two places are both inside the room. The board will not guess. It says two places, and the order is to search both, most probable first. Or move a puck and it becomes a point." | Phone 2: two weighted candidates, no point. |
| 1:15 | Point at the radio panel and the route | "The agent decides: the trapped phone with two people who cannot move goes first. It sends the rescue team by a route through the twin, tells them what the record says is in that room, and when they reach the phone it asks for EMS for those two people." | Blue route from the stair; radio order; then the EMS request in the timeline. |
| 1:35 | Click **Report** | "And the command post gets the report it keeps: who was found and when, who is still unaccounted for, and why that number is a range: the record says who is usually here, the phones say who is here now." | `/report.html`: people, accountability table, plan, timeline. |
| 1:50 | Point at the footer | "Honest status: the building data is real public data standing in for CoStar's, the interior is synthetic, and every uncertain thing is drawn as uncertain. Plug in CoStar's layers and the walls that decided this are the real walls." | Footer: what is real. |

On the hands-free simulator the same beats play by themselves; the ah-ha there is the ring or room-level zone tightening to a point in Suite 310 beside the record's tenant, and the rescue team being re-routed from "search the room" to the point.

**45-second version** (a judge in a hurry): start with the pre-plan already done and the pucks placed. Phones press their button (0:00), two rings each, two candidates (0:10), the twin deletes one: a point in the dentist's office beside who the record expects (0:20, the ah-ha), phone 2 stays two places and the order is to search both (0:30), the route and the report (0:40).

**Scout, in one line:** the same pucks as a radar that finds an untagged, breathing person; one board cannot hear its own echo, three or more can locate, and this mode is simulation only. **Crews, in one line:** tagged firefighters and a mayday exercise, simulation only on these boards.

## If it goes wrong

| Problem | Do |
|---|---|
| Badge says "Beacon module not connected" | The board is relaying its labelled sample. Say so, or restart with `--beacon-url` pointing at the server the phones report to |
| A phone shows nothing | App open and screen on (as built it does not range in the background); Bluetooth on; the puck powered (some battery banks switch off at this low current: plug into the board's other USB port or use a wall adapter) |
| Pucks are heard but not placed | Place them on lane L16's own board at <http://localhost:1885/> (its tray maps each Bluetooth name to a puck), then come back |
| Both phones become points, or both stay two candidates | Fine: explain what you see, it is the honest picture. Move phone 2 toward or away from the line between the pucks and watch it change |
| A ring is clearly wrong | Real ranges at this scale have not been measured; say so. Switch to the hands-free simulator (B) |
| Agent log shows `reflex-fallback` | Say so: "no recording for this exact situation, so a deterministic policy acted; with an API key the model decides live" |
| No sound | Read the top radio line aloud |

## Questions judges will ask, with straight answers

**"Is that our data?"** No, and the screen says so. No CoStar key or toolkit reached the machine this was built on. The building record is real public data for this building (the county assessor parcel, the city's business registrations by suite with names withheld, the county footprint and height); the aerial is real USDA NAIP imagery; the interior is synthetic, fitted to the real footprint. CoStar-only fields are empty on purpose. The live adapters are written, and `docs/TOOLKIT_SWAP.md` is the 30-minute swap. With a real Matterport model the walls that prune a phone are real walls.

**"What does the agent actually decide?"** Which phone goes first (from the occupants' own reports and the record's life hazards), which crew, which route, what to say on the radio, when to ask for EMS, and how to search when it only has a ring or two candidates: it is told it may never send a crew to "the phone's position" unless the geometry is a point. A deterministic reflex layer turns the feed into situations; the model acts on them with tools. The pre-plan you saw is a replay of a recorded real model run, labelled `replay`; a situation with no recording is handled by a deterministic policy labelled `reflex-fallback`; with an API key every decision is live.

**"Why phones?"** Nearly everyone inside carries one. A phone can say what a sensor cannot: "two of us, cannot move, smoke under the door", and the battery and whether it has moved. And the person is identified to the command post as a target (B1), not as a blob.

**"What about people without the app?"** They are never ranged, located or identified. The most a puck could do is count Bluetooth devices near it (a band, devices not people, and the stock puck firmware does not even scan; the UNO Q's radio could, written but never run). They show up honestly in the accountability table: the building record's expected head count minus everyone accounted for, as a range. Getting people to install the app is the adoption problem; a building's own occupant app or an operating-system emergency feature would solve it, and neither exists today.

**"Privacy and consent?"** Opt-in by design: the phone's operating system will not range for an app the person did not open and permit. The app sends a random id that changes every 10 minutes, distances, the status the person chose, battery and pressure; never a name, number or GPS position. "Stop sharing" deletes the phone at once; a quiet phone is deleted after 15 minutes. Limits, stated in `vendor/beacon/docs/PRIVACY.md`: a 10-minute pseudonym is still followable, and the prototype's link is plain HTTP on a local network.

**"How accurate is a ring, and a fix? With what evidence?"** On real phones: unmeasured. Lane L16 found no controlled measurement of an iPhone ranging these boards; Qorvo's DW3000 datasheet quotes about plus or minus 6 cm after calibration and 15 cm without, in line of sight, and forum users report 40 to 50 cm errors between boards in some set-ups (sources in `vendor/beacon/docs/research/puck_hardware.md`). In simulation, the Beacon table in the README's Results gives fix errors, how often the truth lies inside what was drawn, and how often a fix was wrong, per number of pucks, with and without the twin. A ring is drawn as a ring precisely because one distance is not a place.

**"Can one UWB board scan a room by radar?"** No. The board cannot transmit and receive at once (Qorvo staff say so about this module), and its shortest frame lasts 32.6 µs while a wall 3 m away answers in 20 ns. Two boards give an ellipse per echo; three or more locate where the ellipses cross. The result is a coarse echo map, never LiDAR, and the biggest risk is echo strength across a room. Scout is simulation only. Full answer: `docs/RADAR_FEASIBILITY.md`.

**"Why not cameras, or thermal imagers?"** Smoke scatters visible light and LiDAR. Thermal imagers do see through smoke and stay the better tool for fire and hot surfaces, but they need line of sight and fail in heat, steam and water spray. A phone answers from behind a door or under a desk and says who it is and what state they are in. This complements the imager; it does not replace it.

**"What is real tonight?"** The board, the reflex layer, the agent's tools and its replayed real runs, the report and the feeds; real public data for the building. Simulated: the phones on the hands-free run, the radar, the crews. Fixtures: all three sponsor layers.
Not yet run on the owner's pucks and iPhones: say so if asked, and say which run the judge is watching.

**"Who would switch it on on Monday?"** The incident commander and the command post, for any building whose occupants have the app; a fire-prevention bureau, for a pre-plan draft with a verify-on-site list for any commercial address; and a building owner, who has a reason to give tenants the app.

**"Why the Healthcare track?"** Finding trapped and injured people, knowing their condition before a rescuer reaches them, and handing them to EMS with it is the first link of emergency care.
