# iOS occupant app: build and bring-up guide

> **UNCOMPILED SOURCE.** The app in `apps/ios/` was written on a Linux machine with no Mac and no Swift compiler. It has been **parsed for syntax** with tree-sitter (`apps/ios/tools/syntax_check.log`: 9 files, 0 syntax errors) and the hand-written Xcode project file has been **parsed** with the pure-Python `pbxproj` package (`apps/ios/tools/pbxproj_check.log`: every Swift file is in the Sources phase, ids unique, every object reachable). An independent reviewer then read every file line by line as a compiler would, checked the delegate signatures and the less common APIs against Apple's documentation (the current documentation JSON) and against Apple's sample code, and fixed what it found (section 11). The JSON the app sends was derived by hand from the Swift structs and posted to the real server in live mode (`tests/test_ios_wire.py`, fixture `fixtures/beacon/ios_batch_examples.json`). None of that is a compile: **types, API names and method signatures are not compiler-checked**, and the app has **never run on a device**. Expect a compiler error or two on the first build; section 11 lists where, and the fix for each.

What the app does: the person opens it and presses **I NEED HELP**. The app connects over Bluetooth to up to 2 rescue boards (setting, 1 to 5; Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware), runs one Apple Nearby Interaction session per board to measure the UWB distance, and every 0.5 s posts the distances, the Bluetooth signal strength and the person's status to the Beacon server (`POST /api/ingest`, feed addition A2 in `docs/BEACON_FEED.md`). It shows the server's answer ("2 rescue beacons hear you").

The live demo is **two boards and two phones** (iPhone 14 and iPhone 15). Qorvo's stock firmware serves **one phone per board at a time** and stops advertising while connected (Qorvo forum reports collected by the lead), so the two phones must share: **Take turns** (section 9a) or per-phone board toggles.

Total time if nothing goes wrong: about 45 minutes. Budget 90.

## Files

| Path | What |
|---|---|
| `apps/ios/SandtableBeacon/*.swift` | 9 source files: `SandtableBeaconApp` (entry), `ContentView` (the SOS screen), `SetupView`, `Theme`, `Models` (JSON shapes, settings), `PuckLink` (one board: Bluetooth + its `NISession`), `PuckManager` (Bluetooth central), `Sensors` (battery, stillness, barometer, compass), `Reporter` (the 0.5 s POST) |
| `apps/ios/SandtableBeacon/Info.plist` | usage strings for every permission the code can trigger, App Transport Security exception |
| `apps/ios/SandtableBeacon.xcodeproj` | the committed, hand-written project (**primary route**): `project.pbxproj` (classic format, objectVersion 56, explicit file references), a shared scheme and workspace data. No asset catalog: the app has a blank icon, which is fine for running on your own phone |
| `apps/ios/project.yml` | XcodeGen spec (secondary route) |
| `apps/ios/tools/syntax_check.py`, `.log` | the tree-sitter syntax parse and its output |
| `apps/ios/tools/pbxproj_check.py`, `.log` | the project-file parse and cross-reference check and its output |
| `tests/test_ios_wire.py`, `fixtures/beacon/ios_batch_examples.json` | the batches the app sends (derived by hand from `Models.swift` and `Reporter.swift`, synthetic) posted to the real server in live mode; also checks the server's answer decodes into the app's `IngestResponse` / `WireAck` |

The Bluetooth and Nearby Interaction call sequence follows Apple's public sample ["Implementing spatial interactions with third-party accessories"](https://developer.apple.com/documentation/nearbyinteraction/implementing-spatial-interactions-with-third-party-accessories) (`BluetoothLECentral.swift`, `AccessoryDemoViewController.swift`). No sample code is copied into this repository: the files are our own code modelled on it. Comments starting "Differs from the sample" in `PuckLink.swift` and `PuckManager.swift` mark every deliberate difference.

## 1. Before you start (10 min)

1. **A Mac with Xcode 15 or later** (iOS 17 SDK or later; the app targets iOS 16.0). Xcode 16 or 26 is fine.
2. **Your Apple ID in Xcode**: Xcode > Settings (Cmd-,) > Accounts > `+` > Apple ID. A free personal team works (apps expire after 7 days; fine for tonight).
3. **A cable** for each iPhone (Lightning for the iPhone 14, USB-C for the iPhone 15).
4. **Developer Mode on each iPhone**: plug the phone into the Mac once with Xcode open (the switch only appears after that), then on the phone: Settings > Privacy & Security > Developer Mode > On. The phone restarts; unlock it and tap **Turn On**.
5. **The boards are flashed** with Qorvo's Nearby Interaction firmware and powered (USB on J20, the port nearest the antenna). See `docs/research/ios_ni.md`, section A.
6. `git clone` (or `git pull`) the repository on the Mac. Only if route A below fails: install XcodeGen (`brew install xcodegen`).

## 2. Open the project (5 min) — pick ONE route

**Route A, primary: the committed project.** Double-click `apps/ios/SandtableBeacon.xcodeproj` (or `open apps/ios/SandtableBeacon.xcodeproj`). The scheme `SandtableBeacon` is already there. Go to section 3.

**Route B, if Xcode cannot open the committed project (XcodeGen):**

```bash
cd apps/ios
xcodegen            # REPLACES SandtableBeacon.xcodeproj; `git checkout apps/ios/SandtableBeacon.xcodeproj` restores it
open SandtableBeacon.xcodeproj
```

**Route C, always works (manual):** if routes A and B fail to open or build for project-file reasons:

1. Xcode > File > New > Project > iOS > **App**. Product name `SandtableBeacon`, Interface **SwiftUI**, Language **Swift**, no tests, no Core Data. Save it anywhere outside `apps/ios`.
2. In the new project, delete its two Swift files (`SandtableBeaconApp.swift`, `ContentView.swift`) (Move to Trash). Keep its `Assets.xcassets`.
3. Drag the folder `apps/ios/SandtableBeacon` from Finder into the project navigator; tick **Copy items if needed** and the app target.
4. Select the project > target > **Build Settings** (All, Combined), search and set:
   - **Info.plist File**: `SandtableBeacon/Info.plist` (the path of the dragged folder's plist, relative to the project).
   - **Generate Info.plist File**: No.
   - **iOS Deployment Target**: 16.0.
   - **Swift Language Version**: Swift 5.
   - **Strict Concurrency Checking**: Minimal.
   - **Default Actor Isolation** (Xcode 26 and later only): `nonisolated`. New Xcode 26 projects default to MainActor; this code is written for nonisolated.
   - **Approachable Concurrency** (Xcode 26 and later only): No.
   - **Targeted Device Family**: iPhone.
5. If the build says **"Multiple commands produce ... Info.plist"**: select `Info.plist` in the navigator, open the File inspector (Cmd-Option-1) and untick the target under Target Membership.

## 3. Signing (3 min)

1. Click the blue project icon at the top of the navigator > target **SandtableBeacon** > **Signing & Capabilities**.
2. Tick **Automatically manage signing**. **Team**: your Apple ID (Personal Team).
3. **Bundle Identifier**: change `com.example.sandtablebeacon` to something unique, e.g. `com.yourname.sandtablebeacon`. A personal team cannot use `com.example`.
4. No capabilities need adding (no background modes, no special entitlements).

## 4. Compile check before touching the phone (3 min, optional but fastest)

This compiles every file without signing and collects every error with its file and line in one text file (inside `apps/ios/build/`, which git ignores):

```bash
cd apps/ios
mkdir -p build
xcodebuild -project SandtableBeacon.xcodeproj -scheme SandtableBeacon -sdk iphoneos \
  -configuration Debug CODE_SIGNING_ALLOWED=NO build 2>&1 | tee build/build.txt | grep -E "error:|warning:" > build/errors.txt
wc -l build/errors.txt
```

If `build/errors.txt` has `error:` lines, go to section 11 and paste it. If `build/build.txt` ends with `** BUILD SUCCEEDED **`, continue.

If Xcode offers **"Update to recommended settings"**, click away (do not accept tonight): the project is meant to build as committed, and newer recommended settings can switch on stricter concurrency checking.

## 5. Build and run on the phone (5 min)

1. Plug the iPhone in, unlock it, tap **Trust This Computer** if asked.
2. In Xcode's toolbar, pick the phone as the run destination (top centre: "SandtableBeacon > *your iPhone*").
3. Press **Cmd-R**. The first build takes a minute or two; Xcode may ask for your Mac password to use the signing key (Always Allow).
4. First launch fails with **"Untrusted Developer"**: on the phone, Settings > General > **VPN & Device Management** > your Apple ID under Developer App > **Trust**. Press Cmd-R again.

## 6. The permission prompts, in the order they appear (5 min)

Do this dry run once on each phone, before any demo, so no prompt pops up during it. Tap **Allow** (or **OK**) on every one.

| When | Prompt | Why |
|---|---|---|
| Setup (gear icon) > type the server address > **Test connection** | "Beacon would like to find and connect to devices on your local network" (Local Network) | the plain-HTTP POST to the Beacon server. The first test may fail while the prompt is on screen: tap Test again |
| Setup > **Find beacons now (sends nothing)** | "Beacon would like to use Bluetooth" | finding and connecting to the boards |
| as soon as the first board answers (a few seconds later) | "Allow Beacon to find and interact with nearby devices?" (Nearby Interactions) | the UWB distance. **If this one is denied, there is no distance at all** |
| main screen > **I NEED HELP** | "Beacon would like to access your Motion & Fitness activity" | the barometer (floor). Denying it only loses the air pressure |
| only if Setup > Experimental > Camera-assisted direction is on | Location ("Allow While Using App") and Camera | the compass needs location for true north; camera assistance uses ARKit |

Denied one by mistake? Settings > Privacy & Security > (Local Network, Bluetooth, Nearby Interactions, Motion & Fitness, Camera, Location Services) > Beacon. After allowing Nearby Interactions there, turn Find beacons off and on (or Stop sharing and start again): sessions refused earlier are not retried by themselves.

Do this dry run with **Take turns OFF**: with it on, a board that gives no distance within 8 s is released, and the Nearby Interactions prompt can take longer than that to answer (the board is retried a few seconds later, so nothing breaks, it is just slower).

## 7. Network and server address (5 min)

1. **Put every device on ONE phone hotspot**: the laptop and both iPhones join the same hotspot (a spare phone's, or one of the two iPhones': Settings > Personal Hotspot). Venue Wi-Fi often blocks traffic between devices ("client isolation"), so the phones could not reach the laptop. A tailnet also works (Tailscale app on each phone; use the laptop's `100.x.y.z` address).
2. On the laptop: `python -m beacon.demo --source live --lan`. It prints the URL to use, e.g. `http://172.20.10.3:1885` on an iPhone hotspot.
3. **Fastest: scan the QR code.** The board page shows a QR code that encodes `sandtablebeacon://server?url=http://HOST:1885`. Open the iPhone's **Camera** app, point it at the code, tap the banner "Open in Beacon". The app stores the address and shows "Server set from QR code: ..." (tap the line to hide it). The link registers through `CFBundleURLTypes` in `Info.plist` (scheme `sandtablebeacon`); only `http` and `https` addresses with a host are accepted.
4. Or type it: gear icon > Rescue server > type the URL > **Test connection** > it should say "Server reachable." and show the `/healthz` answer.
5. The first connection to the laptop triggers the **Local Network** prompt (section 6). Allow it, or nothing reaches the server.
6. **Type the IP address, not a host name.** The server speaks plain HTTP, which App Transport Security (ATS) restricts. What Apple's documentation says (`NSAppTransportSecurity`, `NSAllowsLocalNetworking`, `NSAllowsArbitraryLoads`, `NSExceptionDomains`, read 2026-11-14): since iOS 17, ATS no longer allows connections to IP addresses by default; `NSAllowsLocalNetworking = YES` allows unqualified names, `.local` names and IP addresses (IPv4 or IPv6); on iOS 10 and later `NSAllowsArbitraryLoads` is **ignored** whenever `NSAllowsLocalNetworking` is present (Apple recommends setting both, the arbitrary-loads key only serves older systems); and since iOS 17 `NSExceptionDomains` accepts IP addresses and CIDR ranges. This build's `Info.plist` therefore has both keys plus an explicit exception for the tailnet range `100.64.0.0/10`.
   - **Works:** `http://192.168.x.y:1885`, `http://10.x.y.z:1885`, `http://172.20.10.x:1885` (an iPhone hotspot), `http://100.x.y.z:1885` (the laptop's Tailscale address), `http://name.local:1885`, `http://name:1885` (one word), and anything over `https://`.
   - **Blocked:** plain `http://` to a full DNS name, such as a MagicDNS `host.tail1234.ts.net` name or any public name (Setup shows a warning if you type one). If you really need one: delete the `NSAllowsLocalNetworking` key from `Info.plist` (then `NSAllowsArbitraryLoads` applies to everything) and rebuild.
   - Both are demo settings; a shipped app would use HTTPS. The tailnet route needs the Tailscale app connected on the phone; the LAN route needs the Local Network permission (section 6).
7. Optional: Setup > **Which suite are you in?** > Load suites from server > tap the suite (sent as `report.home_suite`, the occupant's own statement of where they are; also fills the floor).

## 8. Check what this phone supports (1 min)

Setup > **This phone** shows a line such as `UWB distance: yes · direction: no · camera assistance: yes`. Write it down for each phone.

- `UWB distance: no`: this phone cannot range; it only reports the Bluetooth signal strength of each board.
- `direction: no` is expected on the iPhone 14 and the iPhone 15 with these boards (developer reports in `docs/research/ios_ni.md`, section D). Confirm it on your phones.

## 9. Test sequence (25 min)

1. **Prove the board works with Qorvo's own app first.** Install **"Qorvo Nearby Interaction"** (free, App Store). Power one board, open the app: it should list the board and show a distance. Then **close the Qorvo app completely** (swipe it away): it keeps the board's Bluetooth connection, and a board probably serves one phone at a time.
2. **Our app, one board:** power ONE board. Setup > **Find beacons now**. The row should go `Connecting` > `Finding service` > `Asking board to start` > `Starting UWB` > `Ranging`, with the distance updating several times a second. Check it against a tape measure at 1, 2 and 3 m.
3. **Label the boards.** Each row shows the board's key, `name#ABCD`: the Bluetooth name plus the last two bytes of the board's configuration data (a forum report says these follow the board's UWB short address, so it should be the same on every phone). Write it on the board with tape. Repeat with each board (power one at a time, or read the event log at the bottom of Setup).
4. **Is the key stable?** Turn Find beacons off and on, and check the same board shows the same `#ABCD`, then check on the second phone. If it changes, Setup shows a warning: turn on **Key = board name only**, and if all boards share one name, give them different names in firmware or report boards by name order. Tell the server builder which key format you use.
5. **Two boards, one phone:** Use at most 2 (the default). Both should reach `Ranging` at the same time (one NISession per board).
6. **Two phones:** see 9a below.
7. **The server:** start the server (step 7), press **I NEED HELP** on the phone. Expect "Server: connected" and, once the boards are placed on the board view, the ack line ("2 rescue beacons hear you"). Boards the server does not know yet are listed under the beacons as "Not yet placed by the rescue team". Open **What is being sent** to see the exact JSON.
8. **Optional, experimental bearing:** Setup > Camera-assisted direction on, then reconnect (Find beacons off/on). Hold the phone up in light, camera unobstructed, and move it slowly in a circle for about 10 s. Bearings are sent only when an angle AND a true-north heading exist. Stand with a board to your right: the bearing sent should be about your compass heading + 90. If it is heading - 90, turn on **Flip angle sign** (Apple does not document the sign of `horizontalAngle`).

### 9a. Two phones, two boards (the live demo)

A board serves one phone at a time and is invisible to the other phone while connected. Pick ONE of:

- **Take turns (recommended): on BOTH phones** Setup > Rescue beacons > **Take turns** ON (hold 4 s, the default). Each phone then ranges one board at a time: it connects, waits for distances, keeps the board for 4 s, sends stop (`0x0C`), disconnects, and leaves that board alone for a random 4 to 8 s while it tries the other one. A board that gives no distance within 8 s is dropped and retried later. Each phone keeps the last distance to every board and keeps sending it, with the time it was measured, for up to 25 s, so the server always has both ranges for a phone that is not moving. Expect each board's row to cycle `Connecting` > ... > `Ranging` > `Waiting for its turn`.
- **Dedicate boards:** with four boards powered as two pairs, switch boards off per phone with the **use** toggle on each row (main screen or Setup). The choice is stored on the phone by the board's Bluetooth identifier. Take turns OFF, Use at most 2.

Check: both phones show both boards with a distance at most about 15 s old ("4 s ago"), and the event log has no repeating "no distance within 8 s".

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Stuck at "None connected yet", Beacons heard: 0 | board not advertising: wrong firmware image, board not powered, or Bluetooth off | flash `DWM3001CDK-QANI-FreeRTOS...hex` (research note, section A); power on J20; check Setup shows "Bluetooth on"; check the board in Qorvo's app |
| Board heard but stuck at "Asking board to start", then "no answer from the board" | the board is connected to another phone (or Qorvo's app), or its firmware uses different message ids | close Qorvo's app; power-cycle the board; turn on Take turns on both phones (9a) |
| A board shows "Not advertising (another phone may be using it)" | the other phone holds it | expected with two phones; Take turns on both, or dedicate boards (9a) |
| "Finding service" then "the board has no Nearby Interaction service" | firmware advertises a service this build does not know | check the service UUID with nRF Connect; this build knows `2E938FD0-6A61-11ED-A1EB-0242AC120002` (Qorvo) and `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART, Apple's sample) |
| Ranging state never reached, no distance, banner "Nearby Interaction is off" | the Nearby Interactions prompt was denied | Settings > Privacy & Security > Nearby Interactions > Beacon > on; restart the app |
| "iOS rejected the board's configuration (error -5888)" or "configuration is invalid" | the bytes after message id 0x01 are not a valid accessory configuration (wrong firmware, or the board sent a different framing) | use Qorvo's stock QANI image; confirm Qorvo's app works with the same board; read the event log line with the byte count |
| "this phone cannot run more UWB sessions at once" | the phone's concurrent-session limit | lower "Use at most" |
| "Server unreachable" | Local Network permission denied; wrong address or port; laptop firewall; venue Wi-Fi isolating devices; phone on cellular or another network; a DNS name instead of an IP | Settings > Privacy & Security > Local Network > Beacon on; scan the QR code again; allow port 1885 in the laptop firewall; put all devices on one phone hotspot (section 7) |
| Scanning the QR code does nothing or opens Safari | the app is not installed on that phone, or the code is not a `sandtablebeacon://` link | install the app first; type the address by hand |
| "server answered HTTP 404" or "HTTP 422" | server running without the live ingest route, or a field mismatch | update the server; send the JSON under "What is being sent" to the server builder |
| "server answered HTTP 400: obs[2]: ..." | the server rejected one value, and with it the whole batch (the text after the colon names the field) | send that line and the JSON under "What is being sent" to the server builder. The app already keeps distances above 0 and Bluetooth signal within -130 to 10 dBm, the two ranges the server checks |
| "server answered HTTP 409" | the server was started in `fake` (sample) mode | restart it with `--source live` |
| Distance freezes when the screen locks or the app goes to the background | expected: no background mode in this version | keep the screen open (the app keeps it awake while sharing) |

## 11. When it does not compile

**Get the full error text.** Either:

- **Terminal (best):** run the `xcodebuild` command of section 4 and send `build/errors.txt` (every error with file and line). Or
- **Xcode:** open the Issue navigator (Cmd-5), click the first error, select all issues (Cmd-A) and copy (Cmd-C). For the long form: Report navigator (Cmd-9) > the newest Build > right-click > **Copy Transcript for Shown Results** (or **Export**).

**Paste it to the agent** with the file names (e.g. "PuckLink.swift:412: error: ..."). Ask for the patched file(s); replace the file in `apps/ios/SandtableBeacon/` and build again. Paste warnings that say **"nearly matches optional requirement"** too: those are not errors, but they mean a delegate method will never be called.

### What was already checked and fixed (review of 2026-11-14)

Checked against Apple's documentation JSON for the current SDK: every delegate method the app implements (`CBCentralManagerDelegate`: `centralManagerDidUpdateState`, `didDiscover`, `didConnect`, `didFailToConnect`, `didDisconnectPeripheral`; `CBPeripheralDelegate`: `didModifyServices`, `didDiscoverServices`, `didDiscoverCharacteristicsFor`, `didUpdateNotificationStateFor`, `didUpdateValueFor`, `didWriteValueFor`, `didReadRSSI`; `NISessionDelegate`: `didGenerateShareableConfigurationData`, `didUpdate`, `didRemove`, `sessionWasSuspended`, `sessionSuspensionEnded`, `didInvalidateWith`; `CLLocationManagerDelegate`: `didUpdateHeading`, `didUpdateLocations`, `didFailWithError`, `locationManagerDidChangeAuthorization`), and the APIs most likely to be misspelled or too new (`NISession.deviceCapabilities` iOS 16, `supportsCameraAssistance` iOS 16, `isCameraAssistanceEnabled` iOS 16, `horizontalAngle` iOS 16, `NIError.Code.activeSessionsLimitExceeded`, `CMAltimeter.isRelativeAltitudeAvailable()`, `CLLocationManager.headingAvailable()`, `Text.monospacedDigit()` iOS 15, `tint` iOS 15/16). The Bluetooth and Nearby Interaction sequence was compared with Apple's sample. Nothing newer than iOS 16.0 is used; no view builder has more than 10 children; every `ForEach` is over identifiable data.

Fixed in that review:

| File, symbol | What was wrong | Fix |
|---|---|---|
| `Reporter.swift`, `buildBatch` | the server rejects the WHOLE batch (HTTP 400, the report too) if one value is out of range, and the app repeats a reading for 25 s: a phone lying on a board (`d_m` 0.00) would have been silent for 25 s | `d_m` is sent as at least 0.01 and only below 149.9; `rssi` only within -130 to 10 dBm |
| `Reporter.swift`, `handleResponse` | an HTTP error showed only the code | shows the server's `detail` text (new `ServerErrorReply` in `Models.swift`) and logs it |
| `Reporter.swift`, `post` | "server slow: batch dropped" overwrote "server unreachable (...)" every other tick | the real error stays on screen |
| `PuckLink.swift`, `didUpdateNotificationStateFor` | `initialize` (0x0A) went out on the FIRST subscription confirmed; with two notifying characteristics the board's answer could come before the second was ready | sent once every requested subscription has answered (the 3 s fallback in `tick` is unchanged) |
| `PuckLink.swift`, `didDiscoverCharacteristicsFor` | wrote to the characteristic named "rx" even if only "tx" is writable (naming from the other side's view) | writes to whichever of the two is writable |
| `PuckLink.swift`, `didModifyServices` | after the board re-published its services, `initialize` was never re-sent and the 12 s watchdog fired at once | resets the attempt counter and the watchdog clock |
| `PuckManager.swift`, `capture` | section 9 promised a warning when a board's `#ABCD` changes between connections; only a change within one connection was detected | compared with the key last seen for the same Bluetooth identifier |
| `PuckManager.swift`, `stop` | the "Nearby Interaction is off" banner stayed after the permission was granted | cleared on stop (set again if still denied) |
| `PuckManager.start`, `Reporter.start` | `Timer.scheduledTimer` runs in the default run-loop mode, which pauses while a finger scrolls: no batches and no turn-taking while scrolling | timers added in `.common` mode |
| `Models.swift`, `DirectoryEntry.id` | two suites with the same number on two floors had the same `ForEach` id | id is floor + suite |
| `Info.plist`, ATS | tailnet addresses relied only on the "IP addresses" wording of `NSAllowsLocalNetworking` | explicit iOS 17+ exception for `100.64.0.0/10` (section 7) |

### Where a compile error is still most likely, and the fix

Most likely first:

1. **Concurrency diagnostics, only if the language mode or isolation differs from the committed settings** (Swift 5, Strict Concurrency Minimal, no default actor isolation). Errors such as "main actor-isolated property 'shared' can not be referenced from a nonisolated context", "capture of non-sendable ..." or "sending ... risks causing data races". Where: `UIApplication.shared` in `Reporter.start()` / `Reporter.stop()`; `UIDevice.current` in `Sensors.refreshBattery()`; the escaping completion of `ServerAPI.get` that sets `@State` in `SetupView.testServer()` and `SetupView.loadDirectory()`; the `URLSession` completion in `Reporter.post`; the `Timer` closures in `PuckManager.start()` and `Reporter.start()`; the `NWPathMonitor` handler in `Sensors.startNetworkMonitor()`. **Fix first:** target > Build Settings > Swift Language Version = Swift 5, Strict Concurrency Checking = Minimal, and (Xcode 26) Default Actor Isolation = nonisolated, Approachable Concurrency = No. If one error remains on a line that is already on the main thread (all of the above are), wrap that line: `MainActor.assumeIsolated { UIApplication.shared.isIdleTimerDisabled = true }`.
2. **A "nearly matches optional requirement" WARNING on a delegate method.** Not an error, but that callback will never run, so the app stops at one state (for example "Subscribing" or "Starting UWB"). Where: the extensions at the end of `PuckLink.swift`, `PuckManager.swift` and `Sensors.swift`. Fix: let Xcode's fix-it rename the method to the exact requirement it suggests. All signatures were checked against Apple's documentation, so this is unlikely.
3. **"The compiler is unable to type-check this expression in reasonable time".** Where: the longest `+` string chains: `PuckLink.handleMessage` ("ignored message id 0x" ...), `PuckLink.startSession` ("configuration suffix changed from" ...), `SetupView.testServer` (`serverCheck = "Not reachable: " + ...`). Fix: rewrite as one interpolated string, `"configuration suffix changed from \(first) to \(suffix); keeping the first"`.
4. **"Ambiguous use of 'system(size:weight:design:)'"** (unlikely: iOS 16 added a second overload with optional parameters, and the non-optional one should win). Where: every `.font(.system(size: ..., design: ...))`, for example `ContentView.sentCard` and `SetupView.logSection`. Fix: use the text-style form, `.font(.system(.caption2, design: .monospaced))`.
5. **Deprecation warnings** (`NavigationView`, `cornerRadius`, `foregroundColor`, `accentColor`, `Section(header:footer:)`): warnings only, ignore tonight.

Not compile errors but the first things a build or a run shows:

- **Signing: "Failed to register bundle identifier"**: the bundle id `com.example.sandtablebeacon` is taken or not allowed for your team. Change it (section 3). The URL scheme does not depend on it.
- **"Untrusted Developer"** on first launch: section 5 step 4.
- **No distance, row stuck at "Asking board to start"**: the board did not answer `initialize`; see section 10 (another phone or Qorvo's app holds the board, or the firmware uses other message ids; the event log in Setup shows every id the board sent).

## 12. What the app sends (for the server)

One batch every 0.5 s while sharing, to `POST <server>/api/ingest`, exactly the A2 shape plus `eid` on each observation:

```json
{"device": {"eid": "a3f19c02", "platform": "ios", "model": "iPhone 14", "chip": "U1", "app": "0.1"},
 "obs": [
  {"kind": "uwb", "t": 1794679200.512, "eid": "a3f19c02", "accessory": "DWM3001CDK#1A2B", "d_m": 2.31},
  {"kind": "sos", "t": 1794679200.101, "eid": "a3f19c02", "accessory": "DWM3001CDK#1A2B", "rssi": -61},
  {"kind": "bearing", "t": 1794679200.5, "eid": "a3f19c02", "accessory": "DWM3001CDK#1A2B", "to_puck_compass_deg": 32.0, "sigma_deg": 25, "source": "camera-assist+compass"},
  {"kind": "report", "t": 1794679200.5, "eid": "a3f19c02", "prev_eid": "09be11d4",
   "report": {"status": "trapped", "people": 2, "can_move": false, "safe_outside": false, "battery_pct": 41,
              "floor_reported": 3, "baro_hpa": 1002.31, "still": true, "still_s": 42, "home_suite": "310",
              "text": "...", "model": "iPhone 14", "chip": "U1", "network": "wifi", "platform": "ios", "app": "0.1"}}
 ]}
```

- `accessory` is `"<advertised Bluetooth name>#<4 hex digits>"` (or the name alone when the user turns that on, or before the board sends its configuration). The board name above is an example: the real advertised name is not confirmed.
- `uwb` and `sos`: the latest reading of **every board measured in the last 25 s**, in **every** batch, each with the time it was **measured** (`t`), not the send time. So the same (`accessory`, `t`) pair arrives several times: the server should treat a repeated `t` as the same measurement (dedupe), and a board this phone released while taking turns keeps counting for 25 s. `bearing` only when the experimental toggle is on and both a live angle and a true heading exist.
- Optional fields that have no value are **left out**, not sent as `null` (e.g. `prev_eid` appears only after the random id rotated, until one batch is accepted).
- The server rejects a whole batch on one out-of-range value, so the app sends `d_m` as at least 0.01 (a phone lying on the board can measure 0) and below 149.9, and `rssi` only within -130 to 10.
- Hand-derived (synthetic, no iPhone involved) examples of every batch kind, and the test that posts them to the server in live mode: `fixtures/beacon/ios_batch_examples.json`, `tests/test_ios_wire.py` (`.venv/bin/python -m pytest tests/test_ios_wire.py -q`).
- "I am out" is sent as `status: "ok"` with `safe_outside: true`.
- **Stop sharing** sends `{"device": {...}, "obs": [{"kind": "bye", "t": ..., "eid": "..."}]}`.
- The app reads `ack.text` (shown large), `ack.heard_by` and `unknown_accessories` from the answer; every field is optional.
- `report.home_suite` is the suite the person picked under "Which suite are you in?" (their own statement of where they are); `report.model` comes from a small table of iPhone identifiers (the raw identifier such as `iPhone14,7` when unknown); `report.chip` is `U1` (iPhone 11 to 14), `U2` (15 and later) or absent when the phone has no UWB.
- Server address by QR: the board page should encode `sandtablebeacon://server?url=<http://HOST:1885>` (URL-encode the inner URL if it has its own `?` or `&`).

## 13. Known limits

- Uncompiled, never run. No background mode: the person must keep the screen open (the app keeps it awake while sharing).
- The Qorvo service UUIDs and the use of Apple's message ids by Qorvo's stock firmware come from forum posts, not Qorvo documents. The key suffix rests on one forum report. One phone per board at a time is from Qorvo forum reports; Take turns is designed around it but untested.
- Take turns trades update rate for sharing: with two boards each phone measures each board for about 4 s out of every 12 to 15 s.
- Camera-assisted direction is experimental: it needs light, an unobstructed camera and a moving phone, is enabled on at most one board at a time, and the sign of the angle is undocumented.
