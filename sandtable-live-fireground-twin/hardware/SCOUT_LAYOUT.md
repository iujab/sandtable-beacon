# Scout pucks layout (rungs 1 and 2)

> **Future work, not for this weekend.** These are placement rules for real scout pucks on ESP32 + DW3000 kit boards. The owner's boards are DWM3001CDK, and Scout is a simulated mode at the event (`BRINGUP.md`).

Scout mode is at full scale (1:1): a body has to be between the pucks for real. The twin room it is registered into is Suite 310 of the fixture building (9.5 m x 8.65 m); your demo floor is not that room, see `BRINGUP.md` step S6.

## As thrown (the layout of `hardware/scenarios/scout_pucks_mesh5.json`)

Tape a **door mark** on the floor (the side you "throw" from). Then place the pucks, measured from that mark. These are the positions the kit simulator and the recorded fake-board session use, so a live run can be compared with the recording.

| Puck | into the room from the door mark (cm) | to the left of the door, looking in (cm; negative = to the right) |
|---|---|---|
| P1 (N1) | 90 | 80 |
| P2 (N2) | 250 | -70 |
| P3 (N3) | 160 | 260 |
| P4 (N4) | 380 | 160 |
| P5 (N5) | 290 | 350 |

Pairwise distances for the self-location check (step S5):

| Pair | cm | | Pair | cm |
|---|---|---|---|---|
| N1-N2 | 219 | | N2-N4 | 264 |
| N1-N3 | 193 | | N2-N5 | 422 |
| N1-N4 | 301 | | N3-N4 | 242 |
| N1-N5 | 336 | | N3-N5 | 158 |
| N2-N3 | 342 | | N4-N5 | 210 |

Footprint about 3.8 m x 4.2 m. The rung-2 demo spot is where N1-N4 and N2-N5 cross: about 270 cm into the room and 130 cm to the left of the door mark. Stand there and both links go down.

## Compact (a booth or a table edge, about 2.4 m x 2.4 m)

Any five points that are **not on one line**, 1.5 to 3 m apart, with the walking area inside them, work. A pentagon of 1.2 m radius: P1 at the door side, then P2..P5 counter-clockwise every 72 degrees. Measure three or four pair distances with the tape and write them down for step S5.

## Rules that matter more than the exact positions

- All pucks the same height, 0.3-0.5 m above the floor, board upright, antenna end up. Flat on the floor the first path fades (ground reflection at a grazing angle) and range drops.
- At least 10-20 cm from metal (laptop lids, metal table legs, bottles, the power bank's case if it is metal) and 0.5 m from walls.
- The person must cross the **straight lines** between pucks. A person outside the pucks' outline crosses no link and is not seen by rung 2; rung 1 sees echoes there, rung 2 does not.
- For rung 1 (echo ellipses) pairs at least 1 m apart, the person 0.5 to 3 m from the pucks, nobody else moving within about 3 m.
