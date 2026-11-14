# Bill of materials

> **The puck section is future work.** It assumes ESP32 + DW3000 boards. The owner's boards are DWM3001CDK, and Scout and Crews are simulated this weekend (`BRINGUP.md`).

## One scout puck (five for a scan; three is the minimum for a 2-D position, two give presence and range only)

| Qty | Part | Notes |
|---|---|---|
| 1 | ESP32 + Qorvo DW3000 UWB board (Makerfabs "ESP32 UWB DW3000" assumed) | channel 5 (6.5 GHz), 64 MHz PRF. A DW1000 board works for rungs 2 and 3 only (no CIR port) |
| 1 | USB power bank (any 5 V, 1 A) and a short USB **data** cable | one puck instead goes to the laptop as the gateway; for rung 1 over USB all five go to a powered USB hub |
| 1 | stand that holds the board upright, antenna end up, 0.3-0.5 m above the floor | a paper cup on a cardboard box is enough; same height for all five. Lying flat on the floor costs first-path power (ground reflection at grazing angle) |
| 1 | label (tape + marker) with the puck id N1..N5 | the id is stored on the board; the label is for you |
| optional | enclosure | see `hardware/puck/` if present; keep metal at least 10 cm from the antenna |

Approximate cost per puck: the board (about USD 30-45) and a power bank. Nothing else.

## Tagged crews (rung 3, unchanged)

5 kit boards (four anchors and one tag, or three and two), four chairs or tripods for the anchors at about 1.2 m, painter's tape, a tape measure, the laptop; optionally the Android phone and a USB cable.
