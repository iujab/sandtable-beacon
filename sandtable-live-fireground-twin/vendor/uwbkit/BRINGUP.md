# uwb-kit bring-up: from five boards in a bag to a live map

For one tired person at 2 am. Follow the numbers; every step says how long it takes and how you know it worked.
Total: **about 45 minutes** the first time (10 of them waiting for flashing), 5 minutes on later days.

**Honest status:** the software path below was tested end to end on this bench with a fake-board emulator over
real serial/UDP transports. The firmware is **compile-verified only** (logs in `firmware/build_logs/`): it has
never run on a real board. Expect the first power-up to need one of the fixes in the troubleshooting table.
If the boards refuse to cooperate, section 10 keeps every demo alive without them.

Commands are written for **Windows PowerShell** (the venue laptop). On Linux / the UNO Q use `.venv/bin/python`
instead of `.venv\Scripts\python` and `/dev/ttyUSB0` instead of `COM5`.

You need: the 5 boards, 5 USB **data** cables (charge-only cables are the #1 time sink), USB power banks or
chargers for the boards that are not on the laptop, a tape measure, masking tape and a marker, this folder.

---

## 0. Install the kit on the laptop (5 min, once)

```powershell
winget install Python.Python.3.13          # skip if `py --version` already prints 3.10 or newer
cd <folder that contains uwb-kit>
py -m venv .venv
.venv\Scripts\python -m pip install -e .\uwb-kit esptool
.venv\Scripts\python -m uwbkit --version   # -> uwbkit 1.0.0
```

No internet at the venue? On any machine with internet run `python -m pip download -d wheels aiohttp numpy pyserial esptool`
for the laptop beforehand, then `pip install --no-index --find-links wheels -e .\uwb-kit esptool`.

**Works when:** the last command prints the version. Dry run with no boards at all:
`.venv\Scripts\python -m uwbkit hub --source sim` then open <http://localhost:1800/> (SIM badge, a tag walking).
Stop it with Ctrl-C before continuing.

## 1. Find out which boards you have (3 min)

Plug in ONE board.

```powershell
.venv\Scripts\python -m uwbkit identify
```

It lists the COM port, the USB chip, and what the board is running. No COM port at all: the cable is charge-only, or
Windows needs the driver for the USB chip printed next to the USB socket (CP210x: Silicon Labs "CP210x VCP";
CH340/CH9102: WCH "CH341SER"). Then decide the firmware variant:

| What you see | Board | Variant to flash |
|---|---|---|
| small shielded module marked **DWM3000**; `esptool` says ESP32 | Makerfabs ESP32 UWB DW3000 | `dw3000` (try this first) |
| module marked **DWM1000** (or a larger "Pro" module) | Makerfabs ESP32 UWB / Pro | `dw1000` |
| ESP32-**S3**, OLED display, separate "MaUWB" module | Makerfabs MaUWB (AT firmware) | none needed: step 3b, or `mauwb` |
| J-Link port, module marked DWM1001 | Qorvo DWM1001-DEV (PANS) | none: step 3b |

Not sure between DW3000 and DW1000? Flash `dw3000`: the firmware reads the chip's ID register and prints either
`# dw3000: DEV_ID=0xDECA0302 configured ...` (correct) or `... is a DW1000: flash firmware/bin/uwbkit_dw1000 instead`.
ESP32 or ESP32-S3: `.venv\Scripts\python -m esptool --port COM5 chip_id`.

Write the board model into `.secrets/hardware.md` on the bench machine so the project teams know.

## 2. Flash all five and give them names (10 min)

One binary fits every board; the ID is stored on the board afterwards. Guided loop (plug the boards in one at a time
when asked; **label each board with tape as you go**):

```powershell
cd uwb-kit
..\.venv\Scripts\python -m uwbkit flash --variant dw3000 --all 4A1T      # A1 A2 A3 A4 T1
#   other layouts:  --all 3A2T  (A1 A2 A3 T1 T2)     --all mesh5  (N1..N5, anchor-free)
```

By hand, one board (the same thing the loop does):

```powershell
..\.venv\Scripts\python -m esptool --chip auto --port COM5 --baud 460800 write_flash 0x0 firmware\bin\uwbkit_dw3000\uwbkit_dw3000.ino.merged.bin
..\.venv\Scripts\python -m uwbkit flash --assign --port COM5 --id A1
```

`python -m uwbkit flash --print` prints these commands for every variant, for Windows and Linux, plus the
`arduino-cli compile` / `upload` form if you would rather build from source.

**Works when:** each board answers `now id=A1 role=anchor chip=DW3000 fw=0.3.0 (saved)`. If esptool sits at
`Connecting....`, hold the board's BOOT button until it starts. Re-check any board later with `uwbkit identify`.

## 3. First sign of life (3 min)

Power all five (anchors on power banks is fine). Plug ONE anchor, say A1, into the laptop: it is the gateway and
prints every result it hears on the air, so the tag can stay on a battery.

```powershell
..\.venv\Scripts\python -m uwbkit hub --source serial:auto --host 0.0.0.0
#   3 anchors + 2 tags: add  --profile 3A2T      mesh boards (N1..N5): detected automatically (or --profile mesh5)
```

The first time, Windows asks whether Python may accept network connections: allow it on private networks
(needed for the phone and for projects on other machines). Open <http://localhost:1800/> (phone on the same Wi-Fi: `http://<laptop-ip>:1800/`; phone on USB: `adb reverse tcp:1800 tcp:1800`
then `http://localhost:1800/`). In a second terminal:

```powershell
..\.venv\Scripts\python -m uwbkit doctor
```

**Works when:** the console badge says **LIVE BOARDS**, the Links table shows `T1-A1 .. T1-A4` at roughly 10 Hz, and
doctor's only failure is "anchor coordinates are not set". Missing links: see the troubleshooting table.

### 3b. Boards that keep their vendor firmware

The hub reads these formats as they are (`uwbkit identify` tells you which one it found):
Makerfabs MaUWB `AT+RANGE=...` lines, DWM1001 PANS shell output (press Enter twice, type `les`), and the Makerfabs
DW1000 example sketches (`from: 1786  Range: 2.43 m`). Plug the **tag** into the laptop and run the same hub command.
You lose the kit's commands (calibration, survey, mesh) and get one tag per port; everything downstream is the same.

**This is also plan B if the kit firmware will not range on your boards:** flash the vendor's own example (the one
from the board's wiki page, which is known to work on that hardware), plug the tag in, and start the hub with a map
from the vendor's device ids to kit ids, so the anchors line up with your coordinates:

```powershell
..\.venv\Scripts\python -m uwbkit hub --source serial:auto --host 0.0.0.0 --id-map 1786=A1,1787=A2,1788=A3,1789=A4
```

(`1786` etc. are the short addresses the Makerfabs example prints after `from:`; for a DWM1001 use its four-digit hex
anchor ids. Without `--id-map` the anchors are numbered in the order they first appear: walk the tag up to each
anchor and watch which link drops to ~0 m to learn which is which.)

## 4. Place the anchors and tell the hub where they are (5 min)

Put A1..A4 at the corners of the largest rectangle you can, above head height if possible (tripods, tape on walls,
on top of monitors), antennas vertical, nothing metal within 10 cm. A1, A2, A3, A4 go **counter-clockwise** seen
from above. A1 is the origin, A1 to A2 is the +x axis, +y is to the left when you stand at A1 looking at A2.

Fastest (tape measure, rectangle): measure the width A1-A2 and the depth A1-A4 and the mounting height:

```powershell
..\.venv\Scripts\python -m uwbkit anchors rect 6.0 4.5 --z 2.0
```

Not a clean rectangle: tape the six distances between the anchors and let the kit solve the layout
(`uwbkit anchors tape`, it asks for each pair), or type coordinates (`uwbkit anchors set A1=0,0,2 A2=6,0,2 ...`),
or use the "Anchors" panel in the console. No tape measure: `uwbkit anchors auto --z 2.0` makes the anchors range
each other for 5 s and solves the layout (do step 5 first, or the layout inherits the antenna-delay error).

**Works when:** a coloured dot appears on the map where the tag really is, and `uwbkit doctor` ends with
`VERDICT: OK`. The coordinates are saved in `uwbkit_anchors.json` and reloaded when the hub restarts.

## 5. Calibrate the antenna delay (5 min; skip it if ranges are already within ~10 cm)

Uncalibrated boards read a constant 10-40 cm long or short. Check: hold the tag exactly 2.00 m from A1 (tape) and
read `T1-A1` in the console's Links table.

- Firmware fix (stored on the board): with the **tag on USB** and A1 2.00 m away, run
  `..\.venv\Scripts\python -m uwbkit anchors antdelay --node T1 --peer A1 --true 2.00`. The tag adjusts its own
  delay until the range reads 2.00 m and saves it. A command reaches the board that is plugged in (or any board on
  Wi-Fi), so for a perfect job repeat with each anchor on USB against the tag. For identical boards the tag alone is
  usually enough.
- No-reflash fix (lives in the hub, lost on hub restart): `uwbkit anchors bias --pair T1-A1 --true 2.00` for each link.

## 6. Walk test and a safety recording (3 min)

Walk the tag around the room. Watch the console: the dot follows you, quality stays above ~0.5, the ellipse stays
small. Then press **Record** in the console, walk for a minute, press it again. The file lands in the hub's working
directory; `uwbkit hub --source replay:<file> --loop` plays it back as if the boards were live (`hello.simulated`
stays false for a real recording). Keep this file: it is the demo's parachute.

## 7. Point the projects at the real hub (1 min each)

Every project reads the hub URL from `UWBKIT_URL` (or its own setting); nothing else changes:

- project running on the laptop: nothing to do (default `ws://127.0.0.1:1800/stream`);
- project hosted on the bench machine: start the hub with `--host 0.0.0.0` (as above) and set
  `UWBKIT_URL=ws://<laptop tailnet ip>:1800/stream` for that project.

The SIM badge in each project disappears by itself because `hello.simulated` is now false.

## 8. Optional: boards on Wi-Fi instead of a USB gateway (5 min)

On each board (USB serial monitor at 115200, or `uwbkit hub` + `POST /command`): `WIFI <ssid> <password>`,
`HUB <laptop ip> 1801`, `SAVE`. Run the hub with `--source udp:0.0.0.0:1801` (or both:
`--source serial:auto+udp:0.0.0.0:1801`) and allow UDP 1801 through the Windows firewall when it asks. Venue Wi-Fi
with client isolation will not work: use a phone hotspot.

## 9. Optional: run the hub on the Arduino UNO Q (15 min)

```powershell
python uwb-kit\deploy\unoq\make_wheelhouse.py                       # with internet: wheels for the UNO Q (19 MB)
python uwb-kit\deploy\unoq\push.py ssh arduino@<uno-q address>      # or:  push.py adb   (over the USB-C cable)
arduino-cli upload --fqbn arduino:zephyr:unoq -p <port> uwb-kit\firmware\uwbkit_unoq_matrix   # LED matrix sketch
```

`push.py` copies the kit and runs `deploy/unoq/install.sh` on the board: a venv, an offline install, and two systemd
user services (`uwbkit-hub`, `uwbkit-matrix`). Plug the gateway anchor into the UNO Q's USB. The LED matrix shows a
mini map with one dot per node online, flashes "SIM" when the data is simulated, and a cross when the hub is down.
Console: `http://<uno-q address>:1800/`. Logs: `journalctl --user -u uwbkit-hub -f`. This path is written from the
Debian/systemd/arduino-router documentation and has not been run on a real UNO Q; the laptop path above is the safe one.

## 10. If the boards will not range by demo time

Nothing downstream breaks, and nothing is faked:

0. Try plan B first (step 3b): the vendor's example firmware on the boards, parsed by the hub. Real ranges, real
   positions, `hello.simulated` false.
1. Replay a real recording from step 6: `uwbkit hub --source replay:<file> --loop`.
2. Otherwise run the simulator: `uwbkit hub --source sim --scenario <the project's scenario>`. Every UI shows the
   SIM badge; say out loud that the positions are simulated and show the boards separately.

---

## Troubleshooting

| Symptom | Most likely cause | Fix |
|---|---|---|
| No COM port appears | charge-only cable, missing USB driver | another cable; install the CP210x or CH340 driver; `uwbkit identify` |
| esptool stuck at `Connecting...` | board not in boot-loader mode | hold BOOT while it connects; close Arduino Serial Monitor; try `--baud 115200` |
| `identify` says "ESP32 boot log" after flashing | flashing failed or wrong chip family | flash again; ESP32-S3 boards need `--variant mauwb` |
| Banner says the chip is the other family | DW1000 binary on a DW3000 board or the reverse | flash the other variant (step 1 table) |
| `DEV_ID=0xFFFFFFFF` / `0x00000000` in the log | SPI wiring / CS pin differs from the Makerfabs default | `firmware/README.md`, "First power-up" item 2 |
| Hub: "serial port ... is not open" | another program owns the port | close serial monitors and other hubs; unplug and replug |
| Console says LIVE BOARDS but no links | only one board powered, or all boards have the same ID | power the others; `uwbkit identify` on each: IDs must differ |
| Some links missing (`doctor`: "not ranging") | that anchor is off, out of range, or has a duplicate ID | check power and ID; move within 10 m line of sight |
| `S` line fail count climbs, ok stays 0 | reply timing too tight for this board | rebuild with `-DUK_RESP_DLY_UUS=4000 -DUK_FINAL_DLY_UUS=4000` (firmware README) |
| All ranges off by a constant | antenna delay | step 5 |
| Position jumps when someone stands between tag and anchor | NLOS: the body blocks the direct path | raise the anchors above head height; the solver down-weights low `los` |
| Dot is mirrored or rotated | anchors numbered clockwise, or width and depth swapped | renumber with `uwbkit flash --assign`, or re-enter coordinates |
| Dot sits far outside the room | anchor coordinates wrong, or the tag height differs a lot from 1 m | re-measure; start the hub with `--tag-z <height>` |
| Quality low everywhere, big ellipse | anchors nearly in a line, or the tag is outside their outline | spread the anchors into a rectangle around the play area |
| Two tags: rate halves, occasional gaps | expected: they share air time | `RATE 5` on both tags if it gets bad |
| Wi-Fi mode: doctor says "no board has sent a UDP datagram" | firewall, client isolation, wrong hub IP | allow UDP 1801; use a phone hotspot; `HUB <ip> 1801`, `SAVE` |
| Phone cannot open the console | hub bound to 127.0.0.1 | start the hub with `--host 0.0.0.0`, or use `adb reverse tcp:1800 tcp:1800` |
| Hub will not start: "cannot listen" | another hub already uses the port | stop it, or add `--port 1801` and use that port in the URLs |
| Everything worked yesterday | anchors moved, or a board lost its saved ID | `uwbkit doctor`; redo step 4; `uwbkit identify` |

More depth on the firmware (pinouts with sources, over-the-air frames, every command, the first-power-up checklist):
`firmware/README.md`. What every tool does: `python -m uwbkit <tool> --help` (`identify`, `flash`, `doctor`,
`anchors`, `fakeboard`, `eval`, `unoq-matrix`).

## Time budget

| Step | Minutes |
|---|---|
| 0 install | 5 |
| 1 identify | 3 |
| 2 flash and name five boards | 10 |
| 3 first sign of life | 3 |
| 4 anchors | 5 |
| 5 antenna delay | 5 |
| 6 walk test + recording | 3 |
| 7 point projects at the hub | 1 each |
| slack for one thing going wrong | 10 |
| **total** | **about 45** |
