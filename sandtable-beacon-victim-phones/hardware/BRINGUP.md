# Sandtable Beacon: bring-up tonight, from boards in a bag to two phones on the board

> **Written from documentation and forum reports; not executed on a board.** Nothing in this project has touched a
> DWM3001CDK, a J-Link, a UNO Q or an iPhone yet. Every hardware fact below carries its source URL; the full notes are
> `docs/research/puck_hardware.md` and `docs/research/ios_ni.md`. Where a fact is **not confirmed**, the step tells you
> what to do for either answer.

For one tired person. Follow the numbers. Every step says how long it takes, what success looks like, and what to do
when it fails. **Total: about 2 hours** the first time (the iOS build is the long part), plus however long Qorvo's
download email takes, so **start step 0.1 first**.

**Tonight's demo:** two pucks and two "trapped" phones.

- **Puck A**: a DWM3001CDK powered together with the Arduino UNO Q from a battery bank (the UNO Q can also run the
  Beacon server and the puck host).
- **Puck B**: a DWM3001CDK powered by the Android phone through a USB-OTG adapter.
- **Victims**: the iPhone 14 and the iPhone 15, each running our app.
- A third puck is "better if you have it" (step 7).

How it works, in one breath: each board runs Qorvo's stock, unmodified Nearby Interaction firmware ("QANI"). Our iPhone
app connects to the boards over Bluetooth, runs one Apple Nearby Interaction session per board, reads each distance,
and posts it over Wi-Fi to the Beacon server (`POST /api/ingest`, port 1885). The server turns the distances, the
measured puck positions and the building twin's walls into a ring, two candidates or one position on the command board.
**A puck needs only power.**

Getting the software (on the laptop, and on the UNO Q if you use it):

```bash
git clone https://github.com/iujab/sandtable-beacon
cd sandtable-beacon/sandtable-beacon-victim-phones
```

Every command below runs from that folder. Commands are given for macOS (the Mac), Windows PowerShell and Linux.

---

## 0. What you need on the table, and the downloads only you can do (10 min, plus the email wait)

**Do the Qorvo download request FIRST** (0.1): it emails you a link and may take a while.

| Thing | How many | Notes |
|---|---|---|
| Qorvo DWM3001CDK boards | 2 for the demo (4 for step 8c, 5 owned) | label them with tape now: **A1, B1** (and A2, B2, spare) |
| micro-USB **data** cables | 1 for flashing, plus 1 per powered board | charge-only cables look identical and are the classic time sink ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)). Test each cable once in step 1 and mark the good ones |
| battery bank for puck A and the UNO Q | 1 (5 V / 3 A USB-C PD for the UNO Q) | see step 6 and `hardware/UNO_Q.md` |
| Arduino UNO Q + USB-C hub/dongle with a PD (power) input, not an Apple one | 1 | optional for puck A: a plain battery bank also powers a board |
| Android phone + **USB-C OTG adapter** (USB-C plug to USB-A socket) **or** a USB-C to micro-B cable | 1 | powers puck B and makes the Wi-Fi hotspot (step 4) |
| a spare battery bank or wall adapter | 1 | the fallback for puck B (step 6) |
| the Mac with Xcode | 1 | builds the iOS app and flashes the boards |
| iPhone 14 and iPhone 15 | 2 | charged, with their cables to the Mac |
| a laptop for the server | 1 | can be the Mac itself |
| stands for the pucks | 2 or 3 | cups, boxes or books, **15 cm or taller** (step 7 says why) |
| masking tape, marker, tape measure | | for labels, the demo rectangle and puck positions |

The board's two micro-USB ports ([Zephyr board page](https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html), [forum 13430](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430)):

- **J9**: the **bottom** micro-USB, labelled **"J-Link"**. Flashing, debug log (RTT), power. The on-board J-Link chip
  behind it shows up as a USB device to whatever it is plugged into.
- **J20**: the **upper** micro-USB, nearer the module. The nRF52833's own USB; it only shows up as a USB device if the
  firmware implements one, and the stock firmware does not ("QANI has no USB function",
  [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)). With the stock firmware J20 only
  gives power.
- Either one powers the board ([product brief](https://www.firaconsortium.org/sites/default/files/2024-08/DWM3001CDK%20Product%20Brief.pdf)).

### 0.1 Download Qorvo's Nearby Interaction firmware (only you can do this)

1. Open **<https://www.qorvo.com/products/d/da008212>** (the link Qorvo staff gave for the Nearby Interaction
   release in June 2026, [forum 25771](https://forum.qorvo.com/t/access-request-for-qani-firmware-dwm3001cdk/25771)).
2. Fill in the form with a **work or school email address, not Gmail**. Users report that personal addresses fail,
   and Qorvo said it is "aware of the issue" ([forum 25771](https://forum.qorvo.com/t/access-request-for-qani-firmware-dwm3001cdk/25771),
   [forum 19160](https://forum.qorvo.com/t/new-release-of-dw3000-and-qm33-sdk-dw3-qm33-sdk-v1-0-2/19160)).
   The form emails a **one-time** link: download straight away and keep the zip.
3. If that page does not work, try the DW3_QM33 SDK at **<https://www.qorvo.com/products/d/da008582>** (same form,
   same caveat). Users find a QANI image inside it at `SDK/Binaries/DWM3001CDK/DWM3001CDK-QANI-FreeRTOS.hex`
   ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).
4. Unzip and find the hex file. In order of preference:
   - `DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex`: reported working with an iPhone in Feb 2026
     ([forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229)).
   - `DWM3001CDK-QANI-FreeRTOS.hex`: what Qorvo support told a user to flash, which then worked (Aug 2025, same thread).
   - **Not** `nRF52840DK-QANI-FreeRTOS.hex` or anything with another board's name: that is the wrong board, and the
     app then sits at "Scanning for accessories" (same thread).
   - Mixed reports, so keep both files if you have both: one user found the SDK 1.1.1 QANI hex worked, another said the
     SDK hex "never worked for me" until they built it themselves ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).
5. **Keep the hex out of the repository**: either outside the project folder, or as `data/qani.hex` inside it
   (`data/` is git-ignored). Qorvo's licence was not found; the hex must never be committed or published.

**If the download never arrives:** step 1 may show your boards already carry Nearby Interaction firmware. If they do
not, go to the degradation ladder (step 10), rung 5.

### 0.2 Install SEGGER J-Link software (5 min, free, no account)

<https://www.segger.com/downloads/jlink/> → "J-Link Software and Documentation Pack" → your system (macOS: the
Apple Silicon or Universal installer; Windows: the Windows installer; the UNO Q: Linux ARM 64-bit DEB). Accept the terms.
It is "publicly available for download, at no charge" ([SEGGER](https://www.segger.com/news/seggers-complete-j-link-software-now-available-for-linux-on-arm/)).
It contains J-Flash Lite (the flashing window), J-Link Commander (`JLinkExe`) and the RTT tools.

### 0.3 Install Qorvo's free iOS app on both iPhones (2 min)

App Store: **"Qorvo Nearby Interaction"** (<https://apps.apple.com/app/id1615369084>), Qorvo's own evaluation app for
these kits.

---

## 1. Five-minute check before flashing anything (5 min for the first board, 2 min for each other)

Your boards may already carry Nearby Interaction firmware: Qorvo said in 2022 that the preloaded sample "is to be used
with an iPhone equipped with a U1 UWB chip" ([forum 12439](https://forum.qorvo.com/t/dwm3001cdk-what-should-i-do-to-start-ranging-between-the-two-boards/12439)).
Not confirmed: what your boards ship with today. So check before you flash.

1. Plug board A1 into a battery bank or the Mac with a micro-USB data cable, into **J20** (the upper port). A power LED
   should light. (What the other LEDs mean under the stock firmware is not documented in the research; ignore them.)
2. On the iPhone 14: Bluetooth on. Open **Qorvo Nearby Interaction**. Allow Bluetooth and Nearby Interaction when asked.
3. Wait up to 30 s for a board to appear in the list. Tap it.

| What you see | It means | Do |
|---|---|---|
| a distance in metres that changes when you move the phone | this board already runs Nearby Interaction firmware | write the name the app shows on the board's tape label. **Skip step 2 for this board.** Check the next board |
| a board appears, connects, but no distance after 30 s | old or incompatible firmware | flash it (step 2) |
| nothing appears ("Scanning for accessories") | no NI firmware, or no power | try another cable and the other USB port (J9). Still nothing: flash it (step 2) |
| the board's LED does not light at all | no power: charge-only cable, dead bank | another cable, another power source |

**Close Qorvo's app completely when you are done** (swipe it away in the app switcher): a board serves one phone at a
time, and Qorvo's app keeps its connection (step 8).

**Not confirmed: the board's Bluetooth name.** An nRF52840 build advertised as "nRF52840DK"
([forum 25749](https://forum.qorvo.com/t/nrf52840dk-dwm3000evb-qani-hangs-after-sent-shareable-configuration-data/25749));
the DWM3001CDK's name is unknown. Whatever it is, write it down: you will match it on the board's tray in step 7.

---

## 2. Flash the stock firmware (5 min for the first board, 2 min for each other)

Only for boards that failed step 1. **Full click path and the command-line version: `hardware/FLASH.md`.** In short:

1. Micro-USB **data** cable from the Mac into **J9** (the bottom port, labelled "J-Link").
2. Open **J-Flash Lite** (installed in 0.2). Device: **`NRF52833_XXAA`** (type "52833" in the search box). Interface:
   **SWD**. Speed: **4000** kHz. OK.
3. Data file: the hex from 0.1. **Erase Chip**, then **Program Device**.
4. Unplug, plug back in (this restarts the board), and repeat step 1's check.

**Pick nRF52833, not nRF52832**: "The cdk is 52833, picking the wrong device will mess things up"
([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).

**Works when:** step 1's check now shows a distance. **Fails:** the troubleshooting table (step 11) and
`hardware/FLASH.md` "Recover a board".

---

## 3. Prove one board and one iPhone with Qorvo's app (5 min)

Before you touch our own app, prove the radio link with Qorvo's.

1. Stand board A1 **upright** on a stand 15 cm or more tall (step 7 says why), powered.
2. iPhone 14, Qorvo's app, connect to A1. Hold the phone upright, back toward the board, about 1 m away.
3. Lay the tape measure on the floor from the board. Hold the phone at **1.00 m**, then **2.00 m**, then **3.00 m**;
   write down what the app shows each time. Walk away to 5 m and back: the number should follow you.
4. Repeat once with the iPhone 15 (close the app on the iPhone 14 first).

**Works when:** the distance follows you and stays within a few tens of centimetres of the tape. For scale: there is no
published accuracy for an iPhone against this board; a study of phones against DW3000 boards found about 13 to 15 cm
(1 sigma) in ordinary rooms ([arXiv 2303.11220](https://arxiv.org/html/2303.11220v1)), and forum users report 40 to
50 cm errors between boards in some setups ([forum 20404](https://forum.qorvo.com/t/how-to-reduce-distance-d-cm-error/20404)).
Your three numbers are the honest accuracy you can quote tonight.

**Fails:**
- Distance jumps around: is the board lying flat? Stand it upright, non-component side toward the phone ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)). Is your body between phone and board?
- Distance on one iPhone but not the other: note it, carry on with the phone that works.
- No direction arrow: **expected.** Developers report `supportsDirectionMeasurement == false` and a nil `direction` on
  the iPhone 14 family and on an iPhone 15 with this exact board ([Apple forum 822522](https://developer.apple.com/forums/thread/822522),
  [720292](https://developer.apple.com/forums/thread/720292)). Our demo uses distances only.

---

## 4. Networking at the table (10 min)

The range reaches the server **through the iPhone app**, so **both iPhones must reach the Beacon server** over the
network. Venue Wi-Fi usually blocks device-to-device traffic ("client isolation"): do not plan on it.

| Where the server runs | Network | When to use it |
|---|---|---|
| **the laptop** (recommended) | **the Android phone's Wi-Fi hotspot**; the laptop and both iPhones join it | the plan: no venue Wi-Fi, no internet needed, the Android phone is on the table anyway |
| the UNO Q ("the box") | the same Android hotspot | when you want the self-contained box story; `hardware/UNO_Q.md` part B |
| the laptop | an iPhone's own hotspot | works too, but it costs that iPhone's battery and that iPhone is a victim phone |
| the laptop | venue Wi-Fi | only if `/healthz` (below) opens from both iPhones; usually blocked |
| the bench machine, over the tailnet | Tailscale: both iPhones need the Tailscale app, signed in to the same tailnet, plus internet | **the fallback, not the plan**: it depends on the venue's internet. On the bench machine: `.venv/bin/python -m beacon.demo --source live --tailnet`; in the app type its tailnet IP address (`tailscale ip -4` there prints it), not a name (`docs/IOS_BUILD.md` step 7) |

**Not found in the research: whether the Android phone can run a Wi-Fi hotspot while it powers a board over OTG.**
Unknown: test it for 10 minutes in step 6. If it cannot, power puck B from a battery bank and keep the hotspot.

### 4.1 The recommended setup

1. Android phone: Settings → hotspot (often "Hotspot & tethering") → on. Note its name and password.
2. Laptop and both iPhones: join that Wi-Fi.
3. On the laptop, start the server in live mode:

   macOS or Linux:
   ```bash
   python3 -m venv .venv                                    # once
   .venv/bin/python -m pip install -r requirements.txt      # once, needs internet (do it before you leave)
   .venv/bin/python -m beacon.demo --source live --lan
   ```
   Windows PowerShell:
   ```powershell
   py -m venv .venv                                         # once
   .venv\Scripts\python -m pip install -r requirements.txt  # once, needs internet
   .venv\Scripts\python -m beacon.demo --source live --lan
   ```
   It prints the address to give the phones, e.g. `phones (Wi-Fi) http://192.168.43.20:1885`. Call it `SERVER`.
   The first time, macOS or Windows asks whether Python may accept incoming connections: **allow** (Windows: private
   networks).
4. Open the board on the laptop's own browser: <http://localhost:1885/>. It shows a **LIVE** badge (no SIM badge).

**Check from each iPhone:** in Safari open `SERVER/healthz` (for example `http://192.168.43.20:1885/healthz`).

- **Works when** it shows `{"ok": true, ... "source": "live" ...}` on **both** iPhones.
- Safari cannot connect: the firewall prompt was refused (macOS: System Settings → Network → Firewall → allow Python;
  Windows: allow Python on private networks), or a phone is on another Wi-Fi, or a typo.
- `"source": "sim"`: the server was started without `--source live`. Ctrl-C and start again.

---

## 5. Our iOS app, and the first ring on the board (30 to 45 min)

### 5.1 Build and install our app (20 to 30 min the first time)

Follow **`docs/IOS_BUILD.md`** (Xcode, signing with your Apple ID, install on both iPhones). Our app is uncompiled
source until you build it. Come back here when the app opens on the phone.

### 5.2 Point the app at the server (2 min per phone)

1. **Close Qorvo's app** on this phone (swipe it away).
2. Easiest: the board page on the laptop shows a **QR code**; scan it with the iPhone's Camera app, and it opens our app
   with the address filled in. If the board says it has no QR code, type it: in our app, the gear icon → **Rescue
   server** → `SERVER` → **Test connection** → "Server reachable." (`docs/IOS_BUILD.md` step 7).
3. In the app's setup, pick your suite (**Load suites from server** → **Suite 310**) on **both** phones. This is the
   occupant's statement that lets the board dismiss a mirror image (step 7).
4. **Use at most 2 boards** on both phones.

### 5.3 The first ring (5 min)

1. Power board A1 only. On the iPhone 14 start sharing (**Find beacons**, then **I NEED HELP**); allow Bluetooth and
   Nearby Interaction.
2. **Keep the app open, in the foreground, screen on.** If the app goes to the background, Apple suspends the Nearby
   Interaction session ([Apple](https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session)).

**Works when:** within about 30 s the app shows a distance to A1, and the board on the laptop shows A1 in the
**unplaced pucks tray** (its Bluetooth name, `#`, four hex digits). Pick it in the tray, click the plan roughly where it
stands: a **ring** appears around it. That ring is the phone. Step 7 places the pucks properly.

**Fails:**
- App shows a distance, board shows nothing: the app cannot reach the server. Re-check `/healthz` in Safari, and the
  address in the app.
- App never shows a distance, but Qorvo's app did in step 3: see `docs/IOS_BUILD.md` troubleshooting. Meanwhile the
  ladder (step 10), rung 4, keeps the demo honest.
- Tray stays empty while the app shows distances: the server is not in live mode (step 4.1 check).

---

## 6. Power the two pucks (10 min, plus a 10-minute soak test)

### 6.1 Puck A: battery bank (+ the UNO Q)

- Simplest: board A1's micro-USB into the battery bank.
- With the UNO Q: the bank feeds the UNO Q through a USB-C hub/dongle with a PD input, and board A1 plugs into the
  hub's USB-A port (wiring and setup: `hardware/UNO_Q.md`). The UNO Q can then also run the puck host
  (on the UNO Q, from the project folder: `python3 -m beacon.host --server SERVER --puck P1 --name uno-q`; the board
  then shows the puck as hosted) and, if you want, the server itself.
- Battery-bank risk: "Some large capacity power banks turn off their supply after a short period of time when
  detecting low current draw" ([forum 22115](https://forum.qorvo.com/t/dwm3001cdk-board-transmits-slower-on-battery-power/22115)).
  If the board goes dark after a few seconds or minutes, use **J9** (the J-Link chip adds about 10 to 15 mA of load,
  [forum 13973](https://forum.qorvo.com/t/does-any-one-know-where-to-find-the-offical-power-requirement-of-the-dwm3001cdk/13973)),
  a bank with an always-on or low-current mode, or a wall adapter. (Sharing the bank with the UNO Q should keep it awake;
  that is reasoning, not a test.)

### 6.2 Puck B: the Android phone over USB-OTG

What the research says ([puck_hardware.md section 4](../docs/research/puck_hardware.md)): an Android phone with USB host
(OTG) support puts 5 V on the cable, usually budgeted around 500 mA, plenty for one board; some phones cut the power in
software above about 500 mA ([arachnoid.com](https://arachnoid.com/usb_power/index.html)). Not confirmed: your
phone's OTG current limit.

**Cable:** a USB-C OTG adapter (USB-C plug into the phone, USB-A socket) plus a micro-USB **data** cable, or one USB-C
to micro-B cable. **Plug it into the board's J9** (the J-Link port).

What can go wrong, and how to tell (the first three are reported behaviours of some phones, not checked for yours):

| Problem | How you tell | Do |
|---|---|---|
| the phone switches 5 V on only when a USB device shows up | the board's LED stays dark | use **J9**: the J-Link chip shows up as a USB device; J20 with the stock firmware does not |
| the phone switches OTG off after an idle timeout | the LED goes dark after some minutes; the board disappears from the app | look for an **"OTG"** switch in the phone's settings (Settings → search "OTG") and turn it on just before the demo; J9 keeps a USB device attached; otherwise use the fallback |
| the phone asks what to do with the USB device | a notification or dialog | dismiss it (or pick "no data transfer" / "charging only" if offered); power stays on |
| wrong cable | LED dark, nothing changes with J9 | a cable or adapter that really is OTG; a data cable |
| battery drain | the Android phone's battery falls during the evening | **not confirmed**: no whole-board current figure was found (the J-Link chip "something around 10-15mA", the UWB chip peaks at about 48 to 78 mA, [DW3000 datasheet mirror](https://atta.szlcsc.com/upload/public/pdf/source/20220621/2EAA42E8F3E2D83DDB8686FF8DCFAD44.pdf)). Charge the phone full before the demo; a hotspot drains it too |

**Fallback:** a second battery bank or a wall adapter for puck B. It changes nothing else.

### 6.3 Soak test (10 min)

Both pucks powered, Android hotspot on, our app ranging both boards: leave it for **10 minutes**. **Works when** both
boards still give distances after 10 minutes and the hotspot is still up. If puck B drops, switch it to the fallback now,
not during the demo.

---

## 7. Place the two pucks (10 min)

**Read `hardware/LAYOUT.md` once** (one page, one figure). In short: two rings cross twice, at the phone and at its
mirror image across the line through the pucks. Put **both pucks just inside the wall the door is in**, so the mirror
image of anyone in the room lands on the other side of that wall, and the board can dismiss it (shown crossed out, with
the reason). With the pucks mid-room, nothing can decide between the two points.

<!-- LAYOUT_NUMBERS:BEGIN -->
**Simulated** (`results/eval_two_puck.json`; 800 phone positions per layout over the fire room; what a real iPhone would deliver: UWB distances only, no line-of-sight flag; noise model with sources in `beacon/models.py`):

| Layout | One position | of which by the twin alone | of which after the occupant's stated suite | Cannot choose (two spots) | Ring or less | Wrong position: share of the positions shown (share of all trials) | Position error, median / 90th percentile |
|---|---|---|---|---|---|---|---|
| Both pucks just inside the wall the door is in (recommended) | 76% | 35% | 42% | 3% | 21% | 1.5% (1.1%) | 0.26 m / 0.77 m |
| Both pucks along the outside wall | 70% | 70% | 0% | 11% | 18% | 2.3% (1.6%) | 0.25 m / 0.78 m |
| Both pucks mid-room (careless) | 4% | 4% | 0% | 86% | 10% | 3.0% (0.1%) | 0.30 m / 0.73 m |
| Three pucks: the door-wall pair plus one in the far corner | 84% | 78% | 6% | 8% | 8% | 1.2% (1.0%) | 0.20 m / 0.59 m |

Two phones taking turns on two boards (simulated, 100 phone scenes): the wait for a position is 3.5 s in the median and 9 s at the 90th percentile; 23% get none within the scene; 2.7% of the positions shown are wrong.

"Ring or less" is mostly a phone that only one puck reached (a blocked link in the simulation). A wrong position is one more than three sigma or 1.5 m from the truth.

When the occupant's stated suite is false (the phone is really in a neighbouring room, 300 trials): with the door-wall layout the board mostly shows a ring, but it shows a position in 6.3% of trials and 100% of those positions are wrong (6.0% of trials are wrong positions produced by the statement; 22.5% for the suite that shares that wall). That is the price of dismissing a spot on a statement, and why the board shows the dismissal.
<!-- LAYOUT_NUMBERS:END -->

Each puck:
- **upright** (antenna end up), on a stand **at least 15 cm off the floor**: "15 cm separation from the ground plane
  ... is required to mostly negate this behavior" ([forum 8712](https://forum.qorvo.com/t/position-of-the-tag-keeps-changing/8712));
  a board lying flat on the floor is the worst case;
- **non-component side facing into the room** ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626));
- power source below the board, not behind it; no metal within a few centimetres of the antenna
  ([Taoglas](https://www.taoglas.com/datasheets/UWC.20.pdf)).

### 7.1 The exercise scale, and the taped rectangle

The board's plan shows **Suite 310: 9.5 m by 8.65 m**. The **exercise scale** says how many building metres one metre
of floor stands for. Every measured distance is multiplied by it before the locator runs, so the ranging error is
multiplied too (say so).

**Worked example, scale 3** (numbers from `python hardware/layout_table.py --scale 3`):

1. Tape a **3.17 m by 2.88 m** rectangle: Suite 310 at scale 3.
2. One short (2.88 m) edge is the **door wall**. From its **start corner**, mark the **door** centre at **2.40 m**,
   **0.30 m** wide.
3. **Puck A (P1)**: **2.23 m** along the door edge from the start corner, **0.12 m** in from the edge.
4. **Puck B (P2)**: **0.90 m** along the door edge, **0.12 m** in. The pucks are **1.33 m apart**.
5. Phones anywhere inside, best **more than 1 m from the door edge**.

Another size: choose the scale so the rectangle fits your floor (scale = 9.5 divided by the longest side you can tape,
rounded up), then `python hardware/layout_table.py --scale 4` (with your scale) prints the same numbers for it.
Full-size room: scale 1, the building numbers in `hardware/LAYOUT.md`.

Set the scale on the board (the **Scale** selector in the header), or from a terminal:

```bash
curl -X POST http://localhost:1885/api/rig -H "Content-Type: application/json" -d '{"scale": 3}'
```
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:1885/api/rig -ContentType "application/json" -Body '{"scale": 3}'
```

**Works when** the board shows **"exercise scale 1 : 3"**. The board can also draw the two spots: "Show where to put
two pucks".

### 7.2 Put the pucks on the plan

1. With our app sharing on the iPhone 14, both boards appear in the **unplaced pucks tray**, each as its key
   (`name#ABCD`). Our app shows the same key on each board's row (`docs/IOS_BUILD.md` step 9.3): write it on the board's
   tape label. Not sure which is which? Unplug puck B for a moment: the row that keeps ranging in the app is puck A.
2. Pick puck A's key in the tray and click the plan at P1 (beside the door); then puck B's key at P2 (along the wall).
3. Clicking is approximate. For exact positions (building metres = tape metres × scale), use the tray name:
   ```bash
   curl -X POST http://localhost:1885/api/pucks -H "Content-Type: application/json" \
     -d '{"pucks": [{"id": "P1", "x": 9.15, "y": 6.70, "accessory": "<tray name, exactly>"}], "merge": true}'
   ```
   (`P2`: `"x": 9.15, "y": 2.70`.)

**Works when:** with the iPhone 14 in the room, the board shows one position, and the mirror image beyond the door wall
crossed out with its reason.

**Fails:** two candidates both inside the room: the pucks are not along the door wall, or the phone is too close to
the line through them. A position far from the phone: x and y swapped, the wrong start corner, or the scale not set.

**Better if you have it:** a third puck well off the line through P1 and P2 (for example against the far wall) gives
one position with no statement needed.

---

## 8. Two phones, two boards (10 min)

The stock firmware serves **one phone per board at a time**: "the QANI project in the DW3 SDK 1.1.1 is hardcoded to
only support exactly one connection"; "MULTIPLE iPhones.. no can do out of the box"
([forum 23706](https://forum.qorvo.com/t/dwm3001cdk-apple-nearby-interaction-with-multiple-iphonse-simultaneously/23706));
"only 1 session with one phone" ([forum 24451](https://forum.qorvo.com/t/does-dw3-qm33-sdks-qani-project-support-multiple-fira-sessions/24451);
see also [forum 18890](https://forum.qorvo.com/t/dwm3001cdk-firmware-to-do-nearby-interaction-twr-with-multiple-phones-via-multiplexing/18890)).
Most users report the board stops advertising while a phone is connected (one user reported it still advertises; same
threads). One iPhone, however, ranges several boards at once ([Apple](https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging),
[forum 23706](https://forum.qorvo.com/t/dwm3001cdk-apple-nearby-interaction-with-multiple-iphonse-simultaneously/23706)).

Three ways, in this order:

**(a) One victim at a time holds both boards. Always works: prove this first (5 min).**
The iPhone 14 shares (Use at most 2 boards) and gets a position; meanwhile the iPhone 15 is NOT sharing (our app
closed, or Find beacons off), or it would take one of the boards. Then the iPhone 14 presses **Stop sharing** (the
boards are released and the server forgets that phone at once), and the iPhone 15 starts sharing and gets its position.
For the demo: victim one, then victim two.

**(b) "Take turns" on both phones (never tested).**
Turn **Take turns** on in both apps. Each phone ranges one board for about 4 s, releases it, and takes the other, so
each phone gets a new range per board every 10 to 20 s. Both victims stay on the board, refreshed slowly. If your build
of the app has no "Take turns" switch, skip to (c) or stay with (a) (`docs/IOS_BUILD.md`).

**(c) Four boards as two pairs (if power allows).**
Put **two boards at each puck position** (A1 + A2 at P1, B1 + B2 at P2), powered from a two-port bank or the UNO Q's
hub. Each phone ranges its own pair continuously, on the stock firmware, with no turn-taking: the iPhone 14 uses A1 and
B1, the iPhone 15 uses A2 and B2. Tell each phone which boards to use with the app's per-board switches. Without such
switches: power A1 and B1 only, let the iPhone 14 connect (Use at most 2), then power A2 and B2 and start the iPhone 15:
it finds only the boards that are still free (if the connected boards keep advertising, it may grab A1 or B1 instead;
then use the switches or go back to (a)). Place A2 and B2 on the plan as two more pucks, **P3** right next to P1 and
**P4** right next to P2 (same click-to-place, or the `curl` of step 7.2 with ids P3 and P4). Puck B on the Android phone powers one board only: the second board at P2 needs a bank.

**When to give up:** if (b) or (c) does not show both phones within **10 minutes**, go back to (a) and demo the victims
one after the other. It is honest and it always works.

---

## 9. The demo run-through, and the pre-demo checklist

### 9.1 Run-through (2 min, rehearse it twice)

1. Board on the laptop, facing the judges: **LIVE** badge, Suite 310, P1 and P2 along the door wall, the exercise scale
   label.
2. The iPhone 14 victim inside the rectangle, app open and sharing, suite set to 310.
3. Point at the board: two rings; the mirror image lands beyond the door wall and is **crossed out, with its reason**;
   one position with the status (people, can move, battery).
4. The victim moves a step: the position follows.
5. Second victim (the iPhone 15): with (a), the first presses Stop sharing and the second starts; with (b) or (c), both
   are already on the board.
6. Close: "Each distance is a real UWB measurement between that iPhone and that board; the walls come from the building
   twin, and the mirror image was dismissed because the occupant said which suite they are in."

### 9.2 Pre-demo checklist (10 min before)

- [ ] Battery banks charged; the Android phone charged (it powers puck B and the hotspot); both iPhones above 50 %;
      the laptop on its charger.
- [ ] Android hotspot on; laptop and both iPhones joined; `SERVER/healthz` opens on both iPhones.
- [ ] Android OTG switch on (if the phone has one); puck B's LED on.
- [ ] iPhones: **Auto-Lock: Never** (Settings → Display & Brightness → Auto-Lock); **Low Power Mode off** (it shortens
      Auto-Lock); **Do Not Disturb on** (a notification must not cover the app); Bluetooth on.
- [ ] Qorvo's app closed on both iPhones (swiped away).
- [ ] Our app: suite 310 set on both phones; Use at most 2 boards.
- [ ] Server running in live mode; board shows LIVE, the scale label, P1 and P2 where the tape says.
- [ ] Pucks upright, 15 cm up, non-component side into the room.
- [ ] **The audience's view of the phones:** hold them up, or mirror one on the Mac (cable to the Mac, QuickTime Player →
      File → New Movie Recording → pick the iPhone as the camera).
- [ ] Filming the demo video: the board screen and the victims in the same shot.
- [ ] The fallback one command away in a second terminal (step 10, rungs 4 and 5).

---

## 10. Degradation ladder (most capable first)

Step down only when the trigger is met. Each rung says what to say, honestly. Never present a simulated screen as live:
the board's SIM badge must be visible whenever it is simulated.

| Rung | What runs | Step down when | What you say |
|---|---|---|---|
| **0. Three pucks (bonus)** | a third board well off the line through P1 and P2 | the third board does not range | "Three pucks: three circles meet at one point; no statement needed." |
| **1. Two pucks at the door wall** | steps 7 and 8 (a), (b) or (c) | after power-cycling a board once, its ring is still missing for 30 s | "Two pucks give two possible spots, mirror images across the door wall. The second one is in the corridor or the next suite: the board crosses it out, because a UWB link would not pass the two walls in between, or because she says she is in Suite 310, and it shows which." |
| **2. One puck: a ring** | one board | no board gives a UWB distance (the app connects over Bluetooth, but no distance after 30 s on every board) | "One puck measures one distance: she is somewhere on this ring. The walls clip the impossible parts. A second puck cuts it to two spots." |
| **3. Bluetooth only: a zone** | our app connected to a board over Bluetooth, the UWB session failing | our app does not build or install, or cannot reach the server at all | "The UWB session failed tonight, so all we have is Bluetooth signal strength: the room and a rough band of distance, not a position." (Whether the server shows a zone depends on the iOS build: `docs/IOS_BUILD.md`.) |
| **4. Qorvo's app + our simulated board** | Qorvo's app showing a live distance on the iPhone; the board on `python -m beacon.demo --story` (simulated, **SIM badge on**) | Qorvo's app shows no distance either (no board works) | "This distance on the phone is a real UWB measurement from Qorvo's own app to this board. Our app could not be installed tonight, so the command board runs our simulator: see the SIM badge." |
| **5. No boards** | `python -m beacon.demo --story`; the simulated phone page `/phone.html` on a phone (server started with `--lan`) | nothing below this | "Everything you see is simulated, with the noise model stated on the board. The hardware path is written and waiting for the boards." |

Commands for rungs 4 and 5 (macOS/Linux; on Windows use `.venv\Scripts\python`):

```bash
.venv/bin/python -m beacon.demo --story          # simulated world, plays the ~35 s walk-through; SIM badge on
.venv/bin/python -m beacon.demo --story --lan    # the same, reachable from a phone for /phone.html
```

---

## 11. Troubleshooting

| Symptom | Most likely cause | Fix |
|---|---|---|
| App (ours or Qorvo's) stuck on "Scanning for accessories" | wrong hex (another board's image), or flashed with the wrong J-Flash device | reflash `DWM3001CDK-QANI-FreeRTOS...hex` with device **`NRF52833_XXAA`**, not 52832 ([forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229), [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)) |
| Board found by Qorvo's app, not by ours | Qorvo's app still holds the board | swipe Qorvo's app away; power-cycle the board |
| A board connected to one phone is invisible to the other | expected: one phone per board at a time ([forum 23706](https://forum.qorvo.com/t/dwm3001cdk-apple-nearby-interaction-with-multiple-iphonse-simultaneously/23706)) | step 8 |
| J-Flash Lite or the Mac does not see the board at all | charge-only cable, or the cable is in J20 | data cable into **J9** (bottom, "J-Link"); try another cable |
| The bank switches itself off after a few seconds, or the board works on the Mac but not on the bank | the bank turns off at low current draw ([forum 22115](https://forum.qorvo.com/t/dwm3001cdk-board-transmits-slower-on-battery-power/22115)) | move the cable to **J9**; a bank with an always-on / low-current mode; a wall adapter |
| Puck B dark on the Android phone, or dark after some minutes | no OTG power until a device shows up, an idle timeout, an OTG switch, the cable | step 6.2 table; fallback: a battery bank |
| After flashing, the board does not advertise; the RTT log shows `fds_init() returned 0x860A` or `NRF_ERROR_STORAGE_FULL` | old data in the chip's flash storage (this exact error: [forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229)) | **full chip erase, then reflash** (`hardware/FLASH.md`). Not confirmed as the fix in that thread; a full erase clears the storage pages the message names, so it is the first thing to try |
| Distance jumps by tens of centimetres or drops out | board lying flat or on the floor; a body between phone and board; board edge-on to the phone | upright, 15 cm or more up, non-component side toward the phone ([forum 8712](https://forum.qorvo.com/t/position-of-the-tag-keeps-changing/8712), [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)) |
| Distances fine up close, nothing beyond a few metres | walls, bodies, metal; users report about 10 m outdoors in free space ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)); Apple: best "within 9 meters" ([Apple](https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session)) | keep the demo area small (scale) and in line of sight |
| Ranging stops when the phone locks or you switch apps | Apple suspends the session in the background ([Apple](https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session)) | keep our app in the foreground; Auto-Lock Never |
| No direction arrow on either iPhone | expected with this board ([Apple forum 822522](https://developer.apple.com/forums/thread/822522)) | nothing; the demo uses distances only |
| The app shows distances but the board shows nothing | the app cannot reach the server | `/healthz` in Safari (step 4.1); same hotspot; firewall |
| Two candidates both inside the room | pucks not along the door wall, or the phone close to the line through them | step 7; move the phone deeper into the room |
| The mirror is not crossed out although it is in the corridor | the phone's suite is not set in the app | step 5.2 item 3 |
| The board shows the SIM badge | server started without `--source live` | restart with `--source live --lan` |
| Ring is the wrong size | exercise scale wrong or not set | step 7.1 |
| Position mirrored or rotated | x and y swapped, or the wrong start corner | step 7.1; `hardware/LAYOUT.md` |
| The UNO Q host shows nothing | see `hardware/UNO_Q.md`; the host is optional | carry on without it |

---

## Time budget

| Step | Minutes |
|---|---|
| 0 table, downloads, installs | 10 (plus the Qorvo email) |
| 1 check the boards | 5 + 2 each |
| 2 flash the boards that need it | 5 + 2 each |
| 3 prove one board with Qorvo's app | 5 |
| 4 networking | 10 |
| 5 our app and the first ring | 30 to 45 |
| 6 power the pucks, soak test | 20 |
| 7 place the pucks | 10 |
| 8 two phones | 10 |
| 9 rehearse twice | 10 |
| slack for one thing going wrong | 10 |
| **total** | **about 2 hours** |
