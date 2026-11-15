# Arduino UNO Q: a puck host, and optionally the whole command post

> **Written from documentation and forum reports, then partly checked on the owner's UNO Q** (Saturday 14 November,
> two sessions over SSH; see the next section). Everything not listed there is still unexecuted.
> Sources are cited inline; the notes are in `docs/research/puck_hardware.md` section 3. The UNO Q is **optional**:
> with Qorvo's stock firmware a puck needs only power, and the iPhone app reports every range.

## What was checked on the real UNO Q (14 November)

Seen on the board, over SSH as user `arduino`:

- Debian 13 (trixie), kernel 7.0, aarch64, 4 cores, 3.6 GB RAM, about 4 GB of disk free, Python 3.13.5, `git` present.
- **USB host through a hub works, and the serial driver is there.** With a USB-C multiport hub on the UNO Q's port, two
  DWM3001CDK boards appeared as two SEGGER J-Link devices and as `/dev/ttyACM0` and `/dev/ttyACM1` (`cdc_acm`). The
  `arduino` user is in the `dialout` group, so it opens them without `sudo`. How the hub and the UNO Q were powered was
  not seen.
- **`python3 -m venv .venv` fails as shipped**: the image has no `ensurepip` (Debian's `python3-venv` is not installed)
  and `sudo` asks for the board's password. This works without `sudo`:
  ```bash
  python3 -m venv --without-pip .venv
  curl -sS -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py && .venv/bin/python /tmp/get-pip.py && rm /tmp/get-pip.py
  .venv/bin/python -m pip install -r requirements.txt
  ```
  The install took 83 seconds on the venue Wi-Fi, and every dependency imported.
- `git clone` of this repository and `python3 -m beacon.host --help` ran.
- Bluetooth: `bluetoothctl` is installed, the controller is powered, and an ordinary (active) scan from the SSH shell
  listed about 300 advertisers in the hall. The passive scan that `--cold-scan` asks for was not tried.

- **The simulated walk-through ran to the end on the UNO Q** (`.venv/bin/python -m beacon.demo --source sim --story
  --tailnet`): the server answered in 4 seconds, both simulated phones reached a position, `/api/beacons` answered in
  about 0.1 seconds, and nothing was logged as an error. The server process used about 40 to 50 % of one of the four
  cores the whole time. Simulated data only: no board and no phone was involved.
- **The test suite on the UNO Q: 257 passed, 4 failed, in 6 minutes** (under 2 minutes on the build machine). All four
  failures are time limits written for a faster machine: the quick evaluation did not finish in its 60 seconds, the
  external-bundle walk-through and the story test ran out of their waiting time, and one position estimate from a
  Bluetooth-only reading took 40 ms against a 30 ms limit. The UWB estimates stayed inside the limit.

Not seen, or not run:

- The Beacon server in **live** mode, the puck host against a server, and reading a board's log (RTT) with this
  project's host were not run on the UNO Q.
- **The two boards on the UNO Q do not carry the Nearby Interaction firmware.** From about 5 pm that day they run a
  board-to-board ranging firmware (now in this repository as `hardware/twr_firmware/`; on 15 November the two boards
  ranged each other through it at about 4.9 Hz, `results/twr_measured_2026-11-15.json`), so an iPhone cannot range them
  until they are flashed with Nearby Interaction again (`hardware/FLASH.md`). Board to board and phones need different
  firmware: `hardware/BOARD_TO_BOARD.md`. Before that, two Bluetooth scans had found no advertiser offering the Nearby
  Interaction services and neither serial port answered `help`. Whether the owner's other boards carry the Nearby
  Interaction firmware is still unknown; the check with Qorvo's iPhone app (`hardware/BRINGUP.md`) is the test.
- **OpenOCD is already installed on the UNO Q** (0.12.0, with the J-Link driver and the nRF52 target), so SEGGER's tools
  are not needed there to read a board's RTT log or to flash a `.hex`. It needs `root` to open the J-Link. Flashing the
  TWR firmware with it worked on 15 November in this form (one board per `adapter serial`; stop anything holding the
  probe first): `openocd -c "adapter driver jlink" -c "adapter serial <JLINK_SERIAL>" -c "transport select swd" -f
  target/nrf52.cfg -c init -c "program build/INITIATOR/app.hex verify reset exit"`. Reading RTT with OpenOCD for this
  project's host is written in `hardware/twr_firmware/README.md`, not run.

## What the research says about the UNO Q

- Debian Linux on a Qualcomm QRB2210 (quad Cortex-A53), 2 or 4 GB RAM; radio Qualcomm WCN3980 (Wi-Fi 5 dual-band,
  Bluetooth 5.1, shared PCB antenna); **one USB-C port** "with Role-Switching Capabilities"; USB PD "requests a 5 V / 3 A
  contract only"; "brief dips can cause resets or link drops" ([datasheet](https://docs.arduino.cc/resources/datasheets/ABX00162-ABX00173-datasheet.pdf)).
- **Use a USB-C hub/dongle with a PD (power) input.** That is the supported and reported-working way to plug USB devices
  in: "The UNO Q does not supply power to the USB-C dongle"; when the UNO Q is powered from VIN, "the USB-C port does not
  function in host mode"; Arduino staff reportedly called that "not a supported scenario"
  ([forum 1442207](https://forum.arduino.cc/t/uno-q-usb-port-not-working-if-board-powered-from-5v/1442207)). Any PD
  dongle "except Apple ones" ([manual, mirror](https://ja-bots.com/wp-content/uploads/2026/03/UNO-Q-User-Manual-_-Arduino-Documentation.pdf));
  some dongles do not work ([forum 1410296](https://forum.arduino.cc/t/uno-q-usbc-dongle-issue/1410296)).
- **Work over SSH, not inside App Lab:** "Uno Q can see dev/ttyACM* but python scripts in App Lab are containerized",
  and the container "can't reliably see USB CDC" ([forum 1433482](https://forum.arduino.cc/t/arduino-uno-q-app-lab-as-a-usb-host-to-an-uno-r3-python-environment-sees-0-devices/1433482)).
- **Bluetooth scanning:** a Qualcomm project runs a bleak scanner on the UNO Q's Debian side over SSH because it "needs
  direct BLE and D-Bus access" ([Qualcomm](https://www.qualcomm.com/developer/project/building-a-practical-asset-tracking-system-with-arduino-uno-q)).
- **SEGGER J-Link software exists for Linux arm64** (DEB and TGZ), so the UNO Q can in principle read a board's RTT log
  through J9 ([SEGGER](https://www.segger.com/news/seggers-complete-j-link-software-now-available-for-linux-on-arm/)).

**Confirmed on the owner's board:** the stock kernel has the `cdc_acm` driver (section above). **Not confirmed:**
the current limit of the host-mode power switch; whether a bare USB-C-to-micro-B cable (no hub) puts the UNO Q into host
mode while it is powered some other way; whether passive Bluetooth scanning works on the WCN3980; whether SEGGER's arm64
build supports the board's on-board J-Link on this kernel (likely). The third-party fix that forces host mode by writing
to `/sys/kernel/debug/usb/.../mode` exists (same forum thread); do not use it tonight.

## Wiring (both jobs)

```
 5 V / 3 A USB-C PD supply  ──►  PD input of the USB-C hub/dongle (not Apple)
                                    │ USB-C plug ──► UNO Q's USB-C port
                                    │ USB-A port ──► micro-USB DATA cable ──► puck board
                                                     J9 ("J-Link", bottom) if you want its RTT log
                                                     J20 (upper) if it only needs power
```

A battery bank works as the PD supply only if it delivers 5 V / 3 A over USB-C PD; otherwise the UNO Q may reset.

## Getting a shell

1. Put the UNO Q on the same Wi-Fi as the laptop and the iPhones: tonight, the Android phone's hotspot (Arduino's
   first-run setup asks for a Wi-Fi network).
2. Find its address (your router's or hotspot's client list; or `hostname -I` in a terminal on the board). Call it
   `UNOQ_IP`.
3. `ssh <user>@UNOQ_IP` from the laptop. The user name is the one set during the board's first-run setup (`arduino`
   on the owner's board).
4. Get the project onto the UNO Q (needs internet once, so do it before you leave, or on a hotspot with mobile data):
   ```bash
   git clone https://github.com/iujab/sandtable-beacon
   cd sandtable-beacon/sandtable-beacon-victim-phones
   ```
   No internet on the UNO Q: copy the folder from the laptop instead, without the laptop's `.venv` (it will not run on
   arm64): `rsync -a --exclude .venv --exclude data sandtable-beacon <user>@UNOQ_IP:~/` (or zip it and `scp` the zip).

## A. Puck host (10 min)

The host is pure standard-library Python: **no install needed** for the heartbeat. On the UNO Q:

```bash
cd ~/sandtable-beacon/sandtable-beacon-victim-phones
python3 -m beacon.host --help
python3 -m beacon.host --server http://LAPTOP_IP:1885 --puck P1 --name uno-q
```

`P1` is the puck id you gave this board on the command board (BRINGUP step 7). `LAPTOP_IP` is the address the server
printed (BRINGUP step 4.1). If the server runs on the UNO Q itself
(job B), use `--server http://127.0.0.1:1885`.

**Works when** the command board lists the puck with host `uno-q`. The host prints one line when the server is
unreachable and one when it is back; it keeps retrying. Keep it running after you log out:
`nohup python3 -m beacon.host --server http://LAPTOP_IP:1885 --puck P1 --name uno-q > host.log 2>&1 &`
(or run it inside `tmux`).

Try it first with no board and no server: `python3 -m beacon.host --puck P1 --dry-run --no-post` prints what it would
send, from built-in sample lines that are **not measurements**. **Never run `--dry-run` against the live demo server**:
the sample ranges would be posted as if the puck had measured them.

### A.1 Optional, experimental: the puck's own range log over RTT

The stock firmware writes its log over SEGGER RTT on J9 ("QNI uses a rtt_reporter task to print all the info",
[forum 13613](https://forum.qorvo.com/t/dwm3001cdk-nearby-interaction-trying-to-get-ranging-via-usb-uart/13613)). Whether
it prints a distance per ranging round is **not confirmed** (an early 2022 release printed lines like
`{"TWR": {"R":6613,"a16":"0x0001","S":"ERR","D cm":0}}`, [forum 12439](https://forum.qorvo.com/t/dwm3001cdk-what-should-i-do-to-start-ranging-between-the-two-boards/12439)).
If it does, the host forwards each distance as `{"kind": "uwb_puck", ...}`, which keeps a phone's ring alive when the
phone's screen goes off (`docs/BEACON_FEED.md` A7).

1. Download SEGGER's J-Link pack for **Linux, ARM 64-bit, DEB** on the laptop
   (<https://www.segger.com/downloads/jlink/>), copy it over (`scp`), and on the UNO Q: `sudo dpkg -i JLink_Linux_*_arm64.deb`.
   Unplug and replug the board afterwards.
2. Board's **J9** into the dongle.
3. Terminal 1 (keep it open; this J-Link connection is what serves the RTT telnet port 19021,
   [SEGGER](https://kb.segger.com/RTT)):
   ```bash
   JLinkExe -device NRF52833_XXAA -if SWD -speed 4000 -autoconnect 1
   ```
   If the board stops advertising after this, type `g` and Enter.
4. Terminal 2:
   ```bash
   python3 -m beacon.host --server http://LAPTOP_IP:1885 --puck P1 --name uno-q --rtt 127.0.0.1:19021 --learn
   ```
   `--learn` prints every raw line and what the parser made of it. **If no line ever says `RANGE`, copy ten lines and send
   them to the bench**: the parser is written from forum examples and gets fixed from a real sample.
   Distances printed with no unit: add `--unitless cm` (or `m`) once you know which it is.

Alternative to steps 3 and 4: `JLinkRTTLogger -Device NRF52833_XXAA -if SWD -Speed 4000 -RTTChannel 0 ~/qani_rtt.log`
(options from [SEGGER](https://kb.segger.com/J-Link_RTT_Logger); the file name goes last, not confirmed: if it asks
questions instead, answer with the same values), then `python3 -m beacon.host ... --rtt-log ~/qani_rtt.log`. Use one or
the other, not both at once. On Linux the tools may be named `JLinkRTTLoggerExe` and so on; `ls /opt/SEGGER/JLink/` shows
the real names.

### A.2 Optional, experimental: the SDK's CLI firmware on a serial port

Only if a board runs Qorvo's DW3_QM33 SDK **CLI** firmware instead of the Nearby Interaction firmware (it prints JSON
lines with `D_cm` on J20 at 115200 baud, [forum 13430](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430);
that firmware does not talk to iPhones). Needs `pyserial`:

```bash
python3 -m venv ~/hostenv && ~/hostenv/bin/pip install pyserial
~/hostenv/bin/python -m beacon.host --server http://LAPTOP_IP:1885 --puck P5 --name uno-q --serial auto --learn
```

### A.3 Optional, never run by its authors: cold Bluetooth count

Counts Bluetooth advertisers near the puck per signal-strength band (near / mid / far) in 10 s windows and posts **only
the three counts**. Addresses are held only as salted hashes in memory for one window (the salt is random per run);
nothing is written to disk; nothing else is sent. Devices, not people (watches, earbuds and laptops advertise too; one
phone may count twice when its random address rotates).

```bash
python3 -m venv ~/hostenv && ~/hostenv/bin/pip install bleak
~/hostenv/bin/python -m beacon.host --server http://LAPTOP_IP:1885 --puck P1 --name uno-q --cold-scan
```

It asks bleak for a **passive** scan (it never sends scan requests). If the UNO Q's radio or BlueZ refuses passive mode,
the host says "cold scan disabled" and carries on without it; it does not fall back to an active scan. Not confirmed on
the WCN3980 (above). On a Mac the cold scan always stays off ("Passive scanning is not supported on macOS!",
[bleak](https://bleak.readthedocs.io/en/latest/api/scanner.html)).

## B. The UNO Q as the whole command post (15 min, needs internet once)

Runs the Beacon server (port 1885) on the UNO Q; the laptop and the iPhones only need a browser and the app.

```bash
cd ~/sandtable-beacon/sandtable-beacon-victim-phones
python3 -m venv --without-pip .venv                       # the image has no ensurepip, so plain "-m venv" fails
curl -sS -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py && .venv/bin/python /tmp/get-pip.py && rm /tmp/get-pip.py
.venv/bin/python -m pip install -r requirements.txt       # needs internet; every dependency is a wheel (83 s on the owner's board)
.venv/bin/python -m beacon.demo --source live --lan       # live mode, reachable from the Wi-Fi (not yet run on the board)
```

- The other way to get `venv` with pip: `sudo apt install python3-venv` (needs the board's password), then plain
  `python3 -m venv .venv`.
- The board: `http://UNOQ_IP:1885/` from the laptop. In the iOS app: server address `UNOQ_IP:1885`.
- Check from an iPhone's Safari: `http://UNOQ_IP:1885/healthz` must answer `{"ok": true, ... "source": "live" ...}`.
- Both jobs at once: start the server, then the host with `--server http://127.0.0.1:1885`.
- Keep it running after you log out: `nohup .venv/bin/python -m beacon.demo --source live --lan > server.log 2>&1 &`.

**If anything here fights you, drop it**: run the server on the laptop (`hardware/BRINGUP.md` step 4.1) and power the
pucks from battery banks. The demo does not depend on the UNO Q.
