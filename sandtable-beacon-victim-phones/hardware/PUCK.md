# The Beacon puck: what it is tonight, and what a real one would need

> **Written from documentation and forum reports; not executed on a board.** Nothing here has been built, powered or
> thrown. Sources are cited inline; the full notes are `docs/research/puck_hardware.md` and `docs/research/ios_ni.md`.
> Lines marked "derived" are calculations, not sourced claims.

![Side views: tonight's table puck and a throwable concept](puck_sketch.svg)

## 1. Tonight's puck

One **Qorvo DWM3001CDK** (DWM3001C module: nRF52833 with Bluetooth 5.2, DW3110 UWB chip, one UWB antenna and one
Bluetooth antenna on the module; on-board J-Link) running Qorvo's **stock, unmodified** Nearby Interaction firmware,
**plus power, plus a stand.** That is all.
([product brief](https://www.firaconsortium.org/sites/default/files/2024-08/DWM3001CDK%20Product%20Brief.pdf))

- The iPhone app connects to the board over Bluetooth, runs an Apple Nearby Interaction session with it, reads the
  distance and posts it to the Beacon server. The board itself sends nothing to the server; it needs no host.
- The board writes its debug log over SEGGER RTT on the J-Link port J9, not over any serial port
  ([forum 13613](https://forum.qorvo.com/t/dwm3001cdk-nearby-interaction-trying-to-get-ranging-via-usb-uart/13613)). A
  host can read that log (experimental, `python -m beacon.host --rtt`), but the format of its lines is not confirmed.
- The DW3110 has a single antenna: no angle of arrival on the puck (DW3000 datasheet: "DW3110 ... PDoA support: No",
  [distributor mirror](https://atta.szlcsc.com/upload/public/pdf/source/20220621/2EAA42E8F3E2D83DDB8686FF8DCFAD44.pdf)).
- It cannot range another board on the stock firmware ("two boards do not range each other on it without code changes",
  [forum 12439](https://forum.qorvo.com/t/dwm3001cdk-what-should-i-do-to-start-ranging-between-the-two-boards/12439)),
  so pucks cannot measure their own positions tonight: you measure them with a tape (BRINGUP step 5).

## 2. Power options, compared honestly

Current draw: no official whole-board figure for idle, Bluetooth advertising or ranging was found. The UWB chip alone
peaks at about 48 to 78 mA at 3.0 V during two-way ranging, with 18 mA between frames (DW3000 datasheet, same mirror);
the on-board J-Link chip draws "something around 10-15mA (if recall it correctly)"
([forum 13973](https://forum.qorvo.com/t/does-any-one-know-where-to-find-the-offical-power-requirement-of-the-dwm3001cdk/13973)).
The module takes 2.5 to 3.6 V (forum paraphrase of the datasheet, same thread).

| Option | Verdict | Why |
|---|---|---|
| **USB battery bank per puck** | **Recommended** | Each puck independent, no wiring, any bank fits. Caveat: "Some large capacity power banks turn off their supply after a short period of time when detecting low current draw" ([forum 22115](https://forum.qorvo.com/t/dwm3001cdk-board-transmits-slower-on-battery-power/22115)). Start on J20 (community advice for standalone NI use, [forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)); if the bank switches off, move the cable to J9 (the J-Link chip adds its 10 to 15 mA of load), use a bank with an always-on or low-current mode, or a wall adapter. Derived: even at a pessimistic 150 mA, a 5,000 mAh bank lasts many hours. |
| **Wall adapter or laptop USB port** | Fine for a table demo | Never switches off. Ties the puck to a cable. |
| **Arduino UNO Q as host**, through a USB-C dongle with power pass-through | Optional, for one puck | The UNO Q's single USB-C port can act as a USB host, but the reported-working setup is a USB-C hub/dongle with PD input; host mode does not come up when the UNO Q is powered from VIN; "The UNO Q does not supply power to the USB-C dongle"; and the manual says any PD dongle works "except Apple ones" ([forum 1442207](https://forum.arduino.cc/t/uno-q-usb-port-not-working-if-board-powered-from-5v/1442207), [datasheet](https://docs.arduino.cc/resources/datasheets/ABX00162-ABX00173-datasheet.pdf), [manual mirror](https://ja-bots.com/wp-content/uploads/2026/03/UNO-Q-User-Manual-_-Arduino-Documentation.pdf)). What it adds: the board shows the puck as hosted (heartbeat), optionally the puck's RTT log and a cold Bluetooth device count from the UNO Q's own radio. It needs a 5 V / 3 A PD supply ("brief dips can cause resets or link drops", datasheet). Setup: `hardware/UNO_Q.md`. |
| **Android phone (USB OTG)** | Emergency power only | A phone with USB host (OTG) support puts 5 V on the cable, usually budgeted around 500 mA, plenty for one board; you need a USB-C (phone end) to micro-B cable or an OTG adapter ([arachnoid.com](https://arachnoid.com/usb_power/index.html)). **Not worth it as a host:** the stock firmware prints nothing on any serial port (its log is RTT, which needs the J-Link protocol); no evidence was found of anyone reading RTT or the J-Link serial port from Chrome on Android; and wired Web Serial in Chrome for Android is tied to Android 17 and device configuration ([Chrome 148 notes](https://developer.chrome.com/release-notes/148), [Intent to Ship](https://groups.google.com/a/chromium.org/g/blink-dev/c/HBJ-uYFvkpM)). Better use of the Android phone tonight: a Wi-Fi hotspot for the laptop and the iPhones. |
| **LiPo on the battery connector** | Not tonight | The Quick Start Guide lists a JST SR battery connector at J12 and a loose-wire option at J1, with "no battery charger" (search snippet; the PDF did not load), which conflicts with the Zephyr page's "battery connector and charging circuit" ([Quick Start Guide](https://www.mouser.com/datasheet/2/412/Qovro_7_7_2022_DWM3001CDK_Quick_Start_Guide-2998998.pdf), [Zephyr](https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html)). **Not confirmed:** the voltage the connector accepts, its polarity, whether a charger is fitted. Do not plug a cell in without the schematic in hand. It is the right answer for a real puck (no USB cable, no bank switching off). |

## 3. Mounting and orientation

From `docs/research/puck_hardware.md` section 6:

- **Upright** (antenna end up), **at least 15 cm above the floor**: "15 cm separation from the ground plane (be it actual
  ground or metal lath such as suspended ceiling grids) is required to mostly negate this behavior"
  ([forum 8712](https://forum.qorvo.com/t/position-of-the-tag-keeps-changing/8712)). A board lying flat on the floor is
  the worst case: the floor reflection arrives within millimetres of the direct path and partly cancels it (long readings
  or dropouts; derived in the research, not measured).
- **Non-component side toward the phone** ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).
- **No metal near the antenna**: "at least 3mm from metal components or surfaces, and ideally 5mm"; antennas "will not
  work within fully enclosed metal enclosures" ([Taoglas](https://www.taoglas.com/datasheets/UWC.20.pdf)). Keep the
  battery below the board, not behind it ([mwrf](https://www.mwrf.com/21128361)).
- Rotation about the antenna axis can shift a range by "5 to 10 cm" ([forum 8712](https://forum.qorvo.com/t/position-of-the-tag-keeps-changing/8712));
  antenna orientation can account for "up to 25 cm" ([EWSN 2020](https://www.ewsn.org/file-repository/ewsn2020/156_161_schroder.pdf)).
  Do not turn the pucks after you measured them.

**Not confirmed:** the DWM3001C antenna's radiation pattern and its nulls (a figure in the module datasheet that could not
be loaded); the measured error of a board lying flat on a floor; any drop or shock rating for the kit; the board's
dimensions.

## 4. A throwable enclosure (concept, not built)

The sketch above, panel B. The design follows from section 3:

- a **heavy, rounded base** with the battery as ballast, so the puck rights itself after a throw (no Qorvo guidance on
  ruggedising or throwing the board was found);
- a **short plastic mast** that holds the antenna 15 cm or more above the floor whichever way the puck lands;
- an **RF-transparent radome** (plain plastic, no metal-flake paint) around the upright board, 3 to 5 mm clear of
  any metal;
- the battery far from the antenna; padding around the board.

## 5. What a real fire puck would need

From `docs/research/puck_hardware.md`, "What a real fire puck would need":

- **Direction on the puck itself:** a dual-port DW3120/DW3220-class radio (phase difference of arrival, about 5 degree
  sigma per the DW3000 datasheet) or a three-antenna array, instead of range differences between two single-antenna
  boards (section 6 below).
- **The antenna off the floor whatever way it lands:** self-righting body, mast, radome (section 4).
- **Heat, water and shock:** high-temperature cells and enclosure, potting, a drop rating; the DW3000 is rated -40 to
  +85 C ambient.
- **Firmware that streams** ranges, link quality and non-line-of-sight indicators over a radio link to command, not a
  debug log; puck-to-puck ranging so thrown pucks can survey their own positions.
- **Per-unit antenna-delay calibration** ("a one-nanosecond timestamping error corresponds to 30 cm in ranging error",
  [arXiv 2210.05888](https://arxiv.org/pdf/2210.05888)), wall and body bias models, and outlier rejection for the
  metre-level phone outliers seen in studies ([arXiv 2303.11220](https://arxiv.org/html/2303.11220v1)).
- **Regulatory approval** for the radios, and a plan for phones that are locked, in pockets or face down (`docs/research/ios_ni.md`).

## 6. Bill of materials

Prices are what the research found on 2026-11-14; "unknown" means nobody looked one up. Nothing needs buying tonight.

| Item | Qty tonight | Unit price | Source |
|---|---|---|---|
| Qorvo DWM3001CDK dev kit | 5 (owned) | USD 29.50 (DigiKey, 1,100 in stock, factory lead time 37 weeks) | [DigiKey](https://www.digikey.com/en/products/detail/qorvo/DWM3001CDK/24367348) |
| USB battery bank | 3 to 5 | unknown | |
| micro-USB data cable | 4 to 6 | unknown | |
| stand (cup, box), tape | 3 | unknown | |
| Arduino UNO Q (optional host) | 1 (owned) | unknown | |
| USB-C hub/dongle with PD pass-through (not Apple) and a 5 V / 3 A PD supply | 1 (for the UNO Q) | unknown | |
| For a custom puck board later: Qorvo DWM3001C module (DWM3001CTR13) | | USD 59.37 (qty 1, cut tape) | [DigiKey](https://www.digikey.com/en/products/detail/qorvo/DWM3001CTR13/24367353) |
| Alternative with Apple NI support (NXP SR150 route): Murata Type 2BP EVK | | USD 179.99 | [DigiKey](https://www.digikey.com/en/products/detail/murata-electronics/LBUA0VG2BP-EVK-P/16680947) |
| LiPo cell, radome, mast, ballast (throwable puck) | | unknown | |

## 7. Two boards on one puck or two pucks?

The question: should a thrown puck carry two boards a few centimetres apart (so the range difference gives a bearing),
or should the five boards be separate pucks a few metres apart?

<!-- LAYOUT_NUMBERS -->

### 7.1 Two boards on one rigid puck: a bearing from the range difference

Model (standard geometry, derived in `docs/research/puck_hardware.md` section 5b): boards A and B at baseline b, the
phone far away at angle theta from the baseline. Range difference d_A - d_B ≈ b cos(theta), so
**sigma_theta ≈ sqrt(2) · sigma_r / (b · |sin theta|)**. A difference between the two links' fixed biases does not
average away: angle bias ≈ delta_bias / (b · |sin theta|).

Broadside (theta = 90 degrees); **double these at 30 or 150 degrees**:

| sigma_r per link | b = 10 cm | b = 30 cm | b = 50 cm |
|---|---|---|---|
| 1.5 cm (datasheet, line of sight) | 12 deg | 4.1 deg | 2.4 deg |
| 5 cm (realistic line of sight) | about 40 deg (useless) | 13.5 deg | 8.1 deg |
| 14 cm (phone, rooms) | useless | 38 deg | 23 deg |
| 10 cm uncorrected differential bias | 90 deg range, useless | 19 deg bias | 11.5 deg bias |

Further against it, tonight: with the stock firmware each board runs its own session with the phone, so the two ranges
are not simultaneous and a moving phone adds error; and each board probably serves only one phone, so two boards per
puck halve the number of pucks. No paper measuring this with two independent DW3000 nodes 10 to 50 cm apart was found.

### 7.2 Two separate pucks a few metres apart

Least-squares geometry (derived in section 5c of the research): with the two pucks seen from the phone at plus and minus
beta from the perpendicular, **sigma_along = sigma_r / (sqrt(2) · sin beta)** and
**sigma_across = sigma_r / (sqrt(2) · cos beta)**.

| Baseline | Phone on the bisector at | beta | sigma_along | sigma_across |
|---|---|---|---|---|
| 3 m | 5 m | 16.7 deg | 2.5 sigma_r | 0.74 sigma_r |
| 3 m | 10 m | | 4.8 sigma_r (about 0.7 m with sigma_r = 15 cm) | |

- Near the line through both pucks the across-baseline error goes to infinity; on that line the phone is unobservable
  across it. So never put the phone in line with two pucks, and never put three pucks in a line.
- Two circles always cross twice: a **mirror ambiguity** across the line between the pucks. The building twin removes a
  candidate that falls outside the building or inside a wall; a third puck removes it everywhere.

**Verdict for tonight (from the closed forms above):** separate pucks, three of them, 2 to 3 m apart, not in a line.
Two boards on one puck would need a rigid baseline of 50 cm or more and per-unit differential calibration to give a
bearing worth drawing.
