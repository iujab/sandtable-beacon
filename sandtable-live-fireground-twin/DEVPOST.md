# Sandtable Beacon (Devpost draft)

**Tagline:** Pucks in a burning building hear the occupants' phones; each phone appears in the right room of a map built from the building's own data, drawn only as precisely as the measurement allows, and an AI command agent routes the rescue and writes the report.

**Tracks:** Healthcare (main) · CoStar "Inside and Out: AI for the Built World" · Best Hardware Hack · Best Physical AI Hack · Amazon Best Practical AI Innovation

TODO(owner): team members' names
TODO(owner): Discord usernames
TODO(owner): public GitHub repository link
TODO(owner): video link (2 minutes at most; `PITCH.md` is the script)
TODO(owner): one photo of the real rig (two pucks, two iPhones, the board on screen), if the hardware runs

## Inspiration

When a commercial building is on fire, the incident commander's hardest question is who is still inside, and where. The answer arrives slowly: from the people who got out, from the radio, from crews searching room by room in smoke. Two things struck us. First, the building is already known: the record of who occupies which suite, the view from above and the 3D interior all exist for the same address, just not on the fire ground. Second, nearly everyone inside carries a radio that can answer: their phone. We had UWB boards and two iPhones, and a sibling team on our bench was working out exactly what an iPhone can and cannot tell a board. So we built the command post that puts those answers on the building's own map.

## What it does

Sandtable Beacon is a command and reporting centre for one question: who is inside, and where.

1. **Pre-plan.** When the alarm comes in, a command agent reads three layers for the address and writes the pre-incident plan: from the building record, the floors, the tenants by suite and their likely life hazards; from the aerial image, which it actually looks at, the sides of the building and where the engine sets up; from the 3D scan, the rooms, stairs and routes. It also lists what the layers disagree about.
2. **Pucks in, phones answer.** A crew places pucks (Qorvo DWM3001CDK boards with Bluetooth and UWB) in the fire room. Occupants who opened the opt-in app and pressed a status button are ranged by each puck in reach and report how many people are with them and whether they can move.
3. **Drawn honestly.** One puck gives a ring. Two pucks give two candidate points, mirror images of each other. The twin then deletes any candidate that falls outside the building or inside a wall, and if one is left, it is a point in a room. If both are possible, the board shows both and the order is to search both. Phones without the app are at most a device count, never people (the simulated pucks show one; the stock puck firmware cannot collect it).
4. **The record against the phones.** Every suite: what the record says about it, phones heard, people reported, located, unaccounted as a range. The record says who should be there; the phones say who is. On a live record that can read: "the record says this suite is empty; the phones say three people are in it", with the storey's other tenancies shown as not covered, with the range of people the record implies.
5. **Command.** The agent prioritises, routes the rescue team through the twin, radios the order with what the record says is in that room, re-routes when a phone's position improves, and requests EMS with the occupants' reported condition.
6. **Report and feeds.** A printable report for the command post, and the same picture as GeoJSON and Cursor-on-Target.

Two secondary modes run in simulation only: **Scout**, the pucks as a multistatic UWB radar that finds an untagged breathing person (and the honest answer to "is one board enough?": no), and **Crews**, tagged firefighters with a mayday exercise.

## How we built it

- **Sponsor data, in three parts.** What ran, privately: on 14 November, with the owner's keys and the CoStar hackathon data kit, the same code ran on all three layers for one real 12-storey, sprinklered office building in Los Angeles: the CoStar record from the kit, the Matterport Model API for one scanned suite (rooms with real boundaries, walls, doorways and a walking graph; the Showcase SDK view embedded on localhost), and seven Vexcel images, on which the agent's vision tool read the roof and the parking. The board ran a phone-rescue exercise end to end on that building with a live model. What the public repository contains: none of that data (the owner's rule and the sponsors' terms). It runs on real public data for a different building (county assessor parcel, city business registrations by suite with names withheld, county footprint and height, city hydrants and fire stations, USDA NAIP imagery) and a synthetic interior fitted to its footprint, every layer labelled as not the sponsor's. What a judge sees: the live laptop with a "LIVE SPONSOR DATA, PRIVATE" header if it is set up, otherwise the stand-in.
- **Phones, by lane L16 of our bench.** The Beacon module (vendored in `vendor/beacon/`) relies on Qorvo's stock Nearby Interaction firmware on the boards and adds iOS source that ranges each puck and reports over Wi-Fi (not built or run by us), a server with a locator and tracker that publishes rings, candidates and fixes, and a simulator. Its feed contract forbids publishing a point unless the measurements support one.
- **One coordinate chain:** twin frame, latitude and longitude, aerial pixel. The same point lights up in the plan, the 3D twin, the aerial and the feeds, and a candidate can be tested against a wall.
- **A two-tier agent.** A reflex layer in plain code turns the feeds into situations and keeps assignments, routes and accountability. The command agent is Claude with tools for the record, the aerial (including vision), the twin, the people and actions, in a plan, act, re-plan loop. Tool calls are prompted JSON, so the same loop runs on the Anthropic API, the Claude CLI or a replay cache of recorded real responses.
- **The board and the reporting centre:** a no-build web app with a three.js view of the floor, an SVG plan, the aerial with overlays, a phone page for the rescue team that reads the GeoJSON feed, and a printable report page.
- **The radar mode** adapts lane L08's simulator and DSP; the feasibility answer is arithmetic from datasheets plus cited published work, in `docs/RADAR_FEASIBILITY.md`.
- **A puck enclosure:** a self-righting ball for one board and a cell, and a command brick for the Arduino UNO Q, designed parametrically and checked in software (not printed).

## Challenges

- **The product changed under us three times in one day**: from tracking crews, to a radar that sees through smoke, to finding victims by their phones. Each turn kept what was true: the pre-plan agent, the map, the reflex layer, the honest labels.
- **Saying only what the measurement supports.** It is tempting to draw a dot for every phone. A distance to one puck is a ring, two pucks give two places, and devices heard without an app are not people. The board, the agent's prompts, the feeds and the report all follow that rule.
- **The sponsor data was not shaped like our guess.** The CoStar adapter we wrote before the event assumed an HTTP API; the kit was a data extract, so the adapter was rewritten to read files through one mapping table. Our first building had no current tenants in the record, so we changed buildings. And none of it may leave the machine, so the public repository runs on stand-ins and the public sync checks every file against a private list of that building's names.
- **The hardware we expected was not the hardware we had.** The boards are DWM3001CDK kits, whose radio cannot be driven as a radar with the firmware we had, and whose stock firmware ranges an iPhone but not other boards. Phones became the hardware path; the radar and crew tracking became honest simulation-only modes.
- **Replaying a model honestly.** Every prompt is built from discrete facts (rooms, graph nodes, whole metres), so a recorded real run replays exactly while the positions underneath stay noisy.

## Accomplishments

- The same code ran on all three live sponsor layers for a real building and carried a phone-rescue exercise end to end on it.
- The building's own data does measurable work: with two pucks, the twin's walls turn some "two possible places" into one point (lane L16's simulated evaluation, in the two-puck section of `results/RESULTS.md`).
- An end-to-end command post that runs hands-free on the stand-in data, with every agent turn replayed from a recorded real model run, a printable report and standard feeds.
- A straight answer to the owner's radar question, with arithmetic, sources and the biggest open risk named.
- Every claim on the screen says where it comes from: live, stand-in, simulated, sample and replay badges, and the source of each agent turn.

## What we learned

A stored map of the built world can do more than display a position: it can decide between possible positions. The building record can do more than list tenants: set against live evidence, it says who is missing. And a measurement drawn as more certain than it is can send a crew to the wrong room.

## What's next

- The first run on the owner's pucks and iPhones (nothing has run on the boards yet), and a measured range error against a tape.
- Scan the whole storey of the live building, not one suite, and correct the scan's georeference, which lands about 90 m from the record's map point.
- A third puck per room; the paired background mode so a phone keeps answering with the screen off; multi-floor routing.

## Built with

Python, FastAPI, three.js, Claude (Anthropic API / Claude CLI / replay of recorded runs), the CoStar hackathon data kit, the Matterport Model API and Showcase SDK, Vexcel API v2 (all three used privately, not redistributed), Qorvo DWM3001CDK (stock Nearby Interaction firmware), Apple Nearby Interaction (lane L16's iOS source), Arduino UNO Q, the bench's uwb-kit (simulator), GeoJSON, Cursor-on-Target, public open data from Los Angeles County, the City of Los Angeles, the US Census and USDA NAIP.
