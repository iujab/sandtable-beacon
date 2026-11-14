# Sandtable: the two-minute demo

One sentence a judge can retell: **"They moved a firefighter token on a taped floor plan and it moved inside the building's twin, and an AI commander that had already read the building's record and aerial photo ran the mayday."**

Two ways to run it. Same screen, same script.

- **With the boards (preferred):** four anchors on the corners of the taped plan, one tag in your hand. Start with `python -m sandtable.demo --hub serial:auto --profile 4A1T`, click **Guided exercise** and follow the yellow `YOUR MOVE` cues. Phone on the table at `/crew.html?unit=RIT` with Voice on.
- **No boards (fallback):** `python -m sandtable.demo`, click **Run exercise**. The orange `SIM` badge is on screen; say "this run is simulated" once, out loud.

Before the judge arrives: Reset, sound on, 3D view, the Outside panel on "Site".

## Script (times are for the replayed agent; the whole run is about 80 s, which leaves 40 s for your words)

| Time | You do | You say | On screen |
|---|---|---|---|
| 0:00 | Click **Guided exercise** (or Run exercise) | "This is a real medical office building in Los Angeles. The first crew through the door has never been inside it. The data has." | Alarm. The agent's steps start streaming on the right. |
| 0:08 | Point at the agent log, then the aerial | "The agent is reading three layers for this address. The record: steel frame, three floors, a dentist with sedated patients on the third. The aerial: it looked at the photo and found where a ladder truck can set up. And it caught something: the twin says Stair B lets out on side C, and in the photo side C almost touches the next building. Two layers disagree, so it tells the crew to confirm that exit before sending anyone down it." | Tool calls coloured by layer; the pre-plan summary; attack and evacuation stairs light up in the twin. |
| 0:28 | Pick up the tag. Walk it from Stair A along the taped corridor into Suite 310. | "This tape is the third floor at one to ten. This tag is Engine 1." | **THE AH-HA (0:30 to 0:45).** The crew moves inside the 3D twin; its dot moves on the aerial; the strip under the twin fills in: *Suite 310 · dentists, patients may be sedated, oxygen and nitrous · Stair B 17 m*. |
| 0:45 | Stop. Put the tag down. Hands off. | "One physical point, joined through the twin, the record and the aerial, live. Now Engine 1 stops moving." | "still for 3 s… 8 s". The bar goes red: **MAYDAY**. The phone flashes and speaks. |
| 0:55 | Point at the radio panel | "A reflex layer caught that in milliseconds. The agent decides what to do about it: it sends the rescue team, tells them what the record says is in that room, and asks for a second alarm." | Blue route from Stair A; radio order; second alarm requested; a pin dropped in the twin. |
| 1:10 | (the script loses a corridor; in free play click **Inject hazard**, then a corridor) | "A ceiling comes down across the west corridor." | "LOST" marker. The route goes red, then re-draws the long way round: 72 m. The agent tells every crew why. |
| 1:30 | When the board says contact, walk the tag to Stair B | "Contact. The agent picks the way out, and I walk Engine 1 out with them." | Both crews at Stair B. "PAR complete: all out." |
| 1:45 | The after-action report opens | "The after-action report: the timeline, what it pinned in the twin for the next crew, and two requests filed against the building record, because the recorded area does not match the footprint from the air, and one suite has two registered tenants. Today the sponsor layers are stand-ins, and the screen says so: real public data for this building and a synthetic interior. The adapters for your toolkit are written. On Monday a training officer can run this drill on any building in the district. The same five boards at one to one track the real crew." | Report panel; footer: what is real here. |

If you only have 45 seconds (a judge in a hurry): start at 0:28 with the pre-plan already on screen. Token in, strip fills, hold still, mayday.

## If it goes wrong

| Problem | Do |
|---|---|
| Tag dot jumps or is in the wrong room | Keep talking. Stop the launcher (Ctrl-C), start it again with no boards (`python -m sandtable.demo`), click **Run exercise**, and say that this run is the simulator. |
| Mayday does not fire after 10 s | Click the **Mayday** button. Say "a crew can also call it by hand". |
| Agent log shows `reflex-fallback` | Say so: "no recorded answer for this exact situation, so the deterministic policy acted. With an API key the model decides live." |
| No sound | Read the top radio line aloud. |

## Questions a judge will ask

**"Is that our data?"** No, and the screen says so in the footer and the badges. No CoStar keys or toolkit reached the machine it was built on. The record is real public data for this building: the Los Angeles County assessor parcel, the city's business registrations by suite (names withheld), the county building footprint and height, the city's hydrants and fire stations. The aerial is real USDA NAIP imagery. The interior is synthetic, fitted to the real footprint. CoStar-only fields are empty, on purpose. The three live adapters are written (Vexcel from its public OpenAPI spec, Matterport from its documented GraphQL samples, CoStar as one mapping table) and `docs/TOOLKIT_SWAP.md` is the 30-minute procedure.

**"What does the agent actually decide? Is this scripted?"** Two tiers. The reflex layer is deterministic code: no-motion timer, route invalidation, contact. The command agent is a model with nineteen tools; it chose which tools to call, which stair to attack from, what to radio and what to file. The demo replays a recording of that real run so it works offline and instantly; every turn is labelled `replay`, and with a key it runs live. The injected hazard and the timing are scripted; the agent's responses are not written by us.

**"Did the model really look at the image?"** Yes. The vision tool sends the aerial with the footprint, side letters, hydrants and engine drawn on it. Its answer is in the log: a row of trees between the street and the north wall, thinner just west of the engine; a parking lot on the west side; a second building almost touching the south side, which is where the twin says Stair B lets out.

**"How accurate is the positioning?"** Simulated so far, with the shared kit's noise model: see `results/sim_eval.json` (median error under a metre in the building at 1:10, room-level location right about 96% of the time, mostly losing corridor-versus-doorway calls). On real boards we do not know yet. Each crew carries an uncertainty disc in building metres, so the board never pretends to be more precise than it is.

**"UWB through walls, in smoke, in a real fire?"** Smoke does not affect radio, which is why UWB is studied for firefighter tracking. Walls add bias; anchors would be dropped at stair doors on the fire floor, and the agent plans those drop points from the twin (it reports coverage honestly: four drops are not enough for this floor). We claim the exercise mode today. The live mode is the same software at a scale of one, untested in a building.

**"How is a table in Princeton a building in Los Angeles?"** Two reference points fit a scale, a rotation and an offset between the rig and the twin. At one to one that is a real floor. At one to ten it is a tabletop exercise, which is how fire academies already train.

**"Why real tags? You could drag icons on a tablet."** For a drill, people moving through a taped plan is the point: several crews at once, hands busy, the commander watching a board, which is how tabletop and walk-through exercises are run today. And the tablet version has no path to a real floor; this one is the same code at a scale of one.

**"Who would switch it on on Monday?"** A training officer: print the plan, five boards, a drill on a real target hazard in the district. A fire-prevention bureau: a pre-incident plan draft for any commercial address, with a list of what to verify on site. And CoStar's own researchers get that verify list for free: every disagreement between the record, the twin and the aerial, filed with evidence.

**"Why is this in the Education track?"** The part that is deployable today is training: interactive, physical exercises on real buildings.

**"What was hardest?"** Making a model's decisions replayable. Every prompt is built only from discrete facts (rooms, graph nodes, whole metres), never from noisy coordinates or clock times, so a recorded run replays exactly while the positions underneath are still noisy.
