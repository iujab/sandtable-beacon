# Plugging in the CoStar event toolkit (about 30 minutes)

Sandtable reads the three sponsor layers through three small adapters. Each has a `fixture` mode (what runs out of the box) and a `live` mode. The layers switch independently, so you can go live one at a time and keep the demo working in between.

**What has been exercised and what has not.** Fixture mode is tested end to end. The live adapters were written from public documentation on a machine with no keys and no toolkit: their request building is unit-tested against mocked transports (`tests/test_adapters.py`), and **no live call has ever been made**. Expect to fix a field name or two. Each step below says how to check it.

| Layer | Adapter | Live source | Written from |
|---|---|---|---|
| CoStar building record | `sandtable/adapters/costar_live.py` | the event toolkit (unknown shape) | nothing public: one mapping table to fill |
| Matterport twin | `sandtable/adapters/matterport_live.py`, `matterport_queries.py` | Model API (GraphQL) | <https://developers.matterport.com/api/model/samples/>, <https://developers.matterport.com/api/model/geocoordinates/> |
| Vexcel aerial | `sandtable/adapters/vexcel_live.py` | Vexcel API v2 | the OpenAPI spec at <https://api.vexcelgroup.com/v2/api-docs> |

## 0. Keys (2 minutes)

Put the keys in a file named `.env` in the project folder (it is git-ignored), one `NAME=value` per line. On the bench machine the drop zone `.secrets/*.env` is read as well. Nothing prints a key value.

```
VEXCEL_API_KEY=...
MATTERPORT_TOKEN_ID=...
MATTERPORT_TOKEN_SECRET=...
MATTERPORT_MODEL_ID=...          # the model (space) id of the building you demo, the part after ?m= in a showcase link
COSTAR_API_KEY=...
COSTAR_BASE_URL=...              # base URL of the toolkit's OpenAPI service
COSTAR_PROPERTY_ID=...           # CoStar property id of the same building
ANTHROPIC_API_KEY=...            # for live model decisions (LLM_BACKEND=anthropic)
```

Check at any time, with any mix of modes:

```
python -m sandtable.adapters.check
```

It prints the mode of each layer, whether each key is present, reads each layer once and prints the honesty label the UI will show. A layer set to `live` with a missing key fails loudly; it never falls back to fixture while saying live.

## 1. Pick the building (5 minutes)

The demo needs one address that exists in all three layers. In the toolkit, find a Houston or Los Angeles office building that has a Matterport model. Note its CoStar property id, its Matterport model id and its latitude/longitude. Everything below is per building; the fixture building (7325 Medical Center Dr, Los Angeles) can stay as the public-context source until then.

## 2. Vexcel (5 minutes): the easiest, do it first

```
VEXCEL_MODE=live python -m sandtable.adapters.check
```

- Auth: the `X-API-KEY` header (security scheme "Header" in the spec). The same key works for the MCP server.
- Calls made (all `GET`, base `https://api.vexcelgroup.com/v2`): `/ortho/extract` (`layer=urban`, `wkt=POINT(lon lat)`, `srid=4326`, `bbox-dimensions=250,250`, `destination-dimensions=1250,1250,fit`, `image-format=png`), `/ortho/collections`, `/oriented/extract` four times (`product-type=oblique-north|east|south|west`), `/oriented/query`, `/structure/extract` (`primary-structure=true`, `include=structures,calculated,metadata`), `/dsm/elevation`, `/dtm/elevation`, `/oriented/transform-points` (`operation=world-2-pixel`).
- Images are saved under `data/vexcel/` and served at `/live/vexcel/`. The board's Outside panel then shows Vexcel imagery and the badge changes.
- If a response key differs from what `_first()` in `vexcel_live.py` expects, fix it there: it is the only place that parses Vexcel JSON. If your key has no `urban` layer coverage for the address, pass another layer (`VexcelLive(..., layer="wide-area")`).
- The MCP server, for the agent of your choice: URL `https://api.vexcelgroup.com/v2/mcp`, header `X-API-KEY: <key>`.

```json
{"mcpServers": {"vexcel": {"type": "http", "url": "https://api.vexcelgroup.com/v2/mcp", "headers": {"X-API-KEY": "${VEXCEL_API_KEY}"}}}}
```

## 3. Matterport (10 minutes)

```
MATTERPORT_MODE=live python -m sandtable.adapters.check
```

- Endpoint `https://api.matterport.com/api/models/graph`, HTTP basic auth with the token id and secret. Sandbox-mode accounts only see their own demo models: use the credentials from the toolkit.
- Queries are in `matterport_queries.py` (documented samples: `getSweeps`, `getDimensions`, `getTags`, `getGeoCoordinates`, mutation `addTag`). If one is rejected, paste it into the console at <https://api.matterport.com/model/graphiql>, fix the field, and save it back: the schema is versioned.
- `normalize_model()` converts the response to Sandtable's twin. Three approximations are made because the Model API does not hand these over directly, and each is stated in the twin's `source.note`: room polygons are bounding boxes of the room's sweep positions; the walking graph links sweeps closer than `MATTERPORT_LINK_M` (default 4.5 m); stairs come from room labels containing "stair". **Name two rooms "Stair A" and "Stair B" in the model** (or add tags with those labels), otherwise there are no exits to route to.
- Axis convention: the adapter assumes the Model API is z-up. If positions look rotated on the board, set `MATTERPORT_UP_AXIS=y`.
- Georeference: taken from `geocoordinates` (latitude, longitude, rotation). Verify with one point: the unit dot on the aerial should sit on the building. If the rotation is off, the fix is the `bearing_deg` line at the end of `normalize_model()`; Matterport's own `geoLocationOf` query (in `matterport_queries.py`) gives ground truth for any model point.
- Observations pinned by the agent are then written to the model with `addMattertag` (they are real edits to the model: use a copy if that matters).
- Optional, in the browser: the Showcase SDK (`MATTERPORT_SDK_KEY`) can show the real twin and move its camera with `Sweep.moveTo`. It is not wired in; the 3D view renders the normalised twin.

## 4. CoStar (10 minutes)

The toolkit's shape was unknown when this was written, so `costar_live.py` is deliberately thin.

1. Open the toolkit's OpenAPI spec (or SDK docs). Fill the right-hand side of `COSTAR_FIELD_MAP`, `TENANT_FIELD_MAP` and `SPACE_FIELD_MAP` with the toolkit's field names (dotted paths work, e.g. `address.street`).
2. Fix the three paths in `COSTAR_PATHS` (property by id, its tenants, its available spaces) and, if the toolkit has one, the path for research requests or corrections.
3. If the toolkit authenticates differently from an `X-API-KEY`/bearer header, change `_http()` (six lines).
4. If the toolkit is an SDK or an MCP server instead of HTTP, pass a transport: `CoStarLive(key, base, property_id, transport=fn)` where `fn(method, path, body) -> dict`. Wrap the SDK call in that function.

```
COSTAR_MODE=live python -m sandtable.adapters.check
```

Hydrants, fire stations, the footprint and the building sides are not CoStar data: they keep coming from the open-data bundle (`scripts/build_site.py`, change the address constants at the top and re-run it for a new building). Life hazards per tenant are inferred from the industry code by a rule table in that script; with live CoStar tenants, apply the same table to the toolkit's industry field (one line in `site()`).

## 5. Run fully live and re-record

```
SANDTABLE_DATA_MODE=live LLM_BACKEND=anthropic python -m sandtable.demo
```

- The badges on the board change from "Sponsor data: fixture" to the live layers, and the footer drops the "not CoStar / not Matterport / not Vexcel" lines automatically (they are generated from the adapters' modes).
- The recorded model responses in `fixtures/llm_cache/` belong to the fixture building. With live data the prompts differ, so use a live model (`LLM_BACKEND=anthropic`), or record a new run: `LLM_BACKEND=anthropic LLM_RECORD=1 python -m sandtable.demo --storyboard`, then `LLM_BACKEND=replay` replays it offline.
- To keep a copy of live data as a fixture for offline demos, save the three bundles from the running server (`curl localhost:1810/api/bundle`) into `fixtures/` and label them real sponsor data in `source_note`. Check the sponsor's terms before committing sponsor data to a public repository.

## Every external call in one table

| Call | Where | Documentation |
|---|---|---|
| Vexcel `GET /ortho/extract`, `/ortho/collections`, `/oriented/extract`, `/oriented/query`, `/oriented/transform-points`, `/structure/extract`, `/dsm/elevation`, `/dtm/elevation` | `vexcel_live.py` | <https://api.vexcelgroup.com/v2/api-docs> |
| Vexcel MCP | not called by Sandtable; config above | <https://api.vexcelgroup.com/v2/mcp> |
| Matterport GraphQL `model { locations, floors, rooms, mattertags, geocoordinates }`, `addMattertag` | `matterport_live.py`, `matterport_queries.py` | <https://developers.matterport.com/api/model/samples/>, <https://api.matterport.com/model/docs/reference> |
| CoStar property, tenants, available spaces, research request | `costar_live.py` | the event toolkit |
| Anthropic Messages API | `sandtable/agent/llm.py` | official SDK |
