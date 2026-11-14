# Sandtable — lead's brief (lane L01, CoStar x UWB flagship)

Lead: Claude Fable 5.2. Written Sat Nov 14 2026, 09:25 CST. Target: CoStar "Inside and Out: AI for the Built World" (30% use of the data, 30% agentic depth, 25% usefulness, 15% demo and polish). Also entered: Best Hardware Hack, Best Physical AI, Amazon Practical AI. Main track: Education.

## 1. What research found (Phase 1)

- **Vexcel** publishes its full OpenAPI 3.1 spec without a key (`https://api.vexcelgroup.com/v2/api-docs`, 67 paths: `/ortho/extract`, `/ortho/collections`, `/oriented/query`, `/oriented/extract`, `/oriented/transform-points` (world coordinate to image pixel and back), `/dsm/elevation`, `/dtm/elevation`, `/structure/extract`, `/property/extract`, `/elements/detect`, `/embedding/*`). Its MCP server is at `https://api.vexcelgroup.com/v2/mcp` (`X-API-KEY` header; answered `401 Unauthorized request` from this machine, so it is real and key-gated).
- **Matterport** has a GraphQL Model API (`https://api.matterport.com/api/models/graph`, token id + secret), a Property Intelligence API (rooms, dimensions), a **GeoCoordinate API** (model coordinates to latitude/longitude) and the Showcase SDK (`Sweep.moveTo`, `Camera`, `Tag`, `Scene`, sweep graph + A*). GeoCoordinate plus Vexcel `transform-points` means one physical point can be carried through twin, map and oblique aerial pixel.
- **CoStar** has no public API. Its public field vocabulary (RBA, building class, stories, year built, construction material, sprinklers, elevators, parking ratio, tenants with suite/floor/SF/industry/lease expiration, owner and property-manager contacts, available space) is what the fixture record is shaped from.
- No toolkit or keys in `.secrets/` at 09:11 CST. Everything sponsor-side is built as live + labelled fixture adapters.
- What 80 teams will build on this data: broker chatbots, site selection, investment memos, parking counters, conversion/solar feasibility, virtual tour guides, ADA checkers from twin imagery. None will have a live physical-position layer.

## 2. Ideation tournament

Scores 1-5. Weights: track fit x3, ah-ha in a 2-minute demo x2, distinctiveness x2, feasibility x2, creativity/utility/charity/avidity x1 each, owner bring-up risk x1 (5 = safest). Max 70.

| # | Concept | Fit x3 | Ah-ha x2 | Distinct x2 | Feasible x2 | Cre | Uti | Cha | Avi | Bring-up | Total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **Curbcut**: accessible curb-to-suite wayfinding (Vexcel finds the accessible stall and entrance, twin routes inside, CoStar resolves "the dentist in suite 300", UWB is the indoor GPS) plus an "Access Score" per building | 4 | 4 | 3 | 4 | 3 | 4 | 5 | 4 | 4 | 54 |
| 2 | **Census Walker**: the three layers audit each other, the agent plans the shortest walk that resolves the disagreements, a researcher with a tag is dispatched to exact spots and evidence-backed corrections are filed | 5 | 3 | 5 | 4 | 4 | 4 | 2 | 4 | 4 | 57 |
| 3 | Last-50-metres courier/robot guidance (dock from the air, route in the twin, UWB tracks the courier) | 3 | 3 | 3 | 4 | 3 | 3 | 2 | 3 | 4 | 44 |
| 4 | **Sandtable**: fire pre-plan from all three layers, then live crew accountability inside the twin (mayday detection, rescue routing, re-planning), on a tabletop exercise rig or 1:1 | 5 | 5 | 4 | 4 | 4 | 4 | 5 | 5 | 4 | **63** |
| 5 | Showing copilot: "web analytics for a physical tour" (dwell heat-map of a prospect's walk, agent writes the tailored proposal from lease comps) | 4 | 3 | 4 | 4 | 4 | 3 | 1 | 3 | 4 | 49 |
| 6 | Twin drift: people "walking through walls" in UWB traces tell the agent the twin and the record are stale; it checks open permits and files a rescan | 3 | 4 | 5 | 4 | 5 | 2 | 2 | 3 | 3 | 50 |
| 7 | Deal table: tangible pucks on printed aerial and plans drive the three layers for a deal team around a table | 3 | 4 | 4 | 4 | 4 | 2 | 1 | 4 | 4 | 48 |
| 8 | Leak tracer: hold the tag under a ceiling stain, the agent projects the point up to the roof in the aerial, inspects it and files the work order | 4 | 4 | 5 | 3 | 5 | 3 | 2 | 3 | 4 | 53 |
| 9 | Evacuation-drill auditor: tagged occupants' real egress versus code travel distances and exterior assembly points | 3 | 3 | 4 | 4 | 3 | 3 | 4 | 3 | 4 | 48 |
| 10 | Physical-occupancy truth for lenders: UWB presence inside plus cars counted from the air versus leased occupancy | 4 | 2 | 4 | 3 | 4 | 4 | 2 | 3 | 2 | 45 |
| 11 | Inspection proof-of-presence for lenders and insurers (the agent plans the inspection, UWB proves every suite was entered) | 4 | 3 | 4 | 4 | 3 | 4 | 2 | 3 | 4 | 50 |

## 3. The pick: Sandtable (concept 4), with grafts

**One line:** slide a firefighter token across a printed floor plan and it moves inside the real building's twin; an AI command agent pre-plans the building from CoStar + Vexcel + Matterport and runs the mayday live from UWB positions.

Why it wins:

- **Each layer does a job the others cannot.** CoStar record: construction, sprinklers, stories, tenants and their life hazards, vacant suites, key holders. Vexcel aerial: the four sides of the building (the fire service's A/B/C/D), apparatus access and staging, hydrants and exposures, the outside leg of the hose stretch, building height. Matterport twin: stairs, standpipes, corridors, travel distances, interior routes. UWB: where the crews are right now, which no stored layer can know.
- **The agent has real work.** It plans (pre-plan, attack stair, anchor drops), acts (radio orders, assignments, tags written to the twin, discrepancy filed against the record) and re-plans when the world changes (stair lost, rescue team off route). A deterministic reflex layer raises the mayday in milliseconds; the LLM command agent deliberates with tools on top of it.
- **The table is not a trick.** Fire academies already train with tabletop exercises on printed plans. Exercise mode (scaled rig) is a real product that can be switched on Monday with five boards and a printer; live mode is the same stack at 1:1 with anchors dropped on the fire floor. That is how a table at Princeton honestly becomes a building in Houston or Los Angeles.
- **Charity is obvious**, and the ah-ha is physical and lands in the first 30 seconds.

Grafted from the runners-up: from Census Walker, crews' position-stamped observations are written back to the twin as tags and disagreements between layers are filed as record corrections; from Twin drift, a crew crossing a wall that the twin says is solid is flagged as "twin stale"; from the Deal table, the tangible token interface; from Leak tracer, the same point is shown in every layer at once (plan, twin, aerial).

Why not the others: Curbcut is on CoStar's own list, so several teams will do accessibility, and indoor navigation for blind users is a familiar hackathon theme. Census Walker is the closest to CoStar's heart but has a quiet demo and little charity; its best parts are grafted in. The rest lose on demo or usefulness.

**Main track: Education.** The product that is deployable on Monday is the training mode: interactive, physical exercises on the real buildings in a company's district. That is an honest fit ("tools for virtual experiences", "more interactive learning").

## 4. Mapping to the CoStar rubric

| Criterion | How Sandtable meets it |
|---|---|
| Use of the data (30%) | All three layers, joined by one coordinate chain: UWB site frame -> twin frame -> latitude/longitude -> aerial pixel. Hose stretch is measured across the aerial (engine to door) and the twin (door to fire room). Tenants from the record become life-hazard priorities on the floor plan. Building height and sides come from the aerial. |
| Agentic depth (30%) | Tool-using command agent with a visible plan -> act -> re-plan loop over spatial tools (locate, route, hose stretch, anchor plan), record tools, an aerial vision tool and action tools (radio, assign, mark hazard, tag the twin, file a correction). Two tiers: reflex (deterministic, 10 Hz) and deliberate (LLM). |
| Usefulness (25%) | Training officers run Monday's drill on a real building in their district; prevention bureaus get a pre-incident plan draft for any commercial address; incident commanders get continuous accountability instead of radio roll calls. |
| Demo and polish (15%) | One-screen command board: Outside (aerial), Inside (3D twin), Command agent (timeline, radio, accountability). A physical rig a judge can touch. |

## 5. Two-minute storyboard

| Time | Beat |
|---|---|
| 0:00-0:20 | "The first crew in has never seen the inside of the building. CoStar has." Dispatch the alarm for a real Houston/LA address. The agent builds the pre-plan live: record facts and life hazards, the four sides from the air, stairs and standpipes from the twin, hose stretch, anchor drops. |
| 0:20-0:50 | **Ah-ha (about 0:30):** slide the Engine 1 token across the paper plan; the crew moves inside the 3D twin, and the same point lights up on the aerial and in the record ("Suite 310: dental office, oxygen in use"). |
| 0:50-1:20 | Hold the token still. The reflex layer raises a MAYDAY; the agent routes the rescue team through the twin and speaks the radio order. |
| 1:20-1:40 | Inject "east corridor lost". The agent re-plans via the other stair and re-briefs every crew. Contact made; the agent plans the way out. |
| 1:40-2:00 | All out. After-action report: timeline, observations pinned in the twin, one record discrepancy filed. Close: "exercise today on a table, the same five boards 1:1 on a real floor." |

## 6. Work packages

Planned as five parallel Opus builders with disjoint file ownership (sponsor adapters and open data; twin and spatial engine; server, reflex layer and simulator path; command agent; command-board UI), with the contract in `docs/CONTRACTS.md`. When the five were spawned at 09:33 CST the bench-wide cap of 20 concurrent subagents was already reached and the tool refused, with an instruction not to retry. Per the playbook the lead then built every package itself, in the same order and against the same contract. The skeptical-judge pass in section 7 was also done by the lead.

## 7. Skeptical review (lead, 10:45 CST) and what was done about it

| Weakness a judge would press on | Response |
|---|---|
| No sponsor data is used at all: every layer is a stand-in | True and stated on screen and in the README. Real open data for a real Los Angeles building makes the stand-in truthful; live adapters are written from the public Vexcel spec and Matterport samples and unit-tested against mocks; `docs/TOOLKIT_SWAP.md` is the procedure. The owner must plug the toolkit in at the event for this criterion to score fully |
| The agent looks canned: a replay, and the first prompts told it which tools to call in which reply | Prompts rewritten as goals, required end states and a reply budget; the model now chooses its tools. Re-recorded. Every turn is labelled with its source; a test fails if a replay turn misses; with a key every decision is live |
| UWB has never run on boards | True. The guided exercise mode, the two-point calibration, the rig layout and the bring-up guide are what make the first real run short. A recorded profile with four anchors and one tag lets the hardware demo run without an API key |
| The interior is synthetic, so the tactics are about a plausible floor, not the real one | Labelled in the view title, the footer and the pre-plan items. It becomes real with one Matterport model id |
| "Why tags? Drag icons on a tablet" | Answered in `PITCH.md`: drills are physical and multi-person, and only this version has a path to a real floor |

## 8. Owner redirect, 11:45 CST: Scout mode becomes the headline

The owner's picture of Sandtable: "you throw this like a grenade into a room and it scans and creates a digital twin/map of the room. This wins over lidar and other visual based systems since the smoke would block them, but smoke won't block UWB radar." What had been built tracks tagged crews inside a twin that already exists. The gap is closed by building the owner's idea on top, and by saying plainly what the physics allows:

- One board is not a radar (half-duplex ranging transceiver). A pair gives one ellipse per echo. Several pairs locate. So the "grenade" is a handful of pucks that first range each other.
- At about 500 MHz of bandwidth the radar resolves about 0.3 m. It finds what moves or breathes; walls give a coarse outline. It does not make a LiDAR-grade model.
- Therefore the twin is the map, and the pucks add what no stored layer has and smoke hides from cameras: an untagged person, their breathing, and where the room no longer matches the twin. This is also the strongest form of the CoStar argument: the data layers are what turn a weak mapper into a useful one.

Decisions (lead):
- **Scout mode is the new first act of the demo**: pre-plan from the three layers, pucks thrown into the smoke-filled fire room, the scan lands on the command board, a breathing victim appears in the twin, the agent routes the rescue. The mayday exercise stays as a second script.
- **Radar code is reused from lane L08** (`tagless-uwb-echo-radar`), copied into this tree with credit and with its caveats.
- **Hand-off is a feed**: GeoJSON and Cursor-on-Target, read by the phone crew page as a second client. Not tested against a TAK server unless one is actually run.
- **Hardware is a ladder**: CIR radar; link-shadow tomography on stock ranging firmware; tagged crews; replay.
- **Main track changes from Education to Healthcare.** The headline is no longer a training tool: it is contactless detection of a breathing person through smoke and getting rescuers to them, which is emergency care before the hospital. Education fitted the exercise mode, which is now the second act.
- Build: eight parallel Opus builders against `docs/SCOUT_CONTRACT.md` (radar engine; registration and twin comparison; session, server, feeds and script; agent; board; feasibility write-up; hardware ladder; puck enclosure), then evaluation, write-ups and an independent judge.

## 9. Second owner direction, 11:55 CST: three sensing modes on one board

The owner now sees Sandtable as three ways to find people: tracking firefighters (tags), UWB radar scanning the room (pucks), and ranging victims' phones over their own UWB. The orchestrator split it: **Crews** and **Scout** stay in this tree; **Beacon** (victims' phones) is a sibling lane, L16, in `../sandtable-beacon-victim-phones/` on port 1885, publishing a feed (`docs/BEACON_FEED.md` there). This lane, after the Scout builders land: a Phones layer on the board that consumes that feed and tolerates its absence, drawn honestly (a ring is a ring); an agent tool and situation for a located phone; the product presented as three modes (Crews, Scout, Beacon) with one honest line each; and at packaging, L16's module vendored here with credit and its `owner_questions` shown beside ours.

## 10. Third owner direction, 12:10 CST: full lock on Sandtable Beacon

"Lets full lock on sandtable beacon, tracking the victims by their phone and having virtual map of the room via costar etc for the command and reporting center/dashboard." The owner's boards are confirmed as five Qorvo DWM3001CDK (nRF52833 + DW3110, Bluetooth on board); the victims' phones are an iPhone 14 and an iPhone 15.

Decisions (lead):
- **The product is Sandtable Beacon; this tree is its command and reporting centre.** Occupants located by their phones (lane L16's module and feed, `../sandtable-beacon-victim-phones/`, vendored here at packaging) appear in the room on a map made from the three CoStar layers; the agent prioritises, routes a crew, issues the order, re-plans, and a report is written for the command post.
- **Phones are drawn as what the measurement supports**: a ring as a ring, two candidates as two candidates, a point only for a fix; devices heard without the app are a count band, never people. The accountability view sets the building record's tenants against the phones still inside.
- **Scout (radar) and Crews (tags) become secondary, simulation-only modes** behind a mode switch, each with one honest line. The ESP32 firmware (L08's echo reader, the kit's ranging) does not run on nRF52833 boards and there is no nRF toolchain here, so neither is attempted on hardware. The radar feasibility answer the owner asked for stays.
- Builders re-tasked mid-flight: R3 (server) to the Beacon link, accountability, report and the `beacon` exercise script; R4 (agent) to phone situations and tools; R5 (board) to the Phones layer, accountability panel and mode switch; R8 stops ESP32 firmware and rewrites the hardware guide; R9 retargets the enclosure to the DWM3001CDK with a battery bank and a UNO Q host variant; new builder U2 builds the reporting page. Contract: `docs/BEACON_BOARD_CONTRACT.md`.
- Main track stays Healthcare (emergency care before the hospital: finding trapped people and getting rescuers to them).

## 11. Sponsor keys arrive, 12:55 CST: live layers in private

Matterport and Vexcel keys are on the build machine; CoStar is a password-protected data extract that has not arrived yet. Owner's hard rule: "don't push the data at all", while the tree is being published to a public repository every 15 minutes.

Decisions (lead):
- Three builders take the layers live now: Matterport (correct the adapter against the real GraphQL schema, pick a scanned commercial suite in Los Angeles or Houston as the demo building, normalise it into the twin, Showcase SDK view on localhost), Vexcel (real ortho, obliques, structure attributes for that building; the agent's vision tool run on real imagery), and plumbing (CoStar read from a local extract folder through one mapping table, public context for any LA or Houston address, `.env.example`, the self-check, the swap guide rewritten from what was actually done).
- **Privacy by construction**: everything fetched or derived in live mode lives under a path containing a directory named `live` (`data/live/...`); the model replay cache switches to a private directory in live mode; `fixtures/llm_cache/` only ever holds recordings made against fixture data; the publish check refuses key values and tracked `live` paths. The public repository keeps the fixture-mode demo exactly as it is; live mode is what the owner shows at the table.
- Prefer a Los Angeles building: the open-data pipeline for hydrants, footprint and parcel already works for City of Los Angeles addresses.
