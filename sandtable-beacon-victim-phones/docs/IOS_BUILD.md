# iOS occupant app: build and bring-up guide

> **UNCOMPILED SOURCE.** The app in `apps/ios/` was written on a Linux machine with no Mac and no Swift compiler. It has been **parsed for syntax** with tree-sitter (`apps/ios/tools/syntax_check.log`: 9 files, 0 syntax errors) and the hand-written Xcode project file has been **parsed** with the pure-Python `pbxproj` package (`apps/ios/tools/pbxproj_check.log`: every Swift file is in the Sources phase). Neither is a compile: **types, API names and method signatures are unchecked**, and the app has **never run on a device**. Expect a few compiler errors on the first build; section 11 says how to get them fixed quickly.

What the app does: the person opens it and presses **I NEED HELP**. The app connects over Bluetooth to up to 3 rescue boards (Qorvo DWM3001CDK running Qorvo's stock Nearby Interaction firmware), runs one Apple Nearby Interaction session per board to measure the UWB distance, and every 0.5 s posts the distances, the Bluetooth signal strength and the person's status to the Beacon server (`POST /api/ingest`, feed addition A2 in `docs/BEACON_FEED.md`). It shows the server's answer ("2 rescue beacons hear you").

Total time if nothing goes wrong: about 45 minutes. Budget 90.

## Files

| Path | What |
|---|---|
| `apps/ios/SandtableBeacon/*.swift` | 9 source files: `SandtableBeaconApp` (entry), `ContentView` (the SOS screen), `SetupView`, `Theme`, `Models` (JSON shapes, settings), `PuckLink` (one board: Bluetooth + its `NISession`), `PuckManager` (Bluetooth central), `Sensors` (battery, stillness, barometer, compass), `Reporter` (the 0.5 s POST) |
| `apps/ios/SandtableBeacon/Info.plist` | usage strings for every permission the code can trigger, App Transport Security exception |
| `apps/ios/SandtableBeacon/Assets.xcassets` | an empty AppIcon set (builds without an image; the app has a blank icon) |
| `apps/ios/project.yml` | XcodeGen spec (primary route) |
| `apps/ios/SandtableBeacon.xcodeproj/project.pbxproj` | hand-written project file (fallback route) |
| `apps/ios/tools/syntax_check.py`, `.log` | the tree-sitter syntax parse and its output |
| `apps/ios/tools/pbxproj_check.py`, `.log` | the project-file parse and cross-reference check and its output |

The Bluetooth and Nearby Interaction call sequence follows Apple's public sample "Implementing spatial interactions with third-party accessories" (`BluetoothLECentral.swift`, `AccessoryDemoViewController.swift`). Comments starting "Differs from the sample" in `PuckLink.swift` and `PuckManager.swift` mark every deliberate difference.

## 1. Before you start (10 min)

1. **A Mac with Xcode 15 or later** (iOS 17 SDK or later; the app targets iOS 16.0). Xcode 16 or 26 is fine.
2. **Your Apple ID in Xcode**: Xcode > Settings (Cmd-,) > Accounts > `+` > Apple ID. A free personal team works (apps expire after 7 days; fine for tonight).
3. **A cable** for each iPhone (Lightning for the iPhone 14, USB-C for the iPhone 15).
4. **Developer Mode on each iPhone**: plug the phone into the Mac once with Xcode open (the switch only appears after that), then on the phone: Settings > Privacy & Security > Developer Mode > On. The phone restarts; unlock it and tap **Turn On**.
5. **The boards are flashed** with Qorvo's Nearby Interaction firmware and powered (USB on J20, the port nearest the antenna). See `docs/research/ios_ni.md`, section A.
6. Optional: install XcodeGen (`brew install xcodegen`) for route A below.

## 2. Open the project (5 min) — pick ONE route

**Route A, primary (XcodeGen):**

```bash
cd apps/ios
xcodegen            # writes SandtableBeacon.xcodeproj, replacing the hand-written one
open SandtableBeacon.xcodeproj
```

**Route B (no XcodeGen):** double-click `apps/ios/SandtableBeacon.xcodeproj` (the hand-written project file).

**Route C, always works (manual):** if routes A and B fail to open or build for project-file reasons:

1. Xcode > File > New > Project > iOS > **App**. Product name `SandtableBeacon`, Interface **SwiftUI**, Language **Swift**, no tests, no Core Data. Save it anywhere outside `apps/ios`.
2. In the new project, delete its two Swift files (`SandtableBeaconApp.swift`, `ContentView.swift`) and its `Assets.xcassets` (Move to Trash).
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

This compiles every file without signing and collects every error with its file and line in one text file:

```bash
cd apps/ios
xcodebuild -project SandtableBeacon.xcodeproj -target SandtableBeacon -sdk iphoneos \
  -configuration Debug CODE_SIGNING_ALLOWED=NO build 2>&1 | tee build.txt | grep -E "error:|warning:" > errors.txt
wc -l errors.txt
```

If `errors.txt` has `error:` lines, go to section 11 and paste it. If it says `** BUILD SUCCEEDED **` at the end of `build.txt`, continue.

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

Denied one by mistake? Settings > Privacy & Security > (Local Network, Bluetooth, Nearby Interactions, Motion & Fitness, Camera, Location Services) > Beacon.

## 7. The server address (2 min)

1. On the laptop: `python -m beacon.demo --source live --lan`. It prints the URL to use, e.g. `http://192.168.1.20:1885`.
2. The phone and the laptop must be on the same Wi-Fi, or both on the tailnet (Tailscale app on the phone): then use the laptop's `100.x.y.z` address.
3. In the app: gear icon > Rescue server > type the URL > **Test connection** > it should say "Server reachable." and show the `/healthz` answer.
4. **Type the IP address, not a host name.** Why the Info.plist has both App Transport Security keys: the server speaks plain HTTP on a LAN or tailnet address. Since iOS 17, plain HTTP to an IP address is blocked unless `NSAllowsLocalNetworking` is true, and Apple documents that on iOS 10 and later `NSAllowsArbitraryLoads` is **ignored** whenever `NSAllowsLocalNetworking` is present (it is kept only for older systems). So this build allows plain HTTP to IP addresses, `.local` names and single-word names, and **blocks plain HTTP to a full DNS name** such as a MagicDNS `host.tailnet.ts.net` name (Setup shows a warning if you type one). If you really need a DNS name: delete the `NSAllowsLocalNetworking` key from `Info.plist`, so `NSAllowsArbitraryLoads` applies, and rebuild. Both are demo settings; a shipped app would use HTTPS.
5. Optional: **Load suites from server** and tap your suite (fills `home_suite` and the floor).

## 8. Check what this phone supports (1 min)

Setup > **This phone** shows a line such as `UWB distance: yes · direction: no · camera assistance: yes`. Write it down for each phone.

- `UWB distance: no`: this phone cannot range; it only reports the Bluetooth signal strength of each board.
- `direction: no` is expected on the iPhone 14 and the iPhone 15 with these boards (developer reports in `docs/research/ios_ni.md`, section D). Confirm it on your phones.

## 9. Test sequence (25 min)

1. **Prove the board works with Qorvo's own app first.** Install **"Qorvo Nearby Interaction"** (free, App Store). Power one board, open the app: it should list the board and show a distance. Then **close the Qorvo app completely** (swipe it away): it keeps the board's Bluetooth connection, and a board probably serves one phone at a time.
2. **Our app, one board:** Setup > Use at most **1** board > **Find beacons now**. The row should go `Connecting` > `Finding service` > `Asking board to start` > `Starting UWB` > `Ranging`, with the distance updating several times a second. Check it against a tape measure at 1, 2 and 3 m.
3. **Label the boards.** Each row shows the board's key, `name#ABCD`: the Bluetooth name plus the last two bytes of the board's configuration data (a forum report says these follow the board's UWB short address, so it should be the same on every phone). Write it on the board with tape. Repeat with each board (power one at a time, or read the event log at the bottom of Setup).
4. **Is the key stable?** Turn Find beacons off and on, and check the same board shows the same `#ABCD`, then check on the second phone. If it changes, Setup shows a warning: turn on **Key = board name only**, and if all boards share one name, give them different names in firmware or report boards by name order. Tell the server builder which key format you use.
5. **Three boards, one phone:** Use at most 3. All three should reach `Ranging`.
6. **Two phones:** set both phones to at most 2 or 3 (five boards shared). Watch for `no answer from the board`: that is a board already serving the other phone; the app gives up on it for 30 s and tries another.
7. **The server:** start the server (step 7), press **I NEED HELP** on the phone. Expect "Server: connected" and, once the boards are placed on the board view, the ack line ("2 rescue beacons hear you"). Boards the server does not know yet are listed under the beacons as "Not yet placed by the rescue team". Open **What is being sent** to see the exact JSON.
8. **Optional, experimental bearing:** Setup > Camera-assisted direction on, then reconnect (Find beacons off/on). Hold the phone up in light, camera unobstructed, and move it slowly in a circle for about 10 s. Bearings are sent only when an angle AND a true-north heading exist. Stand with a board to your right: the bearing sent should be about your compass heading + 90. If it is heading - 90, turn on **Flip angle sign** (Apple does not document the sign of `horizontalAngle`).

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Stuck at "None connected yet", Beacons heard: 0 | board not advertising: wrong firmware image, board not powered, or Bluetooth off | flash `DWM3001CDK-QANI-FreeRTOS...hex` (research note, section A); power on J20; check Setup shows "Bluetooth on"; check the board in Qorvo's app |
| Board heard but stuck at "Asking board to start", then "no answer from the board" | the board is connected to another phone (or Qorvo's app), or its firmware uses different message ids | close Qorvo's app; power-cycle the board; lower "Use at most" on the other phone |
| "Finding service" then "the board has no Nearby Interaction service" | firmware advertises a service this build does not know | check the service UUID with nRF Connect; this build knows `2E938FD0-6A61-11ED-A1EB-0242AC120002` (Qorvo) and `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART, Apple's sample) |
| Ranging state never reached, no distance, banner "Nearby Interaction is off" | the Nearby Interactions prompt was denied | Settings > Privacy & Security > Nearby Interactions > Beacon > on; restart the app |
| "iOS rejected the board's configuration (error -5888)" or "configuration is invalid" | the bytes after message id 0x01 are not a valid accessory configuration (wrong firmware, or the board sent a different framing) | use Qorvo's stock QANI image; confirm Qorvo's app works with the same board; read the event log line with the byte count |
| "this phone cannot run more UWB sessions at once" | the phone's concurrent-session limit | lower "Use at most" |
| "Server unreachable" | Local Network permission denied; wrong address or port; laptop firewall; phone on cellular or another Wi-Fi; a DNS name instead of an IP | Settings > Privacy & Security > Local Network > Beacon on; retype the IP from the launcher; allow port 1885 in the laptop firewall; same Wi-Fi |
| "server answered HTTP 404" or "HTTP 422" | server running without the live ingest route, or a field mismatch | update the server; send `build.txt`-style details (the JSON under "What is being sent") to the server builder |
| Distance freezes when the screen locks or the app goes to the background | expected: no background mode in this version | keep the screen open (the app keeps it awake while sharing) |

## 11. When it does not compile

**Get the full error text.** Either:

- **Terminal (best):** run the `xcodebuild` command of section 4 and send `errors.txt` (every error with file and line). Or
- **Xcode:** open the Issue navigator (Cmd-5), click the first error, select all issues (Cmd-A) and copy (Cmd-C). For the long form: Report navigator (Cmd-9) > the newest Build > right-click > **Copy Transcript for Shown Results** (or **Export**).

**Paste it to the agent** with the file names (e.g. "PuckLink.swift:412: error: ..."). Ask for the patched file(s); replace the file in `apps/ios/SandtableBeacon/` and build again. Paste warnings that say **"nearly matches optional requirement"** too: those are not errors, but they mean a delegate method will never be called.

The three most likely kinds of error, honestly:

1. **A mistyped delegate method signature.** These are written from Apple's sample and docs, not compiled. Where: the `CBPeripheralDelegate` and `NISessionDelegate` extensions at the end of `PuckLink.swift`, the `CBCentralManagerDelegate` extension at the end of `PuckManager.swift`, the `CLLocationManagerDelegate` extension at the end of `Sensors.swift`. A wrong signature is often only a **warning** ("nearly matches"), and the symptom is at run time: the app stays at one state forever (e.g. "Subscribing").
2. **An optional that needs unwrapping, or a Float/Double mismatch.** Where: `Reporter.swift` (`buildBatch`, `reportObservation`), `PuckLink.swift` (`reading(now:)`, `session(_:didUpdate:)`), the text helpers in `ContentView.swift`. The fix is one `if let` or one `Double(...)`.
3. **A main-actor or concurrency diagnostic.** The code assumes Swift 5 language mode with minimal concurrency checking and no default MainActor isolation: every callback already arrives on the main queue (Core Bluetooth with `queue: nil`, Nearby Interaction's default delegate queue, timers on the main run loop, `DispatchQueue.main.async` after every network and path-monitor callback). If Xcode reports "main actor-isolated ... cannot satisfy nonisolated requirement" or "Sendable" errors, first check Build Settings: Swift Language Version 5, Strict Concurrency Checking Minimal, Default Actor Isolation nonisolated (Xcode 26+). Where it would show: the delegate extensions above, `UIApplication.shared` and `UIDevice.current` in `Reporter.swift` and `Sensors.swift`, and the `Timer` closures in `PuckManager.swift` and `Reporter.swift`.

Also plausible: an API name the docs spell differently from what the SDK exposes (least certain: `NIError.Code.activeSessionsLimitExceeded` in `PuckLink.swift`).

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
- `uwb` and `sos` are sent only when there is a new reading since the last batch; `bearing` only when the experimental toggle is on and both an angle and a true heading exist.
- Optional fields that have no value are **left out**, not sent as `null` (e.g. `prev_eid` appears only after the random id rotated, until one batch is accepted).
- "I am out" is sent as `status: "ok"` with `safe_outside: true`.
- **Stop sharing** sends `{"device": {...}, "obs": [{"kind": "bye", "t": ..., "eid": "..."}]}`.
- The app reads `ack.text` (shown large), `ack.heard_by` and `unknown_accessories` from the answer; every field is optional.

## 13. Known limits

- Uncompiled, never run. No background mode: the person must keep the screen open (the app keeps it awake while sharing).
- The Qorvo service UUIDs and the use of Apple's message ids by Qorvo's stock firmware come from forum posts, not Qorvo documents. The key suffix rests on one forum report. How many phones one board serves at once is unknown (the app assumes one).
- Camera-assisted direction is experimental: it needs light, an unobstructed camera and a moving phone, is enabled on at most one board at a time, and the sign of the angle is undocumented.
