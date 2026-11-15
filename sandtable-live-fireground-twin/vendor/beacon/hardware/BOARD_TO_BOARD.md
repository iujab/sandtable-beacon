# Board to board: measure the puck spacing, then anchor the pair on the plan

> **What is real here.** Two DWM3001CDK boards running our SS-TWR firmware ranged each other on the owner's UNO Q on
> 2026-11-15 (about 4.9 ranges a second, 4 to 6 cm spread; `results/twr_measured_2026-11-15.json`, hand-transcribed).
> Everything else on this page — the host's baseline mode against those boards, the anchor flow with real pucks,
> option (b) — is **written, not run on boards**. The UNO Q was powered off when this was written.

## The idea, in four lines

1. The beacon is two pucks. On landing they range each other: their spacing is **measured, not guessed**.
2. The responder anchors one puck on the floor plan (where it is, roughly: the door it went through) and taps the
   direction of the other.
3. The other puck snaps to the measured spacing along that direction (`POST /api/pucks/anchor`).
4. A phone that opted in is ranged by both pucks; two distances and two known puck positions give two mirror spots, and
   the twin's walls or the occupant's statement choose (`hardware/LAYOUT.md`). A third puck removes the mirror.

## The hardware truth (do not claim more)

| Firmware | Ranges iPhones (opt-in app) | Ranges another board | Where |
|---|---|---|---|
| Qorvo's stock Nearby Interaction (QANI) | **yes**, through Apple's accessory protocol | **no** | `hardware/FLASH.md` |
| Our SS-TWR (`hardware/twr_firmware/`) | **no** | **yes** (measured) | `hardware/twr_firmware/README.md` |

A board runs one firmware at a time. Two boards are wired to the UNO Q; the owner has five. No phone that has not opted
in can be ranged or found, by either firmware.

## Three ways to get the spacing

| | How | Boards per puck | Status |
|---|---|---|---|
| **(a) Tonight** | Measure the spacing once with the TWR firmware at setup (or with a tape measure), post it, then flash QANI on both boards **without moving them** | 1 | the TWR half was measured; the whole sequence was not run |
| **(b) A 4-board beacon** | Each puck carries two boards: a QANI board (phones) and a TWR board (spacing). Four of the owner's five boards; the fifth is a spare | 2 | not built. The TWR firmware is on UWB **channel 5**; QANI's channel is set per phone session and must be read from its RTT log; **interference between co-located boards is UNTESTED** |
| **(c) Production** | One firmware does both: a FiRa ranging session with the phone and a puck-to-puck session, time-multiplexed (Qorvo's QM33 SDK supports FiRa multi-session) | 1 | **not built**; the sensible product |

For (b): if the TWR pair and the QANI boards disturb each other (QANI ranges drop out or TWR timeouts climb while a
phone ranges), the honest options are to range puck to puck only for the first seconds after landing and then cut the
TWR boards' power, or to move the TWR pair to channel 9 (a one-line change in `app.c`'s radio configuration, then
rebuild; not tried). Measure before you claim either.

---

## Procedure (a), for a tired person: about 30 minutes

You need: two DWM3001CDK boards (call them A and B; tape labels), the UNO Q or a laptop with OpenOCD, two micro-USB
**data** cables into each board's **J9** (bottom port, "J-Link"), the Beacon server running (`hardware/BRINGUP.md`
step 4), and the two hex files (`hardware/twr_firmware/README.md`, Build).

### 1. Flash the TWR firmware (5 min)

Stop the UNO Q's ranging dashboard and anything else using the probes (`ps aux | grep -E "openocd|JLink"`). Then, with
each board's J-Link serial (`ls /dev/serial/by-id/`):

```bash
sudo openocd -c "adapter driver jlink" -c "adapter serial <SERIAL_A>" -c "transport select swd" -f target/nrf52.cfg \
  -c init -c "program build/INITIATOR/app.hex verify reset exit"
sudo openocd -c "adapter driver jlink" -c "adapter serial <SERIAL_B>" -c "transport select swd" -f target/nrf52.cfg \
  -c init -c "program build/RESPONDER/app.hex verify reset exit"
```

**Works when** both print `** Verified OK **` and both LEDs blink. (`sudo` on the UNO Q only; a laptop may not need it.)

### 2. Read the initiator and post the spacing (5 min)

Terminal 1 (keep open; serves board A's RTT on port 19021):

```bash
sudo openocd -c "adapter driver jlink" -c "adapter serial <SERIAL_A>" -c "transport select swd" -c "adapter speed 4000" \
  -f target/nrf52.cfg -c init -c "rtt setup 0x20000000 0x20000 {SEGGER RTT}" -c "rtt start" -c "rtt server start 19021 0"
```

(This is the form that read this firmware's lines on the owner's UNO Q.)

Terminal 2 (from the project folder; `SERVER` is the Beacon server, e.g. `http://127.0.0.1:1885`):

```bash
python3 -m beacon.host --server SERVER --puck P1 --baseline-to P2 --rtt 127.0.0.1:19021 --learn
```

**Works when** lines like `R,41,1.572,-3.21,-78.9,977 -> TWR board-to-board range 1.572 m` scroll by, and within about
10 s the host prints `baseline P1-P2: ... -> posted`. The board's event list says `Baseline P1-P2: 1.57 m ± 0.04 m
(measured by UWB board-to-board ranging, 48 samples)`, and its puck panel shows the baseline.

**Fails:**

| You see | Do |
|---|---|
| no lines at all | the RTT reader is not connected (terminal 1 errors?), or it reads board B: B prints only `P`, `H`, `ID` lines |
| only `T,<seq>` lines | the responder is off, out of range or not flashed; power-cycle B |
| `H` lines on B with `late_tx` climbing | the responder answers too late: the hex is not this firmware's (turnaround 2700 us) |
| `baseline ... not posted (server ... unreachable)` | wrong `--server`, or the server is not running; it retries |
| `400` from the server | the ids: `--puck` and `--baseline-to` must be two different ids among P1..P5 |

No TWR boards at all? **Tape-measure** the antenna-to-antenna distance instead and post it by hand:

```bash
curl -X POST SERVER/api/pucks/baseline -H "Content-Type: application/json" \
  -d '{"pair": ["P1", "P2"], "d_m": 1.33, "sigma_m": 0.02, "source": "manual"}'
```

### 3. Anchor the pair on the plan (2 min)

Puck P1 at a known spot on the plan (say just inside the door, `x 9.15, y 6.7` in Suite 310), P2 somewhere along the
door wall toward `y 0`:

```bash
curl -X POST SERVER/api/pucks/anchor -H "Content-Type: application/json" \
  -d '{"anchor": "P1", "x": 9.15, "y": 6.7, "toward": {"x": 9.15, "y": 0.0}, "other": "P2", "room": "r310"}'
```

**Works when** the answer has `"ok": true` and both pucks with `"placed_by": "baseline"`; on the board P2 sits exactly
the measured spacing from P1 (times the exercise scale, if one is set: `hardware/BRINGUP.md` 7.1), and both puck
markers say "baseline". `toward` only gives the direction: tap anywhere along the line to P2. A `400` says why: no
baseline for that pair yet, or `toward` is the anchor's own spot.

**If you place the pucks by hand instead** and their spacing on the plan disagrees with the measured one by more than
3 sigma + 0.2 m, the board shows a warning ("one of them is misplaced on the plan"): re-anchor.

### 4. Back to phones: flash QANI on both boards without moving them (5 min)

The spacing is a property of where the boards stand. **Do not move them.** Flash Qorvo's QANI image on both
(`hardware/FLASH.md`, or the same OpenOCD form with the QANI hex), then follow `hardware/BRINGUP.md` from step 5. The
anchored positions stay on the board; map each board's Bluetooth name to its puck id from the tray as usual (the anchor
keeps any accessory names already mapped).

### 5. Before you trust it

- The ranges are **uncalibrated** (antenna delays left at 16385): a constant bias of unknown size. A one-time check
  at 1.000 m with a tape measure gives it (`hardware/twr_firmware/README.md`, calibration); pass it as `--twr-offset-m`.
- Line of sight between the pucks, both upright, 15 cm or more off the floor.
- The board shows what it got: the baseline's `source` (`twr` or `manual`) and each puck's `placed_by`.

## What is measured, and what is not

| Claim | Status |
|---|---|
| Two DWM3001CDK boards on our TWR firmware range each other at 4.9 Hz, 100 % of the last 50, 4 to 6 cm spread, at about 0.24 m and 1.57 m | **measured** (bench, line of sight, `results/twr_measured_2026-11-15.json`) |
| Absolute accuracy of those ranges | **unknown** (no tape reference recorded; antenna delays uncalibrated) |
| The host turns the firmware's lines into a baseline and posts it | written and tested with lines in the real format; **not run against the boards** |
| `/api/pucks/baseline`, `/api/pucks/anchor`, `placed_by`, the mismatch warning | written and tested in the server (`tests/test_baseline.py`) |
| Range between pucks through a wall, beyond 2 m, on the floor | **not measured** |
| QANI and TWR boards side by side (option b) | **untested**; QANI's channel unknown |
| One firmware for phones and pucks (option c) | **not built** |
| UWB "radar" (wall sensing from the channel impulse response) | **not built**, a research idea (`docs/ROOM_LOCATION.md`) |
