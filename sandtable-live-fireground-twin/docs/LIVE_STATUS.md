# Live status of the sponsor layers

This page holds no sponsor data. It says which layers have been read live, on what kind of building, what is still a stand-in and what broke. How to go live is in `docs/TOOLKIT_SWAP.md`. Everything read live stays under `data/live/` and is never committed.

## Exercised live (Saturday afternoon, bench machine, the event's keys and data kit)

| Layer | Status | What was read |
|---|---|---|
| CoStar | **read live** from the CoStar data kit (a local extract, not an API) | The demo building's record (property, contacts, current tenants by suite and floor with employees, industry and lease expiration, available spaces, sale and lease comparables, the property's Matterport model ids). `COSTAR_MODE=live python -m sandtable.adapters.check` prints the counts. |
| Matterport | **read live** (Model API, GraphQL) | The scan of one space of the demo building, normalised into the twin: 1 floor, 8 rooms, 30 sweeps, a 199-edge walking graph. Tags are written as a dry run by default. |
| Vexcel | **read live** (API v2) | 7 images of the demo building (2 ortho, 1 detail, 4 obliques), cached under `data/live/vexcel/` so the demo also runs offline. |
| Public context | **real open data** for the demo building | Footprint, the four sides, hydrants, fire stations and street geometry, from Los Angeles open data (`scripts/build_site.py --from-demo`). |

**The building.** A Class A, 12-storey, sprinklered office building in Los Angeles that is on CoStar's Matterport list. The Matterport scan covers one suite on one storey, not the whole building.

## What ran end to end (Saturday 14 November 2026, about 1:05 to 1:35 pm CST)

- **CoStar** (the data kit): 26 property fields, 72 current tenant rows, 18 available spaces. Elevators, height, parking and percent leased are empty in that record.
- **Matterport** (Model API): the scanned suite as 8 rooms with real boundaries, 48 walls, 14 doorways, 30 scan positions and a 199-edge walking graph. The Showcase SDK view was embedded and steered on `localhost`.
- **Vexcel** (API v2): 7 images (orthos, a roof detail, four obliques). The agent's vision tool read the roof and the parking on them.
- **The exercise:** the board ran a phone-rescue exercise end to end on that building with a live model: a pre-plan citing each layer, both simulated phones located and brought out, and a report with its provenance. Phones, pucks and crews were simulated.
- **Known limits:** the scan covers one suite, not the storey; the scan's georeference lands about 90 m from the record's map point (the pre-plan flagged it; not corrected); the first live run needed one order given by hand (fixed since).
- Hands-free live replay: see the matching line in the README's "Sponsor data" section.

None of this data is in the public repository: it runs on stand-ins (real open data for a different building and a synthetic twin), labelled as not the sponsor's.

## Still a stand-in, or not in any layer

- **The interior outside the scanned space.** Only one space of the building was scanned. Other storeys have no rooms, and routing works only inside the scan. The fixture building keeps its synthetic full-floor twin.
- **Fields the CoStar record does not hold for this building:** elevators, building height, parking spaces and percent leased. Height comes from the open-data roof height instead, with its source named.
- **Entrances and the apparatus position** are inferred from the footprint and the nearest street. They are flagged `synthetic: true`.
- **Life hazards per tenant** are inferred from the industry by a rule table and marked "inferred". They are not observed.
- **Contact phone numbers** in the CoStar kit are fake by design. Names are real.
- **Model decisions on live data** need `LLM_BACKEND=anthropic`, or a private recording in `data/live/llm_cache/`. The recordings in `fixtures/llm_cache/` belong to the fixture building and are never used, or written, in a live process. Without either, the agent runs on the deterministic fallback policy and says so.
- **Houston** public context: there is no keyless source for year built, building area, stories or tenants by suite. The CoStar record covers them.

## What broke, and what was done

- **The CoStar adapter written before the event assumed an HTTP API.** CoStar delivered a data extract (JSON Lines, integer enums, a model-id CSV), so the adapter was rewritten to read files through one mapping table, with fuzzy column matching for other extract layouts.
- **The first building candidate had no current tenants in CoStar.** Every tenancy had a move-out date. The demo building was changed to one with current tenants.
- **The first building denylist blocked the public sync.** A single-word tenant name was also an ordinary brand word. Names now need two words or ten letters, matching is on whole words, and dropped terms are listed privately with the reason.
- **Matterport's model id.** When `MATTERPORT_MODEL_ID` is not set, the adapter takes the model id, storey and suite from `data/live/demo_building.json` (which only exists on a machine that has the private bundle). With neither, `check --live` fails for Matterport and names both.
- **The Showcase SDK key works only on `localhost`.** The real-scan page must be opened at `http://localhost:<port>`. For the phone, use `adb reverse`.

## Privacy writers not yet switched (requests to their owners)

`scripts/shots.cjs` writes screenshots into `figures/` and `results/ui_walk.json`. `scripts/check_replay.py` and `scripts/eval_sim.py` write into `results/`. `scripts/record_all.sh` writes logs into `data/` (git-ignored, but not under `live/`); `run_demo.sh --daemon` now logs to `data/live/` in live mode. The others do not know about live mode yet. **Do not run them against a live server** until they use `config.output_dir(...)`, or ask the server whether a layer is live (`GET /api/bundle` -> `labels.<layer>.mode`).
