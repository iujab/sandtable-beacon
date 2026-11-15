# Which floor, which room: what each source can tell, and what it cannot

Written 2026-11-15. Nothing on this page is a new measurement. It says where Beacon's floor and room come from today, in
order of reliability, and what is only an idea. Detection is opt-in throughout: a phone whose owner did not open the
Beacon app is never ranged, located or identified, by any of these sources.

## 1. The responder says it when placing the beacon (the most reliable source)

Whoever throws or places the pucks knows which door they went through. The board takes the room and floor with the
pucks: `POST /api/pucks {"pucks": [...], "room": "r310", "floor": "f3"}`, or with the on-landing calibration
`POST /api/pucks/anchor {..., "room": "r310", "floor": "f3"}` (`docs/BEACON_FEED.md` A12, `hardware/BOARD_TO_BOARD.md`).
Every puck then carries the twin's room under its position, and the Beacons object carries `room`, `room_label` and
`floor`.

The plan itself, rooms, walls and doors, comes from the building's twin, and the tenants by suite from the building
record. On the command board's private live bundle those are a scanned twin and the CoStar record of the building
(`docs/BEACON_FEED.md` A10). **In this repository both are labelled fixtures**: the twin is a synthetic interior fitted
to a real footprint, not a Matterport scan, and the record is public open data, not CoStar data.

## 2. The phone's position on the plan, once the pucks are on it

When the pucks' positions are on the plan (placed by hand, or anchored from their measured spacing), a phone that opted
in and is ranged by the pucks gets a position or candidate spots in the building frame. Every cell of the locator's grid
belongs to a room of the twin, so the phone's room is read off the plan: `room`, `room_label` and `room_prob` on the
Phone (`beacon/locate.py`). This is only as good as the puck positions and the twin's walls: with two candidate spots
in two rooms the board says "cannot choose" rather than pick one; a ring gives no room at all.

## 3. What the phone app already reports

The iOS app (uncompiled source in `apps/ios`) sends, with every status:

| Field | What it is | How the server uses it today |
|---|---|---|
| `home_suite` | the suite the occupant picked ("Which suite are you in?"), from the building record's list | a stated assumption that may dismiss a mirror spot, always shown next to the position (`docs/BEACON_FEED.md` A8) |
| `floor_reported` | the floor of that suite, from the same list | the Phone's `floor`, with `floor_source: "self-report"`; otherwise the pucks' floor is assumed (`"puck"`) |
| `baro_hpa` | barometric pressure from the phone's altimeter | **carried, not used**: turning it into a floor needs a reference pressure taken at a known floor at the same time (a phone or a barometer at the command post or on a puck). One storey is roughly 0.4 hPa, inside the weather's drift over an hour, and a fire and its ventilation move the pressure inside the building. The contract's `floor_source: "barometer"` is reserved; nothing produces it |

What the occupant states is a statement, not a measurement: the board shows it as one.

## 4. GPS

GPS can identify the building and, near windows, roughly which side of it a phone is on. Indoors it is unreliable: weak
or reflected signals, often no fix at all deep inside, and no useful floor. The app does not send GPS today. It could
help only to say "this phone is at this building", never which room.

## 5. UWB "radar": wall sensing from the channel impulse response (a research idea, not built)

The DW3000 family can expose the channel impulse response of a received frame: in principle, reflections from walls and
large objects show up in it, and a pair of boards could sense the room around them. This is a **research idea**. It
needs dedicated boards or dedicated time slots, because the radio cannot do it on the same boards while they range
phones (Qorvo's Nearby Interaction firmware owns the radio during a phone session, and our TWR firmware does not record
impulse responses). **Nothing of it has been built or tried.** No claim on the board, in the pitch or in the feed rests
on it.

## Order of reliability, in one line

The responder's placement (room and floor) > the phone's position on the plan (needs the pucks placed) > the occupant's
statement (suite, floor) > barometric floor (not implemented) > GPS (building only, not sent).
