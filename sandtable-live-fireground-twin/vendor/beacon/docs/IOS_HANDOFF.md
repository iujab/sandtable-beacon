# iOS app handoff — build it against the real pucks

Context for the Xcode build of `apps/ios/SandtableBeacon`, verified 2026-11-15 against the real DWM3001CDK
boards. Companion to `docs/IOS_BUILD.md` (build/signing steps) and `docs/BEACON_FEED.md` (the feed contract).
Nothing here needs sponsor keys.

## What is already verified
- The two boards were flashed with Qorvo's stock **Nearby Interaction (QANI)** firmware and advertise the
  Qorvo service **2E938FD0-6A61-11ED-A1EB-0242AC120002**. The app's `PuckUUIDs.qorvoService`
  (`PuckLink.swift`) equals that UUID — **match confirmed**, so the app's scan/connect path targets the real
  firmware.
- The boards' BLE names are **`DWM3001CDK (088D9BF3)`** and **`DWM3001CDK (1BDB5E43)`**. That exact string is
  the `accessory` the phone must report in each observation (it is `peripheral.name`). The server keys puck
  positions on it; a different string comes back as `unknown_accessories` and cannot be placed.
- `Info.plist` has the required keys (checked): `NSNearbyInteractionUsageDescription`,
  `NSBluetoothAlwaysUsageDescription`, `NSLocalNetworkUsageDescription`, and ATS
  `NSAllowsLocalNetworking`/`NSAllowsArbitraryLoads` so the plain-HTTP POST to the server is allowed.
- The full ingest round-trip was proven on the live server: a two-puck UWB batch produced
  `geometry="fix"`, `sigma_m≈0.26`, `heard_by:["P1","P2"]` — i.e. the server triangulates correctly once the
  app sends real distances. The only missing piece is a real phone feeding it.

## Ingest contract (the app already matches this — do not change the server)
`POST http://<server-host>:1885/api/ingest`:
```json
{"device":{"eid":"<rotating id>","platform":"ios","model":"iPhone 14","chip":"U1","app":"0.1"},
 "obs":[{"kind":"uwb","t":<unix s>,"eid":"<eid>","accessory":"DWM3001CDK (088D9BF3)","d_m":2.94},
        {"kind":"uwb","t":<unix s>,"eid":"<eid>","accessory":"DWM3001CDK (1BDB5E43)","d_m":2.94},
        {"kind":"report","t":<unix s>,"eid":"<eid>","report":{"status":"trapped","people":1,
             "can_move":false,"safe_outside":false,"platform":"ios","app":"0.1"}}]}
```
Reply: `{"ok":true,"accepted":N,"unknown_accessories":[...],"ack":{"heard_by":[...],"geometry":"fix|candidates|ring|none","sigma_m":..,"text":".."}}`.
Test the shape with `GET /api/ingest/example` → POST it back to `/api/ingest`.

## Build, shortest path
1. Open `apps/ios/SandtableBeacon.xcodeproj` (or `xcodegen generate` from `apps/ios/project.yml` if stale).
   Sign with the owner's Apple ID team; enable Developer Mode on the iPhone 14 and 15. Errors most likely at
   the places listed in `docs/IOS_BUILD.md`.
2. In the app's Setup screen, set the server address to the UNO Q's address, port **1885** (or scan the QR on
   the board page). The app GETs `/api/directory` + `/healthz`, then POSTs `/api/ingest` every 0.5 s while SOS
   is active (`Reporter.swift`).
3. Run on both phones; connect to **both** pucks at once (the stock Qorvo app only does one — this app is the
   one that holds two `NINearbyAccessoryConfiguration` sessions). iPhone 14/15 give **distance only**, no
   bearing, against these single-antenna boards.

## Before trusting a real fix: register the real puck positions
Placeholder positions were used for the verification. Tape-measure each puck in the room frame (metres) and:
```
POST http://<server-host>:1885/api/pucks
{"room":"r310","pucks":[
  {"id":"P1","x":<real>,"y":<real>,"accessory":"DWM3001CDK (088D9BF3)"},
  {"id":"P2","x":<real>,"y":<real>,"accessory":"DWM3001CDK (1BDB5E43)"}]}
```
Put both pucks along the same wall so the twin prunes the mirror candidate to one fix (two pucks otherwise give
two mirror-image points). Physics: `docs/BEACON_FEED.md` §0.

## Server it posts to
`python -m beacon.demo --source live --lan` (or `--tailnet`), port 1885, is the live board that receives real
phones. `--source sim` + `POST /api/sim/scenario` + `/api/sim/story` instead shows the walk-through on
simulated phones.
