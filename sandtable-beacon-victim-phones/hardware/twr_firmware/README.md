# Board-to-board ranging firmware (SS-TWR) for the Qorvo DWM3001CDK

Two DWM3001CDK boards measure the distance between them, about five times a second, and print every result on the
board's debug channel (SEGGER RTT). Beacon uses it for one thing: **the spacing of the two pucks** (the baseline), so a
responder only has to anchor one puck on the floor plan and tap the direction of the other
(`hardware/BOARD_TO_BOARD.md`, `docs/BEACON_FEED.md` A12).

| | |
|---|---|
| What it ranges | another DWM3001CDK running this firmware. **Not iPhones**: phones need Qorvo's Nearby Interaction firmware (QANI), and a board runs one firmware at a time |
| Method | single-sided two-way ranging (SS-TWR): the initiator polls, the responder answers with its receive and transmit timestamps, the initiator corrects for the clock offset between the boards |
| Radio | UWB channel 5, 1024-symbol preamble, PAC 32, 850 kbps, preamble code 9, STS off ("long-range profile", below) |
| Output | text lines on SEGGER RTT channel 0 (format below); the LED toggles on every range (initiator) or reply (responder) |
| Status | **measured on the owner's two boards** on 2026-11-15 (numbers below, `results/twr_measured_2026-11-15.json`). Antenna delays **not calibrated**. Reading it with this project's host (`python -m beacon.host --baseline-to`) is **written, not run on boards** (tested against lines in the firmware's format) |

## What is in this folder, and why the C file is not

`app.c` derives from Qorvo/Decawave's DW3000 API examples **ex_06a** (SS-TWR initiator) and **ex_06b** (SS-TWR
responder), through the public project [Uberi/DWM3001C-starter-firmware](https://github.com/Uberi/DWM3001C-starter-firmware),
built against Nordic's nRF5 SDK 17.1.0 and Qorvo's DW3000 driver library (`libdwt_uwb_driver`).

**Licence finding (checked 2026-11-15):** both example files carry "Copyright 2015 - 2021 (c) Decawave Ltd, Dublin,
Ireland. All rights reserved." and no licence text. The starter project has no licence file; its README says most of its
code "comes from the official Qorvo SDKs" and that you "should carefully read the copyright headers of the individual
source files and follow their licenses", releasing only its author's own build tooling under CC0. Nothing clearly allows
redistributing the examples or a derivative of them in a public repository, so **this folder does not contain `app.c`**.
It contains only our own work:

| File | What |
|---|---|
| `app.c.ed` | our changes, as an ed-style script (`diff -e`) against the two example files at starter commit `02c341b4f58dd8410239b5278b40aece675a3650`, concatenated. It carries only added text: 185 non-trivial lines, 9 of which repeat an upstream line (`#include`s and register writes) |
| `prepare.py` | clones the starter project at that commit (or uses your checkout), checks both example files' SHA-256, applies `app.c.ed`, checks the result's SHA-256 (`e2e205e2...`) and writes `Src/app.c`; copies the two files below. Python standard library only |
| `Makefile.twr` | the build (`ROLE=INITIATOR` or `RESPONDER`); the nRF5 SDK path comes from `NRF5_SDK` (argument or environment), the toolchain prefix from `CROSS` |
| `dwm3001c.ld` | our linker script for the nRF52833 (whole flash and RAM, no SoftDevice; the driver library's `.dw_drivers` table; everything else from the SDK's `nrf_common.ld`) |
| `build_logs/` | the compile logs of the build check described below |

The rebuilt `app.c` is byte for byte the file that was measured, except its header comment, which now lists the six-field
`R` line and the `FAIL` line the code really prints. Never commit the SDK, the driver library, a `.hex` or `.elf`
(`.gitignore` blocks `*.hex`, `*.bin`, `build/`).

## Build

You need: Python 3, `git`, `make`, an `arm-none-eabi-gcc` with newlib-nano (Arm GNU Toolchain, or Debian's
`gcc-arm-none-eabi` + `libnewlib-arm-none-eabi`), and Nordic's **nRF5 SDK 17.1.0** (`nrf5_sdk_17.1.0_ddde560.zip`,
free download from nordicsemi.com; unzip it anywhere).

```bash
python3 hardware/twr_firmware/prepare.py --starter ../DWM3001C-starter-firmware      # clones it if missing
cd ../DWM3001C-starter-firmware
make -f Makefile.twr ROLE=INITIATOR NRF5_SDK=/path/to/nRF5_SDK_17.1.0_ddde560 -j4
make -f Makefile.twr ROLE=RESPONDER NRF5_SDK=/path/to/nRF5_SDK_17.1.0_ddde560 -j4
ls build/INITIATOR/app.hex build/RESPONDER/app.hex
```

**Build check on the bench machine (2026-11-15, compile and link only, nothing flashed):** `prepare.py` on a fresh
clone, then both roles with Arm GNU Toolchain 13.3.Rel1 (`arm-none-eabi-gcc` 13.3.1) and nRF5 SDK 17.1.0: both link
with no compiler warnings (only newlib's usual "not implemented" notes for the `-lnosys` stubs). Logs with the `size`
output: `build_logs/build_INITIATOR.log`, `build_logs/build_RESPONDER.log` (paths shortened to `<scratch>`). The first
attempt failed to link (`undefined reference to __dw_drivers_start`): Qorvo's driver library keeps its chip-driver table
in `.dw_drivers` sections, which `dwm3001c.ld` now collects (with `KEEP`, or `--gc-sections` would drop them and
`dwt_probe` would find no driver). The measured boards ran a build of the same `app.c` and the same Makefile body made by
the bench agent with its own `arm-none-eabi-gcc` and its own linker script of the same name; those binaries are not
these. So: the source is the measured one, this build path is compile-verified, and a board flashed from it has not
been run.

## Flash (OpenOCD; this exact form worked on the UNO Q)

One board per J-Link. Each DWM3001CDK has its own on-board J-Link behind the **J9** micro-USB port (bottom, labelled
"J-Link"); the serial number tells two boards apart.

1. **Stop anything holding the probe first**: the UNO Q's ranging dashboard, another `openocd`, `JLinkExe`,
   `JLinkRTTLogger`, or this project's host reading RTT through one of them. Check: `ps aux | grep -E "openocd|JLink"`.
2. Find the probe's serial: `ls /dev/serial/by-id/` shows `usb-SEGGER_J-Link_<JLINK_SERIAL>-if00` (Linux), or
   `JLinkExe` then `ShowEmuList`. Write each serial on the board's tape label with its role.
3. Flash (on the UNO Q OpenOCD needs root to open the J-Link, so prefix `sudo` there):

```bash
openocd -c "adapter driver jlink" -c "adapter serial <JLINK_SERIAL>" -c "transport select swd" -f target/nrf52.cfg \
  -c init -c "program build/INITIATOR/app.hex verify reset exit"
openocd -c "adapter driver jlink" -c "adapter serial <OTHER_JLINK_SERIAL>" -c "transport select swd" -f target/nrf52.cfg \
  -c init -c "program build/RESPONDER/app.hex verify reset exit"
```

Works when OpenOCD prints `** Verified OK **` and `** Resetting Target **` and the board's LED starts blinking (fast and
steady on both boards once both run). To go back to iPhones, flash the QANI image the same way (`hardware/FLASH.md`).

## Read the lines (RTT)

The firmware prints only on SEGGER RTT channel 0, not on any serial port. Either reader serves channel 0 as a TCP
stream; read it with `nc 127.0.0.1 19021`, or let the host turn it into a baseline. **The OpenOCD form below is the
one the UNO Q's ranging dashboard ran (as root) to read this firmware's lines on 2026-11-14 and 15**, one OpenOCD per
board, each on its own RTT port; the J-Link variant is from SEGGER's documentation and was not run with this firmware.

```bash
# OpenOCD (RAM of the nRF52833 is 0x20000000, 128 KB; the control block is found by its "SEGGER RTT" signature)
openocd -c "adapter driver jlink" -c "adapter serial <INITIATOR_JLINK_SERIAL>" -c "transport select swd" -c "adapter speed 4000" \
  -f target/nrf52.cfg -c init -c "rtt setup 0x20000000 0x20000 {SEGGER RTT}" -c "rtt start" -c "rtt server start 19021 0"
# the responder, if you want its P/H lines too: same with its serial and "rtt server start 19022 0"
# or SEGGER's J-Link software: keep this open, RTT is served on port 19021 while it is connected
JLinkExe -device NRF52833_XXAA -if SWD -speed 4000 -autoconnect 1 -SelectEmuBySN <INITIATOR_JLINK_SERIAL>
```

Then, on the same machine:

```bash
python3 -m beacon.host --server http://127.0.0.1:1885 --puck P1 --baseline-to P2 --rtt 127.0.0.1:19021 --learn
```

`--learn` shows every line and what the host made of it. The host posts the spacing of P1 (the initiator's puck) and P2
(the responder's) to `POST /api/pucks/baseline` once 20 good ranges are in its 50-range window, again when it moves by
more than 2 cm or half its sigma, and once a minute. One reader per probe at a time.

## Line format

```
BOOT,<role>,<dw_dev_id>,<nrf_device_id>                     at start
ID,<role>,<dw_dev_id>,<nrf_device_id>,<uptime_s>            every 5 s
R,<seq>,<dist_m>,<ppm>,<rx_level_dBm>,<preamble_count>      initiator: one successful range
T,<seq>                                                     initiator: no answer before the 6 ms timeout
E,<seq>,<status_hex>                                        initiator: receive error (system status register)
P,<seq>,<responses>                                         responder: answered poll <seq>
H,<responses>,<rx_timeouts>,<rx_errors>,<late_tx>           responder: heartbeat, about once a second
FAIL,<role>,init|config                                     radio bring-up failed; the board halts
```

`role` is `INIT` or `RESP`. `seq` is 8-bit and wraps. `dist_m` is metres (three decimals) with the uncalibrated
antenna delays; `ppm` the clock offset between the boards; `rx_level_dBm` the received level from the chip's diagnostics
(generic formula constant, -121.7 dB); `preamble_count` the accumulated preamble symbols (of 1024). `late_tx` counts
replies the responder could not send in time: if it climbs, the turnaround is too short. Parser:
`beacon/host/twr.py`, tested with lines in exactly this format.

## The long-range profile, and why

The stock DW3000 example uses a 128-symbol preamble at 6.8 Mbps. This firmware uses a **1024-symbol preamble at
850 kbps**. The reason is **processing gain, not transmit power**: UWB transmit power is capped by regulation (in the US
and EU a mean power spectral density of -41.3 dBm/MHz), so range cannot be bought with power. A preamble 8 times longer
lets the receiver integrate 8 times more energy before it locks (about 10 log10 8 = 9 dB, derived, not measured), and
a data rate 8 times lower gives the payload about the same margin. The costs: a poll takes about 1.3 ms on air, so the
responder waits 2.7 ms before answering and the initiator listens for 6 ms; each exchange uses more energy; and SS-TWR's
clock-drift error grows with that reply time (the firmware corrects with the measured clock offset, as ex_06a does).

## Measured (2026-11-15)

From `results/twr_measured_2026-11-15.json`: hand-transcribed by the bench agent from the UNO Q ranging dashboard's
`/api/state` (its summary over the last 50 ranges), **not produced by this repository's code**. Two boards on the
owner's UNO Q, this firmware, line of sight, on a bench.

| Spacing (bench notes) | Rate | Success | Mean | SD | Min / max | RX level | Preamble |
|---|---|---|---|---|---|---|---|
| about 0.24 m | 4.9 Hz | 100 % of the last 50 | 0.237 m | 0.056 m | | -81.9 dBm | 977 of 1024 |
| about 1.57 m (separated by the owner) | 4.9 Hz | 100 % of the last 50; 7522 ok and 11 timeouts in all (99.85 %) | 1.572 m | 0.040 m | 1.479 / 1.705 m | -78.9 dBm | 977 of 1024 |

No late replies in either run. **No tape-measure reference was recorded**, so these runs show repeatability (a spread of
about 4 to 6 cm) and a working link, **not accuracy**: with uncalibrated antenna delays the absolute bias is unknown.
Failures seen while tuning: a 1100 us turnaround is shorter than the poll's air time, so every reply was late and the
initiator timed out every time (fixed at 2700 us); without `dwt_configciadiag(DW_CIA_DIAG_LOG_ALL)` the diagnostic
registers read 0.

Not measured: range through a wall, ranges beyond 2 m, boards on the floor, two QANI boards ranging phones next to these
boards (interference: `hardware/BOARD_TO_BOARD.md`).

## Antenna-delay calibration (not done yet; 20 minutes)

`TX_ANT_DLY` and `RX_ANT_DLY` are both the uncalibrated default **16385** on both boards. The chip adds the TX antenna
delay to its transmit timestamps and subtracts the RX antenna delay from its receive timestamps, so in SS-TWR the
measured time of flight drops by (TxA + RxA + TxB + RxB) / 2. With the same value D for all four, raising D by one unit
(15.65 ps) shortens every range by 2 x 4.69 mm = **9.38 mm**.

1. Both boards upright, at least 15 cm off the floor, non-component sides facing, line of sight. Tape-measure the
   antenna-to-antenna distance: **1.000 m**. Do not touch them afterwards.
2. Collect about 120 ranges (25 s): `python3 -m beacon.host --puck P1 --baseline-to P2 --rtt 127.0.0.1:19021 --no-post
   --baseline-window 120 --baseline-min 100` and note the printed `d_m` (the median of the window; ranges older than
   30 s leave it, so a larger window does not help).
3. bias = d_m - 1.000. Repeat at **3.000 m**; the two biases should agree within a few centimetres (if not, a
   reflection is in play: move away from walls and metal and repeat).
4. Either correct in software: `--twr-offset-m <bias>` on the host (subtracted from every range before the median), or
   in firmware: D = 16385 + round(bias / 0.00938); after `prepare.py`, change both `#define TX_ANT_DLY 16385` and
   `#define RX_ANT_DLY 16385` lines in `Src/app.c` to D, rebuild both roles and flash both boards (the edited file no
   longer matches `prepare.py`'s hash; that is expected).
5. Write the date, the boards' J-Link serials, the bias and D in `results/` (a new file, with how you measured it).

Two boards calibrate only the sum of their four delays; a value per board needs a third board (Qorvo/Decawave
application note APS014 describes the three-board method). For a puck spacing, the sum is all that matters.
