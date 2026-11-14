# Flashing Qorvo's stock Nearby Interaction firmware onto a DWM3001CDK

> **Written from documentation and forum reports; not executed on a board.** No J-Link, no DWM3001CDK and no SEGGER
> software has been run by this project. Sources are cited inline. Where SEGGER's or Qorvo's pages do not say
> something, this page says "not confirmed" and tells you what to do either way.

Used by `hardware/BRINGUP.md` step 2. Only flash boards that failed BRINGUP step 1 (a board that already ranges with
Qorvo's app does not need flashing).

## What you need

- SEGGER **J-Link Software and Documentation Pack** (<https://www.segger.com/downloads/jlink/>, free, accept the terms,
  no account: "publicly available for download, at no charge",
  [SEGGER](https://www.segger.com/news/seggers-complete-j-link-software-now-available-for-linux-on-arm/)). It runs on
  macOS (Apple Silicon native), Windows and Linux, including Linux arm64 (the UNO Q).
- Qorvo's hex: `DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex` (reported working Feb 2026) or
  `DWM3001CDK-QANI-FreeRTOS.hex` ([forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229)).
  Download: `hardware/BRINGUP.md` step 0.1. **Never** a hex named after another board (`nRF52840DK-...`).
- A micro-USB **data** cable into **J9**: the **bottom** micro-USB port, labelled **"J-Link"**
  ([Zephyr board page](https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html)). The board
  has an on-board SEGGER J-Link, so no external programmer is needed
  ([product brief](https://www.firaconsortium.org/sites/default/files/2024-08/DWM3001CDK%20Product%20Brief.pdf)).

The recipe from Qorvo's forum, in one line: **"Use JFlashLite, USB to J9, and select NRF52833, not 52832 ... The cdk is
52833, picking the wrong device will mess things up"** ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)).

**Do not use drag-and-drop** (copying the hex onto a USB drive): "J-Link OB drag and drop doesn't work" on this board
([forum 11801](https://forum.qorvo.com/t/getting-started-with-dwm3001cdke/11801)).

---

## A. J-Flash Lite (the window; macOS and Windows) — 3 min per board

1. Plug the board's **J9** into the Mac (or PC) with a data cable. Only one board at a time.
2. Open **J-Flash Lite**:
   - macOS: Finder → Applications → **SEGGER** → **J-Link** folder → **J-Flash Lite** (the folder name may carry
     the version number). If macOS says the app cannot be opened, right-click → Open.
   - Windows: Start menu → **SEGGER - J-Link** → **J-Flash Lite**.
   - Linux: `JFlashLiteExe` (in the J-Link install folder, usually `/opt/SEGGER/JLink/`; not checked tonight).
3. The configuration dialog appears first ([SEGGER](https://kb.segger.com/J-Flash_Lite)):
   - **Device**: click **...**, type `52833` in the search box, pick **`NRF52833_XXAA`** (manufacturer Nordic Semi).
     NOT 52832, NOT 52840.
   - **Interface**: **SWD**.
   - **Speed**: **4000** (kHz).
   - **OK**.
4. Main window:
   - **Data file**: click **...**, choose the QANI hex.
   - Click **Erase Chip** ("Erases all flash banks of the device", [SEGGER](https://kb.segger.com/J-Flash_Lite)). Wait
     until the log at the bottom says it is done.
   - Click **Program Device**. Wait until the log says it is done (the exact wording was not checked).
5. **Reset:** SEGGER's page does not say whether J-Flash Lite restarts the board after programming (not confirmed). So
   always do it yourself: **unplug the cable and plug it back in** (a power cycle always restarts the board), or press
   the board's reset button (the board has "Reset and user-defined buttons",
   [product brief](https://www.firaconsortium.org/sites/default/files/2024-08/DWM3001CDK%20Product%20Brief.pdf)).
6. Verify (section C).

If J-Link offers to **update the on-board J-Link's firmware**, accepting is SEGGER's normal procedure (not checked for
this board; declining also works for flashing).

If J-Link says the device is **secured / protected** and asks whether to **unlock** it: answer **Yes**. It erases the
whole chip, which you were about to do anyway. (Not confirmed whether these boards ship protected.)

---

## B. Command line (J-Link Commander) — 1 min per board

Script: **`hardware/jlink/flash_qani.jlink`** (erase, load the hex, reset, run, exit). Commands from SEGGER's
J-Link Commander page: `Erase` ("Erases all flash sectors"), `LoadFile <FileName>` (`.hex` supported), `Reset` (`r`),
`Go` (`g`), `Exit` ([SEGGER](https://kb.segger.com/J-Link_Commander)).

**The one thing to edit:** the hex path. Either copy Qorvo's hex to `hardware/jlink/qani.hex` (that folder's
`.gitignore` keeps it out of the repository; the licence was not found, so never commit it), or replace the path on the
`loadfile` line with the full path of your hex (avoid spaces in the path).

Run from the project folder, J9 plugged in:

**macOS / Linux**

```bash
JLinkExe -device NRF52833_XXAA -if SWD -speed 4000 -autoconnect 1 -CommanderScript hardware/jlink/flash_qani.jlink
```

**Windows PowerShell** (the install folder may include the version, e.g. `JLink_V794`; press Tab to complete it)

```powershell
& "C:\Program Files\SEGGER\JLink\JLink.exe" -device NRF52833_XXAA -if SWD -speed 4000 -autoconnect 1 -CommanderScript hardware\jlink\flash_qani.jlink
```

**UNO Q (Debian arm64)**: same as Linux, after installing SEGGER's Linux ARM 64-bit DEB (`hardware/UNO_Q.md`).

Notes:
- SEGGER's page documents the options `-Device`, `-If`, `-Speed`, `-AutoConnect 1` ("Forces the J-Link Commander to
  connect to the target, automatically") and **`-CommandFile`** for a script ([SEGGER](https://kb.segger.com/J-Link_Commander)).
  `-CommanderScript` (used above) is not on that page; it is the spelling newer J-Link versions accept for the same
  thing (not confirmed). **If your J-Link says it does not know `-CommanderScript`, use `-CommandFile` instead.**
- `JLinkExe` not found on macOS/Linux: the installer normally puts it on the PATH; otherwise use the full path inside the
  SEGGER folder (macOS: `/Applications/SEGGER/JLink*/JLinkExe`; Linux: `/opt/SEGGER/JLink/JLinkExe`; not checked tonight).
- "Could not open file": the path in the script is relative to the folder you ran the command from. Run it from the
  project folder, or put the full path in the script.
- **Works when** the output ends without an error after `loadfile` and J-Link Commander exits by itself.

Several boards: plug the next board's J9, press the Up arrow, Enter.

---

## C. Verify (2 min)

1. **The board appears in Qorvo's iOS app** and shows a distance (`hardware/BRINGUP.md` step 1 table). This is the test
   that matters.
2. **Optional: the board's debug log over RTT.** Qorvo's stock firmware writes its log over SEGGER RTT on the J-Link
   port J9, not over a serial port ("QNI uses a rtt_reporter task to print all the info",
   [forum 13613](https://forum.qorvo.com/t/dwm3001cdk-nearby-interaction-trying-to-get-ranging-via-usb-uart/13613)).
   - **J-Link RTT Viewer** (in the same SEGGER pack): Connection to J-Link: USB; Target device: `NRF52833_XXAA`;
     Target interface: SWD, 4000 kHz; RTT control block: Auto Detection; OK. Terminal 0 should show text when the board
     boots (press reset or replug). What exactly the stock firmware prints is **not confirmed**; a user who read it in
     Feb 2026 saw peer-manager errors there (section D), so text does appear.
   - **Command line:** keep a J-Link connection open in one terminal:
     `JLinkExe -device NRF52833_XXAA -if SWD -speed 4000 -autoconnect 1` (leave it at its `J-Link>` prompt; if the
     board stopped advertising, type `g` and Enter to let it run). In a second terminal, read RTT channel 0 from the
     telnet port J-Link serves while connected ("a TELNET-like channel on a localhost port (default: 19021)",
     [SEGGER](https://kb.segger.com/RTT)): `nc localhost 19021` (macOS/Linux), or our host, which also shows what its
     parser makes of each line:
     `python -m beacon.host --server http://127.0.0.1:1885 --puck P1 --name laptop --rtt 127.0.0.1:19021 --learn`
   - **JLinkRTTLogger** writes RTT to a file: options `-Device NRF52833_XXAA -if SWD -Speed 4000 -RTTChannel 0`
     ([SEGGER](https://kb.segger.com/J-Link_RTT_Logger)); the output file name goes last
     (not listed on that page: if the logger asks questions instead, answer them with the same values and a file name).
     Our host follows such a file with `--rtt-log FILE`.

---

## D. Recover a board

| Symptom | Do |
|---|---|
| J-Flash Lite / JLinkExe cannot find a J-Link | cable into **J9** (not J20); a data cable, not charge-only; another USB port; on Windows wait for the J-Link driver to finish installing |
| J-Link found, but it cannot connect to the target | check the Device is `NRF52833_XXAA`; lower the speed to 1000 kHz; unplug and replug; if asked to unlock a secured device, say Yes (this erases the chip) |
| Board flashed with the wrong device (nRF52832) | Erase Chip with the right device `NRF52833_XXAA`, then Program Device again ([forum 25626](https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626)) |
| Flashed, but the app stays at "Scanning for accessories" | wrong hex: use `DWM3001CDK-QANI-FreeRTOS...hex`, never another board's image ([forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229)); if you have both the `_full_QNI_3_0_0` hex and the SDK hex, try the other one |
| RTT shows `fds_init() returned 0x860A` / `pm_init failed because pds_init() returned NRF_ERROR_STORAGE_FULL` | full chip erase (J-Flash Lite **Erase Chip**, or `erase` in J-Link Commander), then program again. The error is from [forum 23229](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229); that a full erase fixes it is **not confirmed** there, but it clears the flash storage the message complains about |
| Still dead | Nordic's own tools also drive this J-Link (`nrfjprog`, `nrfutil`, nRF Connect Programmer; the Zephyr board page lists `nrfjprog` and `nrfutil` as flash runners, [Zephyr](https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html)); not checked tonight. Otherwise set the board aside: three working boards are enough for the main demo (BRINGUP step 5) |
