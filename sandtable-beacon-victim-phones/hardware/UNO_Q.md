# Arduino UNO Q: a puck host, and optionally the whole command post

> **Written from documentation and forum reports; not executed on a board.** No UNO Q was used to write or test this.
> Sources are cited inline; the notes are in `docs/research/puck_hardware.md` section 3. The UNO Q is **optional**:
> with Qorvo's stock firmware a puck needs only power, and the iPhone app reports every range.

**Tonight it is part of puck A**: one battery bank powers the UNO Q (through a USB-C hub/dongle with a PD input) and
board A1, which plugs into the hub. The UNO Q joins the Android phone's Wi-Fi hotspot like everything else
(`hardware/BRINGUP.md` step 4).

Two jobs it can do, independently:

- **A. Puck host** (10 min): power one puck and tell the board it is hosted; optionally read that puck's debug log
  (experimental) and count nearby Bluetooth devices (optional, never run).
- **B. Command post** (15 min): run the Beacon server itself, so the laptop only needs a browser.

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

**Not confirmed:** that the stock UNO Q kernel has the `cdc_acm` driver (users see `/dev/ttyACM*`, which suggests it);
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
3. `ssh <user>@UNOQ_IP` from the laptop. The user name is the one set during the board's first-run setup (often
   `arduino`; not confirmed).
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
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt       # needs internet; every dependency is a wheel
.venv/bin/python -m beacon.demo --source live --lan       # live mode, reachable from the Wi-Fi
```

- `python3 -m venv` fails with "ensurepip is not available": Debian ships venv separately; run `sudo apt install python3-venv`
  (on the UNO Q, not on any other machine), then repeat.
- The board: `http://UNOQ_IP:1885/` from the laptop. In the iOS app: server address `UNOQ_IP:1885`.
- Check from an iPhone's Safari: `http://UNOQ_IP:1885/healthz` must answer `{"ok": true, ... "source": "live" ...}`.
- Both jobs at once: start the server, then the host with `--server http://127.0.0.1:1885`.
- Keep it running after you log out: `nohup .venv/bin/python -m beacon.demo --source live --lan > server.log 2>&1 &`.

**If anything here fights you, drop it**: run the server on the laptop (`hardware/BRINGUP.md` step 4.1) and power the
pucks from battery banks. The demo does not depend on the UNO Q.
