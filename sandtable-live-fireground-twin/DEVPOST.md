# Sandtable Beacon (Devpost draft)

**Tagline:** Pucks in a burning building hear the occupants' phones; each phone appears in the right room of a map built from the building's own data, drawn only as precisely as the measurement allows, and an AI command agent routes the rescue and writes the report.

**Tracks:** Healthcare (main) · CoStar "Inside and Out: AI for the Built World" · Best Hardware Hack · Best Physical AI Hack · Amazon Best Practical AI Innovation

TODO(owner): team members' names
TODO(owner): Discord usernames
TODO(owner): public GitHub repository link
TODO(owner): video link (2 minutes at most; `PITCH.md` is the script)
TODO(owner): one photo of the real rig (two pucks, two iPhones, the board on screen)

## Inspiration

When a commercial building is on fire, the incident commander's hardest question is who is still inside, and where. The answer arrives slowly: from the people who got out, from the radio, from crews searching room by room in smoke. Two things struck us. First, the building is already known: the record of who occupies which suite, the view from above and the 3D interior all exist for the same address, just not on the fire ground. Second, nearly everyone inside carries a radio that can answer: their phone. We had UWB boards and two iPhones, and a sibling team on our bench was working out exactly what an iPhone can and cannot tell a board. So we built the command post that puts those answers on the building's own map.

## What it does

Sandtable Beacon is a command and reporting centre for one question: who is inside, and where.

1. **Pre-plan.** When the alarm comes in, a command agent reads three layers for the address and writes the pre-incident plan: from the building record, the construction, the floors and which tenants are life hazards (a dentist whose patients may be sedated); from the aerial image, which it actually looks at, the sides of the building and where the engine sets up; from the 3D twin, the stairs, routes and hose stretch. It also lists what the layers disagree about.
2. **Pucks in, phones answer.** A crew puts pucks (Qorvo DWM3001CDK boards with Bluetooth and UWB) in the fire room. Occupants who opened the opt-in app and pressed a status button are ranged by each puck in reach and report how many people are with them and whether they can move.
3. **Drawn honestly.** One puck gives a ring. Two pucks give two candidate points, mirror images of each other. The twin then deletes any candidate that falls outside the building or inside a wall, and if one is left, it is a point in a room, beside the tenant the building record expects there. If both are possible, the board shows both and the order is to search both. Phones without the app are only a device count, never people.
4. **Accountability.** Every suite of the floor: who the record expects (an estimate, stated as a range), phones heard, people reported, located, unaccounted as a range, crew assigned, reached, out.
5. **Command.** The agent prioritises, routes the rescue team through the twin, radios the order with what the record says is in that room, re-routes when a phone's position improves, and requests EMS with the occupants' reported condition.
6. **Report and feeds.** A printable report for the command post, and the same picture as GeoJSON and Cursor-on-Target.

Two secondary modes run in simulation only: **Scout**, the pucks as a multistatic UWB radar that finds an untagged breathing person (and the honest answer to "is one board enough?": no), and **Crews**, tagged firefighters with a mayday exercise.

## How we built it

- **Phones, by lane L16 of our bench.** The Beacon module (vendored in `vendor/beacon/`) runs Qorvo's stock Nearby Interaction firmware on the boards, an iOS app that ranges each puck and reports over Wi-Fi, a server with a locator and tracker that publishes rings, candidates and fixes, and a simulator. Its feed contract forbids publishing a point unless the measurements support one.
- **Sponsor layers through typed adapters**, each with a live and a fixture mode. No sponsor key or toolkit reached the build machine, so the demo runs on fixtures labelled on screen: a real Los Angeles medical office building assembled from public data (county assessor parcel, city business registrations by suite with names withheld, county footprint and height, city hydrants and fire stations), real USDA NAIP aerial imagery, and a synthetic interior fitted to the real footprint. The live adapters are written from the public Vexcel OpenAPI spec and Matterport's documented GraphQL samples; CoStar is one field-mapping table.
- **One coordinate chain:** twin frame, latitude and longitude, aerial pixel. The same point lights up in the plan, the 3D twin, the aerial and the feeds, and a candidate can be tested against a wall.
- **A two-tier agent.** A reflex layer in plain code turns the feeds into situations and keeps assignments, routes and accountability. The command agent is Claude with tools for the record, the aerial (including vision), the twin, the people and actions, in a plan, act, re-plan loop. Tool calls are prompted JSON, so the same loop runs on the Anthropic API, the Claude CLI or a replay cache of recorded real responses.
- **The board and the reporting centre:** a no-build web app with a three.js view of the floor, an SVG plan, the aerial with overlays, a phone page for the rescue team that reads the GeoJSON feed, and a printable report page.
- **The radar mode** adapts lane L08's simulator and DSP; the feasibility answer is arithmetic from datasheets plus cited published work, in `docs/RADAR_FEASIBILITY.md`.
- **A puck enclosure:** a self-righting ball for one board and a cell, and a command brick for the Arduino UNO Q, designed parametrically and checked in software (not printed).

## Challenges

- **The product changed under us three times in one day**: from tracking crews, to a radar that sees through smoke, to finding victims by their phones. Each turn kept what was true: the pre-plan agent, the map, the reflex layer, the honest labels.
- **Saying only what the measurement supports.** It is tempting to draw a dot for every phone. A distance to one puck is a ring, two pucks give two places, and devices heard without an app are not people. The board, the agent's prompts, the feeds and the report all follow that rule.
- **The hardware we expected was not the hardware we had.** The boards turned out to be DWM3001CDK kits, whose radio cannot be driven as a radar with the firmware we had, and whose stock firmware ranges an iPhone but not other boards. Phones became the real hardware path; the radar and crew tracking became honest simulation-only modes.
- **Replaying a model honestly.** Every prompt is built from discrete facts (rooms, graph nodes, whole metres), so a recorded real run replays exactly while the positions underneath stay noisy.

## Accomplishments

- The building's own data does measurable work: with two pucks the twin's walls turn some "two possible places" into one point (lane L16's simulated numbers are in the README's Results).
- An end-to-end command post that runs hands-free on the simulator, with a printable report and standard feeds.
- A straight answer to the owner's radar question, with arithmetic, sources and the biggest open risk named.
- Every claim on the screen says where it comes from: `SIMULATED`, `SAMPLE` and fixture badges, and the source of each agent turn.

## What we learned

A stored map of the built world becomes a sensor's best friend: it does not just display a position, it decides between possible positions. And a measurement drawn as more certain than it is can send a crew to the wrong room.

## What's next

- The first live run with the owner's pucks and iPhones, and a measured range error against a tape.
- Live CoStar, Matterport and Vexcel data for a Houston or Los Angeles building, so the walls that prune a candidate are the real walls.
- A third puck per room; the paired background mode so a phone keeps answering with the screen off; multi-floor routing.
- Recording the agent's Beacon and Scout runs with a live model.

## Built with

Python, FastAPI, three.js, Claude (Anthropic API / Claude CLI / replay of recorded runs), Qorvo DWM3001CDK (Bluetooth + UWB, stock Nearby Interaction firmware), Apple Nearby Interaction (lane L16's iOS app), Arduino UNO Q, the bench's uwb-kit (simulator), Vexcel API v2 and Matterport Model API adapters (fixture mode), GeoJSON, Cursor-on-Target, public open data from Los Angeles County, the City of Los Angeles, the US Census and USDA NAIP.
