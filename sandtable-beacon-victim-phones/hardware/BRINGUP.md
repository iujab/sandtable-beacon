# Sandtable Beacon: bring-up tonight, from five boards in a bag to a phone on the board

> **Written from documentation and forum reports; not executed on a board.** Nothing in this project has touched a
> DWM3001CDK, a J-Link or an iPhone yet. Every hardware fact below carries its source URL; the full notes are
> `docs/research/puck_hardware.md` and `docs/research/ios_ni.md`. Where a fact is **not confirmed**, the step tells you
> what to do for either answer.

For one tired person. Follow the numbers. Every step says how long it takes, what success looks like, and what to do
when it fails. **Total: about 90 minutes** the first time (the iOS build is the long part), plus however long Qorvo's
download email takes, so **start step 0's download first**.

How it works, in one breath: each board runs Qorvo's stock, unmodified Nearby Interaction firmware ("QANI"). Our iPhone
app connects to up to three boards over Bluetooth, runs one Apple Nearby Interaction session per board, reads each
distance, and posts it over Wi-Fi to the Beacon server (`POST /api/ingest`, port 1885). The server turns the distances,
the measured puck positions and the building twin's walls into a ring, two candidates or a fix on the command board.
**A puck needs only power.** The UNO Q is optional (`hardware/UNO_Q.md`).

Commands are given for macOS (the Mac), Windows PowerShell and Linux. `<project>` means the folder that contains this
`hardware/` folder.

---

## 0. What you need on the table, and the downloads only you can do (10 min, plus the email wait)

**Do the Qorvo download request FIRST** (0.1): it emails you a link and may take a while.

On the table:

| Thing | How many | Notes |
|---|---|---|
| Qorvo DWM3001CDK boards | 5 | label them **1 to 5** with tape now |
| micro-USB **data** cables | 1 for flashing, plus 1 per powered puck | charge-only cables look identical and are the classic time sink ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)). Test each cable once in step 1 and mark the good ones |
| power per puck | 3 for the demo (up to 5) | one USB battery bank per puck (recommended), or a wall adapter, or a laptop USB port. See `hardware/PUCK.md` |
| the Mac with Xcode | 1 | builds the iOS app and flashes the boards |
| iPhone 14 and iPhone 15 | 2 | charged, with their cables to the Mac |
| a laptop for the server | 1 | can be the Mac itself |
| stands for the pucks | 3 | cups, boxes or books, **15 cm or taller** (step 5 says why) |
| masking tape, marker, tape measure | | for labels, the demo rectangle and puck positions |

The two boards' micro-USB ports ([Zephyr board page](https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html), [forum 13430](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430)):

- **J9**: the **bottom** micro-USB, labelled **"J-Link"**. Flashing, debug log (RTT), power.
- **J20**: the **upper** micro-USB, nearer the module. The nRF52833's own USB. With the stock firmware it only gives
  power ("QANI has no USB function", [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).
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
5. Copy the hex into `<project>/hardware/firmware/` on the Mac (the folder is git-ignored? No: do **not** commit
   Qorvo's file; its licence was not found. Keep it out of the repository, for example in `~/Downloads`).

**If the download never arrives:** step 1 may show your boards already carry Nearby Interaction firmware. If they do
not, go to the degradation ladder (step 8): rung 6.

### 0.2 Install SEGGER J-Link software (5 min, free, no account)

<https://www.segger.com/downloads/jlink/> → "J-Link Software and Documentation Pack" → your system (macOS: the
Apple Silicon or Universal installer; Windows: the Windows installer; the UNO Q: Linux ARM 64-bit DEB). Accept the terms.
It is "publicly available for download, at no charge" ([SEGGER](https://www.segger.com/news/seggers-complete-j-link-software-now-available-for-linux-on-arm/)).
It contains J-Flash Lite (the flashing window), J-Link Commander (`JLinkExe`) and the RTT tools.

### 0.3 Install Qorvo's free iOS app on the iPhone 14 (2 min)

App Store: **"Qorvo Nearby Interaction"** (<https://apps.apple.com/app/id1615369084>). It is Qorvo's own evaluation app
for these kits ([ios_ni.md section 2](../docs/research/ios_ni.md)).

---

## 1. Five-minute check before flashing anything (5 min for the first board, 2 min for each other)

Your boards may already carry Nearby Interaction firmware: Qorvo said in 2022 that the preloaded sample "is to be used
with an iPhone equipped with a U1 UWB chip" ([forum 12439](https://forum.qorvo.com/t/dwm3001cdk-what-should-i-do-to-start-ranging-between-the-two-boards/12439)).
Not confirmed for your boards: what they ship with today. So check before you flash.

1. Plug board 1 into a battery bank or the Mac with a micro-USB cable, into **J20** (the upper port). A power LED should
   light. (What the other LEDs mean under the stock firmware is not documented in the research; ignore them.)
2. On the iPhone 14: Bluetooth on. Open **Qorvo Nearby Interaction**. Allow Bluetooth and Nearby Interaction when asked.
3. Wait up to 30 s for a board to appear in the list. Tap it.

| What you see | It means | Do |
|---|---|---|
| a distance in metres that changes when you move the phone | this board already runs Nearby Interaction firmware | write the name the app shows on board 1's tape label. **Skip step 2 for this board.** Do the same check on boards 2 to 5 |
| a board appears, connects, but no distance after 30 s | old or incompatible firmware, or an Apple/firmware mismatch | flash it (step 2) |
| nothing appears ("Scanning for accessories") | no NI firmware, or the board has no power | first try another cable and the other USB port (J9). Still nothing: flash it (step 2) |
| the board's LED does not light at all | no power: charge-only cable, dead bank | another cable, another power source |

**Close Qorvo's app completely when you are done** (swipe it away in the app switcher): a board that is connected to one
app is not free for another (see the troubleshooting table).

**Not confirmed: the board's Bluetooth name.** An nRF52840 build advertised as "nRF52840DK"
([forum 25749](https://forum.qorvo.com/t/nrf52840dk-dwm3000evb-qani-hangs-after-sent-shareable-configuration-data/25749));
the DWM3001CDK's name is unknown. Whatever it is, write it down: you will match it on the board's tray in step 5.

---

## 2. Flash the stock firmware (5 min for the first board, 2 min for each other)

Only for boards that failed step 1. **Full click path and command-line version: `hardware/FLASH.md`.** In short:

1. Micro-USB **data** cable from the Mac into **J9** (the bottom port, labelled "J-Link").
2. Open **J-Flash Lite** (installed in 0.2). Device: **`NRF52833_XXAA`** (type "52833" in the search box). Interface:
   **SWD**. Speed: **4000** kHz. OK.
3. Data file: the hex from 0.1. **Erase Chip**, then **Program Device**.
4. Unplug, plug back in (this restarts the board), and repeat step 1's check.

**Pick nRF52833, not nRF52832**: "The cdk is 52833, picking the wrong device will mess things up"
([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).

**Works when:** step 1's check now shows a distance. **Fails:** see the troubleshooting table (step 9) and
`hardware/FLASH.md` "Recover a board".

---

## 3. Prove one board and one iPhone with Qorvo's app (5 min)

Before you touch our own app, prove the radio link with Qorvo's.

1. Stand board 1 **upright** on a stand 15 cm or more tall (step 5 explains why), powered.
2. iPhone 14, Qorvo's app, connect to board 1. Hold the phone upright, back camera toward the board, about 1 m away.
3. Lay the tape measure on the floor from the board. Hold the phone at **1.00 m**, then **2.00 m**, then **3.00 m**;
   write down what the app shows each time. Walk away to 5 m and back: the number should follow you.
4. Repeat once with the iPhone 15 (close the app on the iPhone 14 first).

**Works when:** the distance follows you and stays within a few tens of centimetres of the tape. For scale: there is no
published accuracy for an iPhone against this board; a study of phones against DW3000 boards found errors of about
13 to 15 cm (1 sigma) in ordinary rooms ([arXiv 2303.11220](https://arxiv.org/html/2303.11220v1)), and forum users report
40 to 50 cm errors between boards in some setups ([forum 20404](https://forum.qorvo.com/t/how-to-reduce-distance-d-cm-error/20404)).
Write your three numbers down: they are the honest accuracy you can quote tonight.

**Fails:**
- Distance jumps around: is the board lying flat? Stand it upright, non-component side toward the phone ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)). Is your body between phone and board?
- Distance appears on the 14 but not the 15 (or the reverse): note it, carry on with the phone that works, and tell the iOS builder.
- No direction arrow: **expected.** Direction is very likely nil on both phones without Camera Assistance; developers report `supportsDirectionMeasurement == false` on the iPhone 14 family and on an iPhone 15 with this exact board ([Apple forum 822522](https://developer.apple.com/forums/thread/822522), [720292](https://developer.apple.com/forums/thread/720292)). Our demo does not need it.

---

## 4. Our iOS app, the Beacon server, and the first ring on the board (30 to 45 min)

### 4.1 Build and install our app (20 to 30 min the first time)

Follow **`docs/IOS_BUILD.md`** (Xcode, signing with your Apple ID, install on the iPhone 14 first). Our app is
uncompiled source until you build it. Come back here when the app opens on the phone.

### 4.2 Start the Beacon server in live mode (5 min)

Pick ONE place for the server.

**A. On the laptop at the venue (recommended: no dependency on the venue's internet).** The laptop and both iPhones must
be on the **same Wi-Fi**. Venue Wi-Fi often blocks phone-to-laptop traffic ("client isolation"); if the check below
fails, make a hotspot with the Android phone and put the laptop and both iPhones on it.

macOS or Linux:

```bash
cd <project>
python3 -m venv .venv                                    # once
.venv/bin/python -m pip install -r requirements.txt      # once, needs internet
.venv/bin/python -m beacon.demo --source live --lan
```

Windows PowerShell:

```powershell
cd <project>
py -m venv .venv                                         # once
.venv\Scripts\python -m pip install -r requirements.txt  # once, needs internet
.venv\Scripts\python -m beacon.demo --source live --lan
```

The first time, macOS or Windows asks whether Python may accept incoming connections: **allow** (on Windows: private
networks). Find the laptop's address: macOS `ipconfig getifaddr en0`; Windows `ipconfig` (the "IPv4 Address" of the
Wi-Fi adapter); Linux `hostname -I`. Call it `LAPTOP_IP`.

**B. On the bench machine over the tailnet.** If the server is already running at the bench, it must be in live mode:
`.venv/bin/python -m beacon.demo --source live --tailnet` (on the bench machine; `tailscale ip -4` there prints its
address). The iPhones then need the **Tailscale** app installed and signed in to the same tailnet, and mobile data or
Wi-Fi with internet. Use A whenever you can: B depends on the venue's internet.

**Check from the iPhone before you open our app:** in Safari open `http://LAPTOP_IP:1885/healthz`.

- **Works when** it shows `{"ok": true, ... "source": "live" ...}`.
- Safari cannot connect: firewall prompt refused (macOS: System Settings → Network → Firewall → allow Python;
  Windows: allow Python on private networks), or client isolation (use the hotspot), or a typo in the address.
- `"source": "sim"`: you started it without `--source live`. Ctrl-C and start again.

Open the board on the laptop: <http://localhost:1885/>. It shows a **LIVE** badge (no SIM badge) and an empty plan.

### 4.3 Point our app at the server and see the first ring (5 min)

1. **Close Qorvo's app** on this phone (swipe it away): a board connected to it is invisible to ours.
2. In our app's settings: **Server address** = `LAPTOP_IP:1885` (the app adds `http://`); **Max pucks** = **3** on the
   iPhone 14. (Exact screens: `docs/IOS_BUILD.md`.)
3. Power board 1 only. Start sharing in our app; allow Bluetooth and Nearby Interaction.
4. **Keep the app open, in the foreground, screen on.** If the app goes to the background, Apple suspends the Nearby
   Interaction session ([Apple](https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session)).

**Works when:** within about 30 s the app shows a distance to board 1, and the board on the laptop shows board 1 in the
**unplaced pucks tray** (its Bluetooth name followed by `#` and four hex digits). Pick it in the tray, then click the plan
roughly where it stands: a **ring** appears around it. That ring is the phone. Step 5 places the pucks properly.

**Fails:**
- App shows a distance, board shows nothing: the app cannot reach the server. Re-check the `/healthz` page in Safari,
  and the server address in the app.
- App never shows a distance, but Qorvo's app did in step 3: the iOS app's problem; see `docs/IOS_BUILD.md`
  troubleshooting. Meanwhile use the ladder (step 8): rung 5 keeps the demo honest.
- Tray stays empty while the app shows distances: the server is not in live mode (step 4.2's check).

---

## 5. Place the pucks (10 min)

**Recommended live-demo layout: three separately powered pucks, about 2 to 3 m apart, NOT in a line** (a triangle
around the area where the "trapped" phone will be).

<!-- LAYOUT_NUMBERS -->

Why three and why not in a line: one puck gives a ring, two give two mirror-image candidates, three not in a line give
one point; with pucks nearly in a line the error across that line blows up
([puck_hardware.md section 5c](../docs/research/puck_hardware.md)).

Each puck:
- **upright** (antenna end up), on a stand **at least 15 cm off the floor**. A UWB vendor on Qorvo's forum: "15 cm
  separation from the ground plane ... is required to mostly negate this behavior"
  ([forum 8712](https://forum.qorvo.com/t/position-of-the-tag-keeps-changing/8712)). A board lying flat on the floor is
  the worst case: the floor reflection partly cancels the direct path (long readings or dropouts).
- **non-component side toward where the phone will be** ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).
- battery bank **below** the board, not behind it; no metal within a few centimetres of the antenna
  ([Taoglas](https://www.taoglas.com/datasheets/UWC.20.pdf)).
- its own power (one bank each).

### 5.1 Tape the demo area and choose the scale

The board's plan shows **Suite 310: 9.5 m by 8.65 m** (from `fixtures/building/twin.json`). Your demo area is
probably smaller. The **exercise scale** says how many building metres one metre of floor stands for.

**Worked example:** tape a **3.2 m by 2.9 m** rectangle on the floor to stand for the 9.5 m by 8.65 m suite.
9.5 / 3.2 = 2.97 and 8.65 / 2.9 = 2.98, so the scale is **3**. Every measured distance is multiplied by 3 before the
locator runs: a phone 1.0 m from a puck draws a ring of radius 3.0 m on the plan. The board shows
**"exercise scale 1 : 3"**. Be honest about the side effect: the ranging error is multiplied by 3 too.

Set it on the board (the **Scale** selector in the header), or from a terminal:

```bash
curl -X POST http://localhost:1885/api/rig -H "Content-Type: application/json" -d '{"scale": 3}'
```

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:1885/api/rig -ContentType "application/json" -Body '{"scale": 3}'
```

Full-size room instead? Leave the scale at 1.

### 5.2 Measure the pucks and put them on the plan

1. Pick the taped corner that stands for the suite's corner at plan position (0, 0); the long taped side (3.2 m) stands
   for the suite's 9.5 m side (x), the short one for the 8.65 m side (y).
2. For each puck, measure **x** (along the long side) and **y** (along the short side) from that corner, in metres.
   Write them on the puck's tape label.
3. Multiply by the scale to get plan coordinates. Example at scale 3: a puck at x 0.5 m, y 2.4 m on the floor sits at
   x 1.5 m, y 7.2 m on the plan.
4. **Power ONE puck at a time** and keep our app sharing. The newly powered puck appears in the **unplaced pucks tray**.
   Pick it, click the plan at its plan coordinates. Then power the next one. (This way you never have to guess which
   tray entry is which board.)

Clicking is approximate. For exact positions, after the puck is in the tray, use its tray name in:

```bash
curl -X POST http://localhost:1885/api/pucks -H "Content-Type: application/json" \
  -d '{"pucks": [{"id": "P1", "x": 1.5, "y": 7.2, "accessory": "<tray name, exactly>"}], "merge": true}'
```

**Works when:** all three pucks are on the plan, and with the phone in the middle of the triangle the board draws one
point (a fix), not a ring.

**Fails:** a fix that sits far from the phone: x and y swapped, or the wrong corner as (0, 0), or the scale not set.
Re-measure one puck, compare with the plan.

---

## 6. Two phones sharing five boards (5 min)

The stock firmware probably serves **one phone per board** (not confirmed: "How many iPhones one QANI board serves at
once is not confirmed ... assume 1 phone per board", [ios_ni.md section B](../docs/research/ios_ni.md)). One iPhone can
run several accessory sessions at once ([Apple](https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging)).
So split the boards:

- **iPhone 14 (the "trapped" phone): Max pucks = 3** → boards 1 to 3, the triangle of step 5.
- **iPhone 15 (the second phone): Max pucks = 2** → boards 4 and 5, placed and measured like step 5.2.

Start sharing on the iPhone 14 **first** and wait until it shows three boards; then start the iPhone 15, which finds only
the boards that are still free. (Not confirmed: whether a connected board stops advertising. If the iPhone 15 grabs one of
boards 1 to 3 anyway, stop sharing on both, power-cycle that board, and start the 14 first again.)

If it turns out one board CAN serve two phones (both phones show a distance to the same board), you can give both phones
Max pucks = 5. Write down what you saw: it settles an open question.

---

## 7. The demo run-through, and the pre-demo checklist

### 7.1 Run-through (2 min, rehearse it twice)

1. Board on the laptop, facing the judges: LIVE badge, Suite 310, three pucks placed, the exercise scale label.
2. The "trapped" person (iPhone 14, our app open and sharing) stands or sits inside the triangle.
3. Point at the board: the phone's position in the suite, with its status (people, can move, battery).
4. The person moves a step: the point follows.
5. Power off one puck (pull its cable): the fix becomes **two candidates**, and the twin's wall deletes one of them where
   it can. Power it back: back to a fix.
6. Close: "Each distance is a real UWB measurement between this iPhone and that board; the walls come from the building
   twin."

### 7.2 Pre-demo checklist (10 min before)

- [ ] Every battery bank charged; both iPhones above 50 %; the laptop on its charger.
- [ ] iPhones: **Auto-Lock: Never** (Settings → Display & Brightness → Auto-Lock); **Low Power Mode off** (it forces
      a short Auto-Lock); **Do Not Disturb on** (a notification must not cover the app); Bluetooth on; on the right Wi-Fi.
- [ ] Qorvo's app closed on both phones (swiped away).
- [ ] Server running in live mode; `/healthz` opens from the iPhone's Safari.
- [ ] Pucks upright, 15 cm up, non-component side inward, positions on the plan checked against the tape.
- [ ] Scale set and the "exercise scale" label visible (or scale 1).
- [ ] **The audience's view of the phone:** the judges must see the phone too. Hold it up, or mirror it on the Mac
      (cable to the Mac, QuickTime Player → File → New Movie Recording → pick the iPhone as the camera).
- [ ] Filming the demo video: one camera on the board screen and the person in the triangle in the same shot.
- [ ] Fallback ready in a second terminal (step 8, rung 5 or 6), one command away.

---

## 8. Degradation ladder (most capable first)

Step down only when the trigger is met. Each rung says what to say, honestly. Never present a simulated screen as live:
the board's SIM badge must be visible whenever it is simulated.

| Rung | What runs | Step down when | What you say |
|---|---|---|---|
| **1. Three pucks, UWB fix** | step 7 as written | after power-cycling a puck once, its ring is still missing for 30 s, or the app still shows fewer than 3 boards | "Three pucks each measure the distance to her phone over ultra-wideband; three circles and the building's walls agree on one spot." |
| **2. Two pucks: two candidates** | the same, with two boards | only one board ranges | "With two pucks there are two mirror-image spots. The twin deletes the one inside a wall or outside the building; when both are possible, we show both. We never draw a point the measurements do not support." |
| **3. One puck: a ring** | one board | no board gives a UWB distance (the app connects over Bluetooth, but no distance after 30 s on every board) | "One puck measures one distance: she is somewhere on this ring. The walls clip the impossible parts. A second puck would cut it to two spots." |
| **4. Bluetooth only: a zone** | our app connected to a board over Bluetooth, UWB session failing | our app does not build or install, or it cannot reach the server at all | "The UWB session failed tonight, so all we have is Bluetooth signal strength: the room and a rough band of distance, not a position." (Our app reports the Bluetooth signal strength of each board, `kind: sos`; whether the server turns it into a zone depends on the iOS build, see `docs/IOS_BUILD.md`.) |
| **5. Qorvo's app + our simulated board** | Qorvo's app showing a live distance on the iPhone; the board on `python -m beacon.demo --story` (simulated, **SIM badge on**) | Qorvo's app shows no distance either (no board works) | "This distance on the phone is a real UWB measurement from Qorvo's own app to this board. Our app could not be installed tonight, so the command board is running our simulator: see the SIM badge." |
| **6. No boards** | `python -m beacon.demo --story`; the simulated phone page `http://localhost:1885/phone.html` on a phone (same Wi-Fi, server started with `--lan`) | nothing below this | "Everything you see is simulated, with the noise model stated on the board. The hardware path is written and waiting for the boards." |

Commands for rungs 5 and 6 (macOS/Linux; on Windows use `.venv\Scripts\python`):

```bash
.venv/bin/python -m beacon.demo --story          # simulated world, plays the ~35 s walk-through; SIM badge on
.venv/bin/python -m beacon.demo --story --lan    # the same, reachable from a phone for phone.html
```

---

## 9. Troubleshooting

| Symptom | Most likely cause | Fix |
|---|---|---|
| App (ours or Qorvo's) stuck on "Scanning for accessories" | wrong hex (another board's image), or flashed with the wrong J-Flash device | reflash `DWM3001CDK-QANI-FreeRTOS...hex` with device **`NRF52833_XXAA`**, not 52832 ([forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229), [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)) |
| Board found by Qorvo's app, not by ours | Qorvo's app still holds the board | swipe Qorvo's app away; power-cycle the board |
| J-Flash Lite or the Mac does not see the board at all | charge-only cable, or the cable is in J20 | data cable into **J9** (bottom, "J-Link"); try another cable |
| Board works on the Mac but not on a battery bank; or the bank switches itself off after a few seconds | the bank turns off at low current draw ("Some large capacity power banks turn off their supply after a short period of time when detecting low current draw", [forum 22115](https://forum.qorvo.com/t/dwm3001cdk-board-transmits-slower-on-battery-power/22115)) | move the cable to **J9** (the on-board J-Link adds about 10 to 15 mA of load, [forum 13973](https://forum.qorvo.com/t/does-any-one-know-where-to-find-the-offical-power-requirement-of-the-dwm3001cdk/13973)); or use a bank with an always-on / low-current mode; or a wall adapter |
| After flashing, the board does not advertise; the RTT log shows `fds_init() returned 0x860A` or `NRF_ERROR_STORAGE_FULL` | old data in the chip's flash storage (this exact error was seen in [forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229)) | **full chip erase, then reflash** (`hardware/FLASH.md`). Not confirmed as the fix in that thread; a full erase clears the storage pages, so it is the first thing to try |
| A board connected to one phone is invisible to the other | expected: the stock firmware probably serves one phone per board (not confirmed) | step 6: Max pucks 3 and 2, start the 14 first |
| Distance jumps by tens of centimetres or drops out | board lying flat, or on the floor; a body between phone and board; board edge-on to the phone | stand it upright, 15 cm or more up, non-component side toward the phone ([forum 8712](https://forum.qorvo.com/t/position-of-the-tag-keeps-changing/8712), [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)) |
| Distances fine up close, nothing beyond a few metres | walls, bodies, metal; users report about 10 m outdoors in free space ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)); Apple says best "within 9 meters" ([Apple](https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session)) | keep the demo area small (scale) and in line of sight |
| Ranging stops when the phone locks or you switch apps | Apple suspends the session in the background ([Apple](https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session)) | keep our app in the foreground; Auto-Lock Never |
| No direction / arrow on either iPhone | expected on the iPhone 14 and 15 with this board ([Apple forum 822522](https://developer.apple.com/forums/thread/822522)) | nothing; the demo uses distances only |
| The app shows distances but the board shows nothing | app cannot reach the server | `/healthz` in Safari (step 4.2); same Wi-Fi; firewall; hotspot |
| The board shows the SIM badge | server started without `--source live` | restart with `--source live --lan` |
| Ring is the wrong size | exercise scale wrong or not set | step 5.1 |
| Fix is mirrored or rotated | x and y swapped, or the wrong corner taken as (0, 0) | step 5.2 |
| The UNO Q host shows nothing | see `hardware/UNO_Q.md`; the host is optional | carry on without it |

---

## Time budget

| Step | Minutes |
|---|---|
| 0 table, downloads, installs | 10 (plus the Qorvo email) |
| 1 check five boards | 13 |
| 2 flash the boards that need it | 5 + 2 each |
| 3 prove one board with Qorvo's app | 5 |
| 4 our app, the server, the first ring | 30 to 45 |
| 5 place three pucks | 10 |
| 6 the second phone | 5 |
| 7 rehearse twice | 10 |
| slack for one thing going wrong | 10 |
| **total** | **about 90 to 110** |
