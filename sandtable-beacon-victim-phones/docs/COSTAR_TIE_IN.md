# The CoStar tie-in: who should be on the floor, against who is still inside

For the CoStar judge. One page. Every number below was printed by the code in this repository on the fixture record (commands at the end); nothing here is CoStar data.

## What it does with the data

Pucks thrown into the fire room hear the phones of occupants who opened the Beacon app. On their own the phones say **who is still inside, where, and who got out**. They cannot say **who is missing**: someone with no app, or no phone, is silent. The building record says who **should** be there: which tenant occupies which suite, what kind of business it is, how large it is, which suites are empty. Joined through the twin, the two give a per-suite accountability table. A command agent turns the table into orders: who goes where first, what each crew hears on the radio, where to throw the next puck, and which record entries to check.

## The three layers and the join

| Layer | Fields used | Used for |
|---|---|---|
| Building record (CoStar in live mode; public open data in fixture mode) | tenant by **suite** and **floor**; **kind of business** (`industry`) and **NAICS** code; **leased area** (`sf`, when present); **available space** (`available_spaces`); **head count** (`employees`, when present); `life_hazard` / `hazmat` (in the fixture these come from a rule on the NAICS code: inferred, not observed); **property-manager contact**; address, construction, year built, floors for the report header | the expected occupants of each suite, the life-hazard weighting of the search order, the vacant-on-record test, the roll call, the report |
| Twin (Matterport in live mode; synthetic fixture here) | rooms with **suite numbers**, room **areas**, walls, doors, stairs, the walking graph | the join (suite number = room), the area when the record has none, pruning impossible phone positions, routes and distances, which door to throw the next puck through |
| Beacon (the phones) | per opt-in phone: geometry (fix / candidates / ring / zone), room, the occupant's report (status, people, can move, **the suite they registered in the app**, "I am out"); cold device counts per room | people reported, located, safe outside, devices heard per suite |

**The join** (`beacon/roster.py`, `suites()` and `accountability()`): every suite room of the twin's floor is matched to the record's tenants by suite number and floor (the twin's floor index + 1 = the record's storey). A suite with no tenant, or one listed as available space, is **vacant on record**. Phones are attributed to the room of their geometry; a ring or a zone with no room goes to the room of the puck that hears it, **flagged as an assumption** ("not yet located (range only)"). Phones whose user pressed "I am out" count as accounted for in the suite they registered, never as victims. On the fixture floor: 12 suites, 4 vacant on record, 8 occupied by 10 business registrations (two suites have two each).

## What is an estimate, and on what basis

Every expected head count is an **estimate** and carries its basis text into the prompt, the board and the report:

1. **The record's head count** when every tenant of the suite has one (`employees`; a live tenant field). Band: 25 % to 100 % of it; central figure 80 % during open hours.
2. Otherwise **area / building-code load factor**: the record's leased area, else the twin's room area (the basis says which). Load factors from the International Building Code, Table 1004.5: **100 sf per person for outpatient areas** (NAICS 621 to 623), **150 sf per person for business areas** (sources cited in `roster.py`). That quotient is an egress-design **maximum**, not a census.
3. **Time of day**: 50 % of that maximum present during open hours (08:00 to 18:00), band 25 % to 100 %; 5 % outside them. These shares are stated assumptions.

Fixture result: a dental suite of 885 sf (twin area) expects about 4 people (2 to 9); the accounting office of 1,040 sf expects about 4 (1 to 7); the floor expects about 32, at most 70. People **unaccounted for** = expected minus everyone accounted for (reported inside by phone, or safe outside): always a range. Devices heard without the app are **devices, not people**, and never enter that arithmetic.

## Where the record and the phones disagree (corrections to the record)

The agent's `suite_accountability` tool lists every disagreement, and the agent files each one with `file_record_check` (fixture mode: appended to `data/record_checks.json`, status "filed locally (fixture mode)"; nothing is sent anywhere):

- a phone or a device heard in a suite the record lists as vacant (`roster.accountability`, `record_checks`);
- an app user who registered a vacant-on-record suite as their own (`tools.ToolBox.disagreements`);
- a range ring whose possible arc lies mostly (50 % or more) in a vacant-on-record suite.

## What the agent does with it (agentic depth)

`beacon/agent.py`: one turn is a plan-then-act loop of up to 8 model replies over 7 tools (`get_phones`, `suite_accountability`, `next_puck_plan`, `route_to`, and the actions `radio`, `assign`, `file_record_check`). It reads the phones and the record, checks routes from the stair nearest each target, assigns the rescue team and the engine, radios orders that say how sure each location is, picks the next suite for a puck from the record's expected people and life hazards (with the door and walking distance from the twin), and files record checks. Two runs were recorded with the real model (`claude-sonnet-5-5` through the Claude CLI) and replay exactly; when no recording matches and no model is configured, a deterministic policy built from the same table acts instead and labels every message `reflex-fallback`.

## When the live CoStar toolkit replaces the fixture

The toolkit and keys were not on the build machine. The right-hand column is an **assumption** from CoStar's public descriptions of its products; it must be checked against the toolkit's schema at the event before anything is claimed.

| Fixture field (`fixtures/building/site.json`) | Fixture source | Expected CoStar field (ASSUMPTION, unverified) |
|---|---|---|
| `tenants[].suite`, `tenants[].floor` | City of Los Angeles, Listing of Active Businesses | tenant record: suite / floor |
| `tenants[].name` (withheld) | same | tenant name (shown as the kind of business on screen, never a person) |
| `tenants[].industry`, `tenants[].naics` | same | tenant industry / NAICS or SIC code |
| `tenants[].sf` (null here) | none in open data | tenant leased square feet |
| `tenants[].employees` (absent here) | none in open data | tenant employee count |
| `tenants[].lease_expiration` (null here) | none | lease expiration |
| `available_spaces[]` (empty here) | none in open data | available / for-lease spaces (suite, floor, sf) |
| `property.property_manager` (null here) | none | property manager contact (for the tenant roll call) |
| `property.rba_sf`, `stories`, `year_built`, `construction` | LA County Assessor parcel record | building RBA, stories, year built, construction / class |

What changes when it lands: the basis texts switch from "twin room area" to "record area" or "record head count" (branches 1 and 2 above, already tested in `tests/test_roster.py`); vacancy comes from listed availability instead of a missing registration; the `roll_call` field of `suite_accountability` gets a contact; `file_record_check` stays local unless the toolkit offers a way to submit a correction.

## Honest limits

- The fixture record is **public open data with tenant names withheld, labelled "Not CoStar data"** everywhere it appears. CoStar-only fields are empty.
- The twin is a **synthetic interior** fitted to the real footprint, not a Matterport scan; room areas are therefore approximate.
- Expected people are estimates from design maxima and assumed shares, not a census. A business registration is not proof that a suite is occupied, and a missing one is not proof that it is empty.
- The phones and pucks in every number above are **simulated** until the owner's hardware is in the loop.
- Re-run: `.venv/bin/python -m pytest tests/test_roster.py tests/test_agent.py tests/test_report.py -q`; `.venv/bin/python scripts/record_agent.py --check`.
