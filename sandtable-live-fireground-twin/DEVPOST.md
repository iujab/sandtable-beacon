# Sandtable (Devpost draft)

**Tagline:** Slide a firefighter token across a floor plan and it moves inside the building's twin, while an AI command agent that has already read the building's record and aerial photo runs the mayday.

**Tracks:** Education (main) · CoStar "Inside and Out: AI for the Built World" · Best Hardware Hack · Best Physical AI Hack · Amazon Best Practical AI Innovation

TODO(owner): team members and Discord usernames · the public GitHub link · the video link (2 minutes at most; `PITCH.md` is the script) · one photo of the real rig.

## Inspiration

The first crew into a commercial building has usually never seen the inside of it, and once they are inside, nobody outside knows exactly where they are. Both gaps are data problems. The inside of the building, the view from above and the record of who is in which suite all exist for the same address. What does not exist in any stored layer is where the crew is right now. We had five UWB ranging boards, and fire academies already train on tabletop exercises with printed plans and tokens. So: make the token real.

## What it does

Sandtable is a live incident-command table.

1. **Pre-plan.** When the alarm comes in, a command agent reads three layers for the address and writes a pre-incident plan: from the building record, the construction, the floors, and which tenants are life hazards; from the aerial image, which it actually looks at, the four sides of the building, where a ladder truck fits and what blocks it; from the 3D twin, the stairs, the standpipes and the hose stretch to the worst-case suite. It also lists what the layers disagree about. In our run it noticed that the stair the twin calls the evacuation stair lets out on the side where the aerial shows another building almost touching the wall, and told the crew to confirm that exit.
2. **Live position.** Five UWB boards add the layer nobody stores. Tags are crews. On a scaled rig (tape on the floor at about 1:10, or a printed plan on a table) a token moved by hand moves inside the twin; the same point shows on the aerial, and a strip under the twin joins the layers in words: which suite, who the record says is in there, how far to each stair, how sure the fix is.
3. **Mayday.** A deterministic reflex layer notices a crew that has stopped moving and calls the mayday. The agent then acts through tools: it routes the rescue team through the twin, tells them by radio what the record says is in that room, asks for a second alarm, and pins the location in the twin. When a corridor is lost it re-plans every affected crew and says what changed. On contact it picks the way out.
4. **After action.** A report with the measured timeline, the observations pinned in the twin for the next crew, and verification requests filed against the building record where the layers disagreed.

Two honest modes on one stack: **exercise** (scaled rig, for training officers, usable with five boards and a roll of tape) and **live** (the same software at 1:1 with anchors dropped on the fire floor; not tested in a building).

## How we built it

- **Sponsor layers through typed adapters** with a live and a fixture mode each. No sponsor key or toolkit reached the build machine, so the demo runs on fixtures that are labelled on screen: a real Los Angeles medical office building assembled from public data (county assessor parcel, city business registrations by suite with names withheld, county building footprint and height, city hydrants and fire stations), real USDA NAIP aerial imagery, and a synthetic interior fitted to the real footprint. CoStar-only fields are left empty. The live adapters are written from the public Vexcel OpenAPI spec and Matterport's documented GraphQL samples; CoStar is one field-mapping table.
- **One coordinate chain:** UWB rig frame, twin frame, latitude and longitude, aerial pixel. Two reference points register the rig to the twin (scale, rotation, offset).
- **A spatial engine** over the twin: point-in-room, routing on the sweep graph around blocked rooms, doors, stairs and corridor stretches, nearest exit, hose stretch across the outside and inside legs, anchor placement by line of sight.
- **A two-tier agent.** Reflexes are plain code at 8 Hz. The command agent is Claude with nineteen tools (record, aerial including vision, twin, incident actions) in a plan, act, re-plan loop. Tool calls are prompted JSON so the same loop runs on the Anthropic API, on the Claude CLI and from a replay cache.
- **The shared UWB kit** of our bench provides ranging, solving and the simulator; Sandtable only consumes its stream.
- **The board:** a no-build web app, a three.js dollhouse of the floor, an SVG plan, the aerial with vector overlays, and a phone page that acts as a crew radio.

## Challenges

- **Replaying a model honestly.** We wanted the demo to work offline and instantly without hand-writing a single model answer. The fix was to build every prompt from discrete facts only (rooms, graph nodes, whole metres; no coordinates, no clock times), so a recorded real run replays exactly while the positions underneath stay noisy. A test replays the whole exercise and fails if any turn misses the recording.
- **No sponsor data.** Building a truthful stand-in took real open data and a rule that anything invented is labelled, down to single record fields.
- **Scale.** At 1:10 a 9 cm ranging error is almost a metre of building. Map matching with hysteresis and an explicit uncertainty disc made room-level location stable in simulation.

## Accomplishments

- The agent's use of the layers is not decoration: the aerial changed the plan.
- The whole exercise runs end to end on the simulator, hands-free or guided, and from a recording of a real model run.
- Every claim on the screen says where it comes from: SIM badge, fixture badges, synthetic chips, the model source of each agent turn.

## What we learned

A stored map of the built world becomes something else once a live position is joined to it. Most of the work was the join.

## What's next

- Run it on the real boards (the firmware of the shared kit is compile-verified only) and on live CoStar, Matterport and Vexcel data for a Houston building.
- Multi-floor routing through the stairs, more crews, and the Matterport Showcase SDK for the real interior view.
- A drill library generated per building for training officers, and a pre-plan export in NFPA 1620 form.

## Built with

Python, FastAPI, three.js, the bench's uwb-kit (ESP32 + Qorvo DW3000 UWB boards, simulator), Claude (Anthropic API / Claude CLI / replay), Vexcel API v2 and Matterport Model API adapters, public open data from Los Angeles County, the City of Los Angeles, the US Census and USDA NAIP.
