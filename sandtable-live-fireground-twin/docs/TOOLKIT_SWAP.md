# Going live with the sponsor layers

Sandtable reads three sponsor layers through three adapters. Each has a `fixture` mode (the public demo, what runs out of the box) and a `live` mode. The layers switch independently, so you can go live one at a time and keep the demo working in between. This page was rewritten from what had to be done when the sponsor keys and the CoStar data kit arrived; the commands below are the ones that were run.

## The rule: nothing live is ever committed

Sponsor data, and everything made from it, never goes into the repository. That covers raw responses, caches, normalised bundles, annotated images, screenshots, model recordings made against live data, and results. All of it lives under a directory named `live`, which is `data/live/` in practice. `data/` is git-ignored, `.gitignore` also ignores any `live/` directory anywhere, and the public mirror skips every path that has a `live` directory in it. The public repository keeps only the fixture-mode demo.

How the code enforces it, so that nobody has to remember:

- `sandtable/config.py` defines the private root, `config.LIVE_DIR` (`<data dir>/live`). As soon as any layer is live in a process, the whole process switches over for good. A layer goes live either from the environment at start-up, or when `build_provider()` (or `Provider`) is given a live layer. After the switch:
  - `config.DATA_DIR` points to `data/live/`. Every runtime writer that goes through config therefore writes privately: the tag overlay, record corrections, the annotated aerials the agent's vision tool sends to the model, and the server's `/live/vexcel` images.
  - The model replay cache becomes `data/live/llm_cache/`. `fixtures/llm_cache/` is read-only for the process: `LLM` refuses to record into it.
  - `config.guard_write(path)` raises `PrivacyError` for any write outside a `live` directory. `config.output_dir("results"|"figures")` gives a writer its private folder.
- `bash scripts/check_publishable.sh` runs before every commit and refuses the tree if any publishable file (tracked, or untracked and not ignored) does any of the following:
  - contains a key value from `.env` or the bench `.secrets/*.env`. The values are read at check time and never printed.
  - contains a term from `data/live/denylist_building.txt`: the building's id, its address lines, its names, its tenant and contact names, and its model ids.
  - contains any Matterport model id from the CoStar kit.
  - sits under a `live` directory.
  - is imagery over 200 KB outside `fixtures/aerial/` and `figures/`.

  It prints file names and reasons only.
- `tests/test_live_privacy.py` runs a pre-plan with a fake live provider while recording every file write in the process. It asserts that nothing was written outside `live`, that the fixture cache is untouched, and that no tracked path is under `live`.

## Laptop setup: Windows or Mac (about 15 minutes)

Three things travel by hand, never through the repository: the private `.env` (keys, the property id, the model id), the CoStar kit zip, and the laptop bundle `sandtable-live-bundle.zip`. The bundle comes from `python scripts/pack_live_bundle.py pack` on the machine that ran live (it writes `data/live/bundle/sandtable-live-bundle.zip`, a git-ignored path), and holds everything that live run used: the chosen building, its public context, the CoStar index of the building, the Matterport and Vexcel caches, the private agent recordings and the Beacon copy. It contains sponsor data and no key values. Never commit it, upload it or attach it.

**Windows (PowerShell).** Python 3.12 or 3.13 from python.org; `py` is the launcher.

```powershell
git clone <repository url> sandtable; cd sandtable
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt openpyxl pyzipper
Copy-Item <path to the private .env> .env
# the CoStar kit: OUTSIDE the repository. 7-Zip works; Windows Explorer cannot open AES zips. Or:
.venv\Scripts\python -m sandtable.adapters.costar_extract unzip <path>\costar-hackathon-kit.zip --out C:\costar-kit
#   then add the line  COSTAR_KIT_DIR=C:\costar-kit  to .env
.venv\Scripts\python scripts\pack_live_bundle.py unpack <path>\sandtable-live-bundle.zip
.venv\Scripts\python -m sandtable.adapters.check --live
$env:SANDTABLE_DATA_MODE="live"; $env:LLM_BACKEND="anthropic"; .venv\Scripts\python -m sandtable.demo
```

- **The unzip password.** The `unzip` command reads it from `COSTAR_KIT_ZIP_PASSWORD` in `.env`.
- **WSL.** Inside WSL, follow the Mac steps (bash) with `python3`. WSL2 forwards `localhost` to Windows by default, so the Windows browser can open `http://localhost:1810`.

**Mac (zsh).**

```bash
git clone <repository url> sandtable && cd sandtable
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt openpyxl pyzipper
cp <path to the private .env> .env
.venv/bin/python -m sandtable.adapters.costar_extract unzip ~/Downloads/costar-hackathon-kit.zip --out ~/costar-kit
echo "COSTAR_KIT_DIR=$HOME/costar-kit" >> .env
.venv/bin/python scripts/pack_live_bundle.py unpack ~/Downloads/sandtable-live-bundle.zip
.venv/bin/python -m sandtable.adapters.check --live
SANDTABLE_DATA_MODE=live LLM_BACKEND=anthropic .venv/bin/python -m sandtable.demo
```

**Then, on both:**

- **Open the board at `http://localhost:1810`.** The Matterport Showcase SDK key is restricted to `localhost`: an IP address or a host name shows no real scan. For the phone, use `adb reverse tcp:1810 tcp:1810` and open `http://localhost:1810` on the phone.
- **What unpack reports.** `unpack` checks every file against the manifest's hashes and prints what is still missing on the machine: keys, the kit, the property id. `check --live` must say OK for all three layers.
- **Keep the modes on the command line, not in `.env`.** The launcher reads the mode from the environment of the command. A per-layer mode written in `.env` would also stop the fall-back below from working.

**Fall-back to fixture mode at the table (one step).** Stop the board (Ctrl+C) and start it without the live variables. On a Mac: `.venv/bin/python -m sandtable.demo` (or `bash run_demo.sh`). In PowerShell, open a new window and run `.venv\Scripts\python -m sandtable.demo`, or first run `$env:SANDTABLE_DATA_MODE="fixture"`. The public fixture demo then runs: real open data, the synthetic interior and the recorded model runs, with no keys and no internet.

**Variables live mode reads** (names only; all are listed in `.env.example`):

| Variable | Needed | Notes |
|---|---|---|
| `SANDTABLE_DATA_MODE`, or `COSTAR_MODE` / `MATTERPORT_MODE` / `VEXCEL_MODE` | yes | Set on the command line. |
| `COSTAR_KIT_DIR` | without the bundle | With the bundle, the building's record is read from the private index `data/live/costar/_index/`, and the kit is needed only for another building. |
| `COSTAR_PROPERTY_ID` | if `data/live/demo_building.json` is absent | `COSTAR_PROPERTY_ADDRESS` also works. |
| `COSTAR_KIT_ZIP_PASSWORD` | only for `costar_extract unzip` | |
| `MATTERPORT_TOKEN_ID`, `MATTERPORT_TOKEN_SECRET`, `MATTERPORT_MODEL_ID` | the two token variables always; the model id only without the bundle | When `MATTERPORT_MODEL_ID` is not set, the adapter takes the model id, storey and suite from `data/live/demo_building.json`; an explicit value wins. |
| `MATTERPORT_SDK_KEY` | for the real-scan view | Works on `localhost` only. |
| `MATTERPORT_STOREY`, `MATTERPORT_SUITE` | if `demo_building.json` is absent | They place the scanned space in the building and join it to its tenant. |
| `MATTERPORT_EXERCISE_FLOOR`, `MATTERPORT_REFRESH`, `MATTERPORT_WRITE_TAGS` | optional | |
| `VEXCEL_API_KEY` | yes | `VEXCEL_OFFLINE=1` serves only the cache, with no internet. |
| `LLM_BACKEND`, `ANTHROPIC_API_KEY` | for live model decisions | Without them, the private recordings from the bundle replay (`LLM_BACKEND=replay`). |
| `SANDTABLE_SITE` | if `demo_building.json` is absent | Point it at the building's public-context folder. |
| `HOST`, `PORT`, `BEACON_PORT` | optional | Defaults: `127.0.0.1`, 1810 and 1885. |

**What the laptop can rebuild by itself, from the kit and the keys,** if the bundle is missing a part or you change the building:

| Part | Command |
|---|---|
| CoStar index of the building | `python -m sandtable.adapters.costar_extract record` (with `COSTAR_KIT_DIR` and the property id) |
| Public context | `python scripts/build_site.py --from-demo`, or `python scripts/build_site.py --address "<street address>" --city la\|houston --out data/live/site/<slug>/ --lat <lat> --lon <lon>` (then `SANDTABLE_SITE=data/live/site/<slug>`) |
| Matterport twin and cache | `python scripts/live_matterport.py normalize` (with `--storey N --suite S` when `demo_building.json` is absent) |
| Vexcel images and cache | `python scripts/live_vexcel.py fetch --demo`, or `fetch <lat> <lon>` |
| Publish-check denylist | `python -m sandtable.adapters.costar_extract denylist` |
| Beacon copy on the live twin | re-assembled by the launcher at every live start |

**What only comes from the bundle:** the chosen building (`data/live/demo_building.json` and its shortlist) and the private agent recordings (`data/live/llm_cache/`). Without them, choose the building again (property id, model id, storey, suite), and either use a live model or record a new private run (section 5).

## 0. Keys and settings (2 minutes)

Copy `.env.example` to `.env` (git-ignored) and fill in what you have. The example lists every variable with a one-line comment. `sandtable/config.py` reads `.env` at start-up (and, on the bench machine, the drop zone `../.secrets/*.env`). Nothing prints a key value.

```
python -m sandtable.adapters.check           # each layer: mode, which variables are present (names only), what it read (counts only), what to do
python -m sandtable.adapters.check --live    # tries every layer live, one at a time, and reports each
```

A layer set to `live` with something missing fails loudly. It never falls back to fixture while claiming to be live.

## 1. CoStar: a local data extract, not an API (10 minutes)

The CoStar layer is a CoStar Data Extract, a password-protected zip that you unzip on the laptop. The hackathon's kit has this layout: `data/*.jsonl`, `schemas/enums.json`, and `commercial-properties.csv` (property id to Matterport model id). Other extracts (CSV, JSON, JSON Lines, Excel) are read too.

```
python -m sandtable.adapters.costar_extract unzip <the zip> --out <folder outside the repository>   # password from COSTAR_KIT_ZIP_PASSWORD; AES zips need pyzipper
python -m sandtable.adapters.costar_extract inspect                 # file names, row counts, column NAMES (never values), what mapped
python -m sandtable.adapters.costar_extract find --city la --type office --has-model   # matches go to a file under data/live/costar/, the terminal gets a count
python -m sandtable.adapters.costar_extract record                  # maps the selected building, prints counts and the fields that came out empty
python -m sandtable.adapters.costar_extract denylist                # writes data/live/denylist_building.txt for the publish check
COSTAR_MODE=live python -m sandtable.adapters.check
```

- **Where the extract lives.** Set `COSTAR_KIT_DIR` to the folder. The default is `data/live/costar/`. On the bench machine the unpacked kit in the drop zone is used when the variable is unset.
- **Which building.** The building is chosen in this order: `COSTAR_PROPERTY_ID`, then the `property_id`, `model_id` (looked up in the model-id CSV) or `address` in `data/live/demo_building.json`, then `COSTAR_PROPERTY_ADDRESS`. Addresses are compared on the house number and the normalised street (St, Street, N., suite numbers all handled).
- **One mapping table.** All mapping lives in `MAPPING` in `sandtable/adapters/costar_extract.py`: our field on the left, candidates on the right. A candidate is a dotted path into a kit record (with `|enum:<Name>` to decode an integer enum) or a column name of a flat file. Flat-file names are matched ignoring case, spaces and punctuation, then by similarity. `inspect` lists every field it could not map.
- **What comes out**, in the `sandtable.site/1` shape:
  - Property: construction, sprinklers and their type, stories, height, class, areas, parking, year built, elevators, the coordinates and the property's Matterport model ids.
  - Contacts: owner and property manager. Names are real; phone numbers are fake by design in the kit, and labelled so.
  - Current tenants (no move-out date): suite, floor (from the property's floor records), square feet, employees, industry and lease expiration. The life hazard is inferred from the industry by the same rule table the fixture uses, and marked "inferred".
  - Available spaces from the active listings.
  - Sale comparables, and lease comparables from the lease deals.
- **Speed.** The kit is about 400 MB. It is indexed once into `data/live/costar/_index/`: a per-property cache, plus a small index for `find` and address lookup. After that a read is instant.
- **Corrections.** The extract is read-only, so `file_correction` writes the correction to `data/live/record_corrections.json` for CoStar research. Nothing is sent.

## 2. The public context of the live building (2 minutes)

Some facts are not CoStar data: the footprint, the building sides (A to D), hydrants, fire stations and the street geometry. They come from keyless open data:

```
python scripts/build_site.py --from-demo                                       # address, lat, lon from data/live/demo_building.json
python scripts/build_site.py --address "<street address>" --city la|houston --out data/live/site/<slug>/ [--lat LAT --lon LON]
```

- **Output.** The script writes `site.json`, NAIP aerials under `aerial/`, and the raw answers under `_open/`. It refuses an output folder that is not under a `live` directory. In live mode the CoStar adapter merges this context automatically: `config.site_context_json()` finds `data/live/site/<slug of the demo address>/site.json`, or you can point `SANDTABLE_SITE` at a folder. Context more than 300 m from the CoStar coordinates is not merged, and the site then says which command to run.
- **Los Angeles sources.** LA County Assessor parcels, LARIAC building outlines with roof height, the City of LA business listing (suite, floor and category only; names withheld), the LAFD GeoHub hydrants and stations, Census TIGERweb roads, and USGS NAIP.
- **Houston sources.** All keyless and tested:
  - Harris County Appraisal District parcels: `www.gis.hctx.net/.../HCAD/Parcels/MapServer/0`.
  - City of Houston building footprints with height in metres: `geogimsms.houstontx.gov/.../EMC/BuildingFootprints_Inspections/MapServer/0`.
  - Houston Water hydrants: `.../HW/OutsideWater_gx/MapServer/32`.
  - City of Houston fire stations: `.../DIR/healthandPublicSafety_gx/MapServer/9`.
  - TIGERweb roads and NAIP.
- **Not found for Houston.** No keyless source gives year built, building area or stories (HCAD's public layer has none; they are in HCAD's bulk files). No open listing gives businesses by suite, and no keyless HIFLD fire-station endpoint was found. The CoStar record supplies these.
- **The fixture.** Without arguments the script rebuilds the fixture building. Its output was checked to stay byte-identical: `fixtures/building/site.json` and `fixtures/aerial/*`.

## 3. Matterport (5 minutes)

```
MATTERPORT_MODE=live python -m sandtable.adapters.check
```

- **Connection.** Model API (GraphQL) at `https://api.matterport.com/api/models/graph`, HTTP basic auth with `MATTERPORT_TOKEN_ID` and `MATTERPORT_TOKEN_SECRET`. `MATTERPORT_MODEL_ID` is the part after `?m=` in a showcase link. The queries are in `sandtable/adapters/matterport_queries.py` and were checked against the schema by introspection.
- **Caching.** Every response is cached under `data/live/matterport/cache/` and reused (`MATTERPORT_REFRESH=1` refetches). The normalised twin is cached at `data/live/matterport/twin_<model>.json`.
- **Which part of the building the scan is.** A scan is usually one suite, not the whole building. `MATTERPORT_STOREY` and `MATTERPORT_SUITE` (or `floor` and `suite` in `data/live/demo_building.json`) place it in the building and join it to the CoStar tenant of that suite.
- **Tags.** Observations the agent pins are a dry run by default: the mutation is built and checked, logged under `data/live/`, and the tag is kept locally. `MATTERPORT_WRITE_TAGS=1` really writes them into the model, so use a copy of the model if that matters.
- **Approximations.** `normalize_model()` states in the twin's `source.note` and `source.approximations` what is measured and what is approximated.
- **The Showcase SDK key works only on `localhost`.** The key is restricted to the domain `localhost`. The real-scan view (`web/mp.html`) must be opened at `http://localhost:<port>/mp.html`, not through an IP address or the tailnet name. For the phone, use `adb reverse tcp:1810 tcp:1810` and then open `http://localhost:1810`. The page gets its key and model id from `GET /api/live/config`, which answers only while the server runs with `MATTERPORT_MODE=live`. Nothing is hard-coded.

## 4. Vexcel (5 minutes)

```
VEXCEL_MODE=live python -m sandtable.adapters.check
python scripts/live_vexcel.py coverage <lat> <lon>     # which collections and layers cover the point, and their dates
python scripts/live_vexcel.py fetch --demo             # ortho, context, roof crop, four obliques, structure, parcel, elevation for the demo building
python scripts/live_vexcel.py show                     # what is cached
```

- **Connection.** Vexcel API v2 at `https://api.vexcelgroup.com/v2`, with the key in the `X-API-KEY` header only. The key never appears in a URL, a cache file name, a log line or an error message.
- **Caching.** Every response is cached under `data/live/vexcel/cache/`. A second run makes no network call. `VEXCEL_OFFLINE=1` serves only what is cached, which makes the demo work with no internet once the site has been fetched.
- **Serving.** Images live under `data/live/vexcel/` and the server serves them at `/live/vexcel/`.
- **Calls.** The calls and the response shapes seen live are listed at the top of `sandtable/adapters/vexcel_live.py`. Image placement is read from the XMP packet of each image, not assumed.
- **MCP.** The same key works for the Vexcel MCP server: URL `https://api.vexcelgroup.com/v2/mcp`, header `X-API-KEY`.

## 5. Run live, and make a private agent recording

```
SANDTABLE_DATA_MODE=live LLM_BACKEND=anthropic python -m sandtable.demo                       # live data, live model
SANDTABLE_DATA_MODE=live LLM_BACKEND=anthropic LLM_RECORD=1 python -m sandtable.demo --storyboard   # record a private run
SANDTABLE_DATA_MODE=live LLM_BACKEND=replay python -m sandtable.demo --storyboard             # replay it offline
```

- **The recorded runs in `fixtures/llm_cache/` belong to the fixture building.** They are recordings of real model runs against fixture data and must never receive anything made against live data. With live data the prompts differ, so a live process neither reads from nor writes to them: it uses `data/live/llm_cache/`. A replay in live mode with an empty private cache falls back to the deterministic policy, and says so on the board.
- **Recording.** `LLM_RECORD=1` in a live process writes only to `data/live/llm_cache/`. `scripts/record_all.sh` counts entries in `fixtures/llm_cache/` and is meant for fixture recordings. Do not run it with live data.
- **Badges.** The board's badges and footer labels come from the adapters' modes, so they switch to "live" by themselves.
- **No copies outside `data/live/`.** Do not copy live bundles into `fixtures/` and do not commit them. To keep an offline copy, `curl localhost:1810/api/bundle > data/live/bundle.json`.

## 6. Before every commit

```
bash run_tests.sh
python -m sandtable.adapters.costar_extract denylist    # after choosing or changing the building
bash scripts/check_publishable.sh                       # must print "publishable"
```

Commit only your own paths (`git add <paths>`), never `git add -A`, and never anything under `data/`.

## Every external call in one table

| Call | Where | Documentation |
|---|---|---|
| Vexcel `GET /ortho/collections`, `/ortho/extract`, `/oriented/query`, `/oriented/extract`, `/oriented/transform-points`, `/structure/extract`, `/property/extract`, `/dsm/elevation`, `/dtm/elevation`, `/{layer}/collections` | `sandtable/adapters/vexcel_live.py`, `scripts/live_vexcel.py` | <https://api.vexcelgroup.com/v2/api-docs> |
| Matterport GraphQL (model details, sweeps/locations, floors, rooms, tags, geocoordinates; tag mutation in dry run by default) | `sandtable/adapters/matterport_live.py`, `matterport_queries.py` | <https://developers.matterport.com/api/model/> |
| Matterport Showcase SDK (browser, localhost only) | `web/mp.html`, `web/mp_live.js` | <https://matterport.github.io/showcase-sdk/> |
| CoStar: none (a local extract) | `sandtable/adapters/costar_extract.py`, `costar_live.py` | the kit's `index.md`, `models/*.md` |
| Open data (LA County, City of LA, Harris County, City of Houston, TIGERweb, NAIP) | `scripts/build_site.py` | URLs in the script |
| Anthropic Messages API | `sandtable/agent/llm.py` | official SDK |
