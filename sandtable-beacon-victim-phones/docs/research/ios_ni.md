# iOS Nearby Interaction research for Sandtable Beacon (victim phones)

Research date: 2026-11-14. Status: complete for a 20-minute pass. Apple developer documentation was read from Apple's documentation JSON for the current SDK, and Apple's public sample code zip was inspected. Some sections (2, 3, 6) were partly gathered by research sub-agents and are marked as such where the main author did not re-open the page.

Scope: iOS side of finding and ranging phones of trapped occupants with thrown UWB + BLE pucks.
Every claim carries a source URL. Items that could not be confirmed are listed under "Not confirmed".

## DWM3001CDK + iPhone 14/15: what the owner needs tonight

Update: the owner's five boards turned out to be Qorvo DWM3001CDK (nRF52833 + DW3110, on-board J-Link), not ESP32. The test phones are an iPhone 14 (U1) and an iPhone 15 (U2), and the owner has a Mac with Xcode. This is the Apple-interoperable kit recommended in "Bottom line", so no purchase is needed. qorvo.com returned HTTP 429 to every automated fetch tonight, so Qorvo facts below come from the Qorvo forum, the Qorvo product brief (hosted by FiRa) and Zephyr docs.

Plain answer:
- Flash the prebuilt Qorvo Nearby Interaction (QANI) hex through the J-Link micro-USB port (J9) with SEGGER J-Flash Lite, device nRF52833. The file reported working as of Feb 2026 is `DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex`.
- Test with the free "Qorvo Nearby Interaction" iOS app, then with your own app built on Apple's sample but scanning for `QorvoNIService`.
- Expect distance on both phones. Angle on the 14 and 15 needs Camera Assistance (moving phone, light), so it is useless in smoke.
- The stock firmware logs over SEGGER RTT, not over a USB serial port. A host reads it through J9 with J-Link RTT tools; getting ranges out over USB CDC needs a code change (swap `rtt_reporter` for `usb_reporter`).

### A. Package, download, flashing

- Package names seen:
  - "Qorvo Nearby Interaction Software package v.3.0", announced 2023-02-21 as "sources, binaries, and documentation (embedded firmware, example application, iOS application)". Users later cite 3.0.1 and 3.1.0 (binaries still labelled 3.0.0). https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390
  - A search-result snippet of the Qorvo forum lists "Qorvo_Nearby_Interaction Software package v.3.2 (03/2024)"; the page itself was not opened. https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390
  - The general SDK is "DW3_QM33 SDK" (v1.0.2, folder `DW3_QM33_SDK_1.0.2`, released 2024-09-11; users cite v1.1.1 in 2025). Users find a QANI image in it at `SDK/Binaries/DWM3001CDK/DWM3001CDK-QANI-FreeRTOS.hex`. https://forum.qorvo.com/t/new-release-of-dw3000-and-qm33-sdk-dw3-qm33-sdk-v1-0-2/19160 , https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626
- Download links:
  - DW3_QM33 SDK: `https://www.qorvo.com/products/d/da008582`. Users report that a one-time form emails a download link, and that some addresses (Gmail) fail. https://forum.qorvo.com/t/new-release-of-dw3000-and-qm33-sdk-dw3-qm33-sdk-v1-0-2/19160
  - QNI release: Qorvo staff (2026-06-11): "You can download the Qorvo Nearby Interaction release from this link ... This is available in the product page of the CDK", linking `https://www.qorvo.com/products/d/da008212`. The requester replied that it was "not working with my personal one" (no corporate email), and Qorvo (2026-06-12) said "We are aware of the issue and are trying to find a solution". https://forum.qorvo.com/t/access-request-for-qani-firmware-dwm3001cdk/25771
  - Practical advice: try da008212 first, then da008582, with a non-Gmail email. Do not sign up on the owner's behalf.
- Which file:
  - Qorvo support told a user who flashed `nRF52840DK-QANI-FreeRTOS.hex` (wrong board, app "stuck on 'Scanning for accessories'") to flash `DWM3001CDK-QANI-FreeRTOS.hex`, which then worked (Aug 2025). https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229
  - In Feb 2026 a user fixed a non-advertising board by flashing `DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex` and reading J-Link RTT on J9. The RTT showed "peer_manager_pds: Could not initialize flash storage. fds_init() returned 0x860A" and "pm_init failed because pds_init() returned NRF_ERROR_STORAGE_FULL". https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229
  - Another user found the SDK 1.1.1 QANI binary worked, while a third said the SDK QANI hex "never worked for me" until they built it themselves. https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626
- Flashing: "Use JFlashLite, USB to J9, and select NRF52833, not 52832 ... The cdk is 52833, picking the wrong device will mess things up". Watch out for power-only micro-USB cables. https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626
- J9 is the J-Link port: Zephyr's board page calls it "the bottom micro-USB port labeled as J-Link" and gives `jlink` and `nrfjprog` as supported flash runners. https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html
- J-Flash Lite is part of the SEGGER J-Link Software Pack (macOS and Windows), and `nrfjprog` also works with the on-board J-Link. Neither was checked against a Qorvo page tonight.
- Licence: not found on any page read.

### B. Stock QANI firmware at run time

- BLE service: current QANI advertises `QorvoNIService`:
  - service `2E938FD0-6A61-11ED-A1EB-0242AC120002`
  - `scCharacteristicUUID` `2E93941C-...`
  - `rxCharacteristicUUID` `2E93998A-6A61-11ED-A1EB-0242AC120002`
  - `txCharacteristicUUID` `2E939AF2-6A61-11ED-A1EB-0242AC120002`
  - Apple's unmodified sample scans for the Nordic UART UUID `6E400001-...` and will not find a current Qorvo board.
  - Sources: https://forum.qorvo.com/t/issues-with-apple-nearby-interaction-app-not-detecting-dwm3001cdk-accessory/20321 and section 7.
- Message ids: Apple's sample framing (section 7) is `0x01` accessoryConfigurationData, `0x02` accessoryUwbDidStart, `0x03` accessoryUwbDidStop, `0x0A` initialize, `0x0B` configureAndStart, `0x0C` stop. That QANI uses exactly these is consistent with forum reports but was not confirmed against Qorvo source.
- Advertising name: on the nRF52840DK build it advertised as "nRF52840DK" (2026-06-09). https://forum.qorvo.com/t/nrf52840dk-dwm3000evb-qani-hangs-after-sent-shareable-configuration-data/25749 . The DWM3001CDK name is not confirmed; scan with nRF Connect.
- Pairing: the foreground flow needs no pairing; the original Nordic UART characteristic was `SEC_OPEN`. The background flow needs LE pairing (raise to `SEC_JUSTWORKS`). https://forum.qorvo.com/t/dwm3001cdk-background-mode-ranging-nearby-interaction/12410
- What it prints:
  - "By default, ranging information is available through the JLink connector and can be viewed with JLinkRTTViewer on Mac OS" (user, 2023-03-28).
  - Qorvo staff (2023-04-12): "QNI uses a rtt_reporter task to print all the info", while the Software Preview/CLI uses a "usb_reporter" (USB CDC). Main tasks are in `Src/Apps/Src/`, and the advice was to replace rtt_reporter with usb_reporter to get output on USB.
  - https://forum.qorvo.com/t/dwm3001cdk-nearby-interaction-trying-to-get-ranging-via-usb-uart/13613
  - A community member says "QANI has no USB function", so the J20 (nRF USB) port only powers it. https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626
  - Practical setup: the host reads ranges with `JLinkRTTLogger` or `JLinkRTTViewer` over J9 USB, no code change. Exact line format and RTT channel not confirmed; no QANI log line with a distance was found.
- Update rate: an iPhone 15 user reports "NINearbyObject.distance works correctly (~63Hz)" with a DWM3001CDK. https://developer.apple.com/forums/thread/822522
- Range reported by a community member: about 10 m outdoors in free space, about 14 m near a wall, own QANI build with an iPhone 16. Face "the non-component side of the board toward the phone". https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626
- Session limits:
  - Apple: "The maximum number of concurrent Nearby Interaction sessions varies across device type and platform"; exceeding it gives `NIError.Code.activeSessionsLimitExceeded`. https://developer.apple.com/documentation/nearbyinteraction/nierror/code/activesessionslimitexceeded
  - Apple: both U1 and U2 support "multiple Nearby Interaction accessory sessions when communicating with third-party MFI chipsets". Use one `NISession` per board. https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging
  - How many iPhones one QANI board serves at once is not confirmed. Apple's background spec allows an accessory to support "multiple NISessions in parallel" (WWDC22), but QANI's limit was not found; assume 1 phone per board.

### C. iOS sample apps

- Qorvo app source: "the sdk has the firmware and the sample app source" (community reply, 2026-07-01). It is distributed inside the QNI package, not on GitHub; no Qorvo GitHub repo was found. https://forum.qorvo.com/t/inquiry-regarding-dwm3001cdk-connectivity-with-iphone/25874
- The App Store apps are "Qorvo Nearby Interaction" (https://apps.apple.com/app/id1615369084) and "Qorvo NI Background" (https://apps.apple.com/app/id6444859929).
- The Qorvo app's licence, file structure, and minimum iOS and Xcode versions were not confirmed (package not opened).
- Apple sample: https://developer.apple.com/documentation/nearbyinteraction/implementing-spatial-interactions-with-third-party-accessories (zip, no login: https://docs-assets.developer.apple.com/published/e2f511d49fda/ImplementingSpatialInteractionsWithThirdPartyAccessories.zip, zip entries dated July 2026).
  - Files: `NINearbyAccessorySample/AccessoryDemoViewController.swift` (contains `enum MessageId: UInt8` and the `NISessionDelegate` extension), `BluetoothSupport/BluetoothLECentral.swift` (`TransferService` UUIDs, `DataCommunicationChannel`), `AppDelegate.swift`, `SceneDelegate.swift`, `Info.plist`.
  - To work with QANI, change `TransferService` to the `QorvoNIService` UUIDs above (forum fix, topic 20321).
  - Exact names and ids are in section 7.

### D. iPhone 14 (U1) vs iPhone 15 (U2) with a DWM3001CDK

- Distance: works on both (section 4).
- `direction`:
  - Community: "iPhone 11–13: direction supported without camera assistance". From iPhone 14 on, "set `isCameraAssistanceEnabled = true`. Without it ... only distance is returned". https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626
  - iPhone 14 Pro / Pro Max report `supportsDirectionMeasurement` false (thread 720292). An iPhone 15 on iOS 26.3.1 with a DWM3001CDK reports false and `direction` always nil (thread 822522). One 2024 report has an iPhone 15 Pro Max on iOS 18 returning true.
  - Expect nil `direction` on both of the owner's phones, and verify on device.
- Camera Assistance: "provides horizontalAngle only after ARKit convergence (~10s), and only while the phone is moving" (thread 822522). One user had to wave the phone "in a roughly 2-foot circle". Covering the camera yields an insufficient-lighting status (forum 25626). It needs the camera, light and motion, so it does not work for a phone in a pocket or in smoke.
  - Camera Assistance uses ARKit, so the app also needs `NSCameraUsageDescription`. This is the standard ARKit camera requirement, not re-checked on an Apple page tonight.
  - Angle coverage: a community member claims convergence raises angle coverage "from about 60 degrees to near 180 degrees". https://forum.qorvo.com/t/inquiry-regarding-dwm3001cdk-connectivity-with-iphone/25874
- Background with stock QANI: QNI v3.0 "includes background mode" (13390), and Qorvo ships the "Qorvo NI Background" app. Developers still report trouble (Apple forum thread "Issue with UWB ranging on DWM3001CDK using NINearbyAccessoryConfiguration in Background mode", https://developer.apple.com/forums/thread/770963; content not read). Whether the SDK-bundled QANI hex has Apple's background NI GATT service enabled is not confirmed. The simpler route for the demo is the iOS 18.4+ Live Activity background path (section 5), which does not need the background GATT service.
- iOS 26: the direction-nil reports above are all on iOS 26.x. iOS 26 also added DL-TDOA (`supportsDLTDOAMeasurement`, section 7). iOS 27 dropped the DL-TDOA entitlement and added Bluetooth Channel Sounding.

### E. Board power

- Qorvo product brief:
  - "Two micro-USB ports connect to the J-Link and DWM3001C USB interface. The board can be powered from either of these USB ports, a Raspberry Pi interface, a battery or an external power supply."
  - "Measure module current via header on VCC trace", "nRF52833 Soc with Bluetooth 5.2", "Supports UWB RF bands 5 (6.5 GHz) and 9 (8 GHz)", "IEEE 802.15.4-2015 and IEEE 802.15.4z BPRF compliant".
  - https://www.firaconsortium.org/sites/default/files/2024-08/DWM3001CDK%20Product%20Brief.pdf
- Battery: the Quick Start Guide lists a JST SR battery connector at J12 and a loose-wire option at J1, with "no battery charger" (from a search-result snippet; the PDF itself did not load). https://www.mouser.com/datasheet/2/412/Qovro_7_7_2022_DWM3001CDK_Quick_Start_Guide-2998998.pdf . This conflicts with Zephyr's page, which says "battery connector and charging circuit". https://docs.zephyrproject.org/latest/boards/qorvo/decawave_dwm3001cdk/doc/index.html
- For standalone QANI use, power from the J20 USB port nearest the antenna (community). https://forum.qorvo.com/t/dwm3001cdk-iphone-16-demo-not-working/25626 . A Qorvo-forum reply says a power bank cut off; use LiPo through the battery connector (search snippet). https://forum.qorvo.com/t/dwm3001cdk-board-transmits-slower-on-battery-power/22115
- Module supply 2.5 to 3.6 V (user paraphrase of the datasheet). The on-board J-Link chip draws about "10-15mA (if recall it correctly)". No ranging-current figure was found. https://forum.qorvo.com/t/does-any-one-know-where-to-find-the-offical-power-requirement-of-the-dwm3001cdk/13973

Not confirmed (this section): the QANI licence; the DWM3001CDK BLE advertising name; RTT line format and channel; the number of simultaneous iPhones per board; whether the SDK QANI hex has background GATT enabled; Qorvo iOS app minimum iOS/Xcode; ranging current draw.

## 1. Apple Nearby Interaction accessory protocol

Plain answer: the accessory must run a "Nearby Interaction-enabled" UWB chipset and implement Apple's Nearby Interaction Accessory Protocol. In practice that means a vendor's closed library (Qorvo, NXP, and so on) builds an opaque "UWB configuration data" blob, sends it to the app over any data link (usually BLE), takes the phone's "shareable configuration data" back, and then runs a FiRa-style IEEE 802.15.4z HRP ranging session with the phone. Apple's spec PDFs are behind a (free) Apple ID sign-in today. The 2022 academic overview says chipset makers must be in MFi to build interoperable chips. App developers and accessory builders who buy a vendor kit do not need MFi to prototype.

Facts:
- Apple's page tells accessory makers to "Implement the Nearby Interaction accessory protocol with a Nearby Interaction-enabled UWB chipset ... Contact your UWB chipset vendor to confirm feature support". It also lists a separate "Nearby Interaction with UWB Interoperability Specification" for chipset vendors, plus a "Developer Preview for iOS 27" version of it. https://developer.apple.com/nearby-interaction/
- The spec downloads (`/file/?file=nearby-interaction-accessory-protocol-specification` and the two interoperability specs) answered with HTTP 302 to an Apple ID sign-in page (idmsa.apple.com) when fetched on 2026-11-14. Older direct PDF URLs (for example `.../download/files/Nearby-Interaction-Accessory-Protocol-Specification-R4.pdf`) now return 404. https://developer.apple.com/file/?file=nearby-interaction-accessory-protocol-specification
- Release history seen in public posts: "Nearby Interaction Accessory Protocol Specification Release R2 Developer Preview" (iOS 16 era, Qorvo forum, Oct 2022). A search engine still indexes R4 of the accessory spec and R3 of the interoperability spec under developer.apple.com/download/files/, but those URLs now 404. The current revision behind the login could not be read. https://forum.qorvo.com/t/how-to-frame-the-apple-nis-uwbconfigurationdata-field-in-the-accessory-configuration-data-per-apple-ni-doc/12863
- Flow, from the class docs: the app discovers the accessory "using a technology you choose — like CoreBluetooth, the local network, or a secure internet connection — and establishes a two-way data link". The accessory sends configuration data for `init(data:)`, formatted "according to the specification". The app runs `NISession`, and the system calls `session(_:didGenerateShareableConfigurationData:for:)`. "Over the data link, your app sends your device's configuration data to the UWB accessory, which enables the two devices to start receiving location updates." https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration
- New in the iOS 27 SDK: `NINearbyAccessoryConfiguration` "supports ranging with accessories using either Ultra Wideband (UWB) or Bluetooth Channel Sounding technology". Channel Sounding needs no config-data exchange, but the accessory must be paired through AccessorySetupKit. This is distance only, over Bluetooth 6.0. https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration and https://developer.apple.com/documentation/nearbyinteraction/nideviceCapability/supportsbluetoothchannelsounding
- Apple's interoperability spec (R3, as quoted in search-result snippets) "defines how a UWB system comprised of a FiRa compliant UWB PHY and MAC, along with Apple Nearby Interaction (NI) works together", on IEEE 802.15.4z-2020, FiRa PHY/MAC v1.1 or later. Only the snippet was seen; the PDF itself now 404s. https://developer.apple.com/download/files/Nearby-Interaction-with-UWB-Interoperability-Specification-R3.pdf
- Coppens et al. (2022 overview of UWB standards): "Companies who want to develop a UWB solution that is interoperable with the Apple UWB chip need to be part of their Made-For-iPhone (MFi) program and follow this protocol". It calls the accessory protocol "a lightweight, transport-agnostic application-level protocol". Its steps: discovery over non-UWB (BLE, LAN, cloud), then the accessory sends "Accessory Configuration Data" (containing "UWB Configuration Data" provided by the chipset vendor), then the phone returns "Apple Shareable Configuration Data", then ranging starts. It lists U1 as IEEE 802.15.4z HRP with STS support, and reports that "for the Apple U1 chip, interoperability with the NXP SR040/SR150 and Qorvo DW3000 is available". https://arxiv.org/pdf/2202.02190
- The same paper says all UWB chips in its survey support channel 5, and all except the DW1000 also support channel 9. It does not say which channel Apple NI uses. https://arxiv.org/pdf/2202.02190
- The UWB config blob is undocumented even to Qorvo forum users. One user (Oct 2022) says the configs "are not documented, but they definitely are not dwt_config_t", and that UWBConfigData is "mostly" constant, "with only the last two bytes changing according to the device UWB MAC short address". https://forum.qorvo.com/t/how-to-frame-the-apple-nis-uwbconfigurationdata-field-in-the-accessory-configuration-data-per-apple-ni-doc/12863
- In practice, "U1-compatible" means two things. iPhone 15+ (second-generation UWB) "can communicate with first-generation devices and Made for iPhone (MFI)-certified third-party chipsets". Third-party sessions always run at first-generation performance; extended distance (EDM) is Apple-to-Apple only. https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging
- Apple: "The framework doesn't implement secure ranging, so don't rely on Nearby Interaction to provide users with secure access". https://developer.apple.com/documentation/nearbyinteraction/ninearbyobject/distance-676dm

Not confirmed:
- The exact UWB channel(s) Apple NI accessory sessions use (5 vs 9), the STS mode (static STS vs dynamic or provisioned), and the ranging scheme (DS-TWR, roles). These are in the gated interoperability spec; no public page I opened states them.
- Whether downloading the accessory spec today needs only a free Apple ID or a paid or MFi account. The redirect goes to a generic Apple ID sign-in, and I did not sign in.
- Which revision of the accessory spec is current (the R4 PDF existed at some point; the current gated file was not read).

## 2. Chips, modules and dev kits with a working Apple-interoperable stack

Plain answer: today only Qorvo and NXP (including Murata modules built on NXP) have confirmed working iPhone NI stacks. The cheapest and most-used path is the Qorvo DWM3001CDK: a DWM3001C module, which is a DW3110 UWB chip plus an nRF52833 (Cortex-M4 with BLE). It runs Qorvo's "Qorvo Nearby Interaction" (QNI/QANI) firmware, sold at DigiKey for USD 29.50 with 1,100 in stock on 2026-11-14. Qorvo's stack ships as source for the example application but as precompiled Cortex-M4 libraries for the UWB driver, NIQ (the NI config library) and the MAC. NXP's route is SR150 kits with a QN9090 BLE host, at USD 180 and up. ST has no confirmed Apple NI support. Estimote now sells DL-TDOA anchors for iOS 27, not two-way-ranging accessories. Most of section 2 was gathered by a research sub-agent. The main author re-checked the DigiKey DWM3001CDK price and stock and the QNI v3.0 forum thread; qorvo.com itself blocked automated fetches (HTTP 429), and Mouser timed out.

Facts:
- Qorvo DWM3001CDK: USD 29.50 at qty 1, 1,100 in stock, status Active, manufacturer lead time 37 weeks, described as "DEV KIT FOR DWM3001C UWB XCVR MO". https://www.digikey.com/en/products/detail/qorvo/DWM3001CDK/24367348
- Qorvo Nearby Interaction Software package v3.0 (announced 2023-02-21): "sources, binaries, and documentation (embedded firmware, example application, iOS application)". It runs on the DWM3001CDK (nRF52833 + DW3000-family) and on nRF52832/833/840 boards with a DWM3000 shield, includes background mode and Camera Assistance demos, and is downloaded from the "Evaluation Tools" tab of the product page. Users report a later 3.1.0 or 3.0.1 package. https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390
- The binary-only libraries users list in that package include `libdwt_uwb_driver-m4-hfp-6.0.14.a`, `libniq-m4-hfp-1.1.0.0.a` and `libembedded_mac_arm-m4-hfp_R11.9.2.a`, all Cortex-M4 hard-float. https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390?page=2
- A ready-made image `DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex` is reported working with iPhone as of Feb 2026. https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229
- Access is disputed. One July 2026 user (not staff) says to "Request access to the QANI sdk"; others downloaded it directly from the product page. https://forum.qorvo.com/t/inquiry-regarding-dwm3001cdk-connectivity-with-iphone/25874
- Qorvo's iOS apps are free on the App Store: "Qorvo Nearby Interaction" ("allows developers to easily evaluate Qorvo Ultra-Wideband technology using approved development kits leveraging the Apple NI framework") and "Qorvo NI Background". https://apps.apple.com/app/id1615369084 , https://apps.apple.com/app/id6444859929
- Qorvo DWM3001C module (DWM3001CTR13): USD 59.37 at qty 1 (cut tape), 6,981 in stock. https://www.digikey.com/en/products/detail/qorvo/DWM3001CTR13/24367353
- Qorvo QM33120WDK1 (nRF52840 + QM33110W/QM33120W): USD 500.00, 113 in stock. Apple NI support was seen only in a search snippet. https://www.digikey.com/en/products/detail/qorvo/QM33120WDK1/24611236
- Qorvo QM35825: a forum reply (2025-12-02) says Apple NI DL-TDOA is "indeed this is possible"; contact sales, as there is no public firmware. https://forum.qorvo.com/t/qm35825dk-and-apple-interaction-framework/24409
- NXP SR150: "Apple MFi certification and interoperability with Apple U1 chip with firmware (v.3.14.0)". No public software is listed, and the datasheet needs an account. https://nxp.com/products/SR150
- NXP lists Apple-approved kits: MobileKnowledge "Mobile Edition" (SR150 + QN9090, pre-flashed with the Apple NI example) and Sunway UWB EVK-2. No prices are given, and secure docs need "request access rights". https://www.nxp.com/pages/uwb-development-kits-that-interoperate-with-apple-u1:UWB_DEV_KITS
- Murata Type 2BP EVK (LBUA0VG2BP-EVK-P; SR150 + QN9090 + SE051W): USD 179.99, 328 in stock. https://www.digikey.com/en/products/detail/murata-electronics/LBUA0VG2BP-EVK-P/16680947
- Murata Type 2DK EVK (LBUA2ZZ2DK-EVK; SR040 + QN9090): USD 71.30, 95 in stock. The page does not claim Apple support. https://www.digikey.com/en/products/detail/murata-electronics/LBUA2ZZ2DK-EVK/21326420
- Forthink esp32-uwb-zero (ESP32-S3 + NXP NCJ29D5) claims an iPhone demo through the Qorvo NI app with a closed binary stack. It is a vendor claim. https://github.com/forthink-xyz/esp32-uwb-zero
- Estimote's current offer is a "UWB Downlink Kit" of four DL-TDOA anchors for the iOS 27 SDK; two-way ranging needs "dedicated firmware". Price only by email. https://estimote.com/dl-tdoa
- STMicro: no Apple NI support found; ST64UWB material mentions FiRa, CCC and Aliro, but not Apple. Treat as no.
- Apple names no chipsets or kits; it tells accessory makers to contact their UWB chipset vendor. https://developer.apple.com/nearby-interaction/

Not confirmed:
- Mouser and Qorvo-store prices for the DWM3001CDK (Mouser timed out, and qorvo.com returned HTTP 429 to the fetcher).
- The QNI license text, and whether the download is ungated today.
- Prices for the DWM3000EVB and the bare DW3110/DW3120/DW3210/DW3220 parts. A DigiKey lead time of 37 weeks means stock on hand (1,100) is what matters for this weekend.
- Whether the Murata Type 2DK (SR040) alone is Apple-approved.

## 3. Can ESP32 + DW3000 range with an iPhone?

Plain answer: NO with anything public today. Treat it as "does not work" for the hackathon. The DW3000 radio is physically capable, since the same die is in Qorvo's Apple-interoperable kits. What is missing is software: (a) Apple's UWB configuration blob, which Qorvo generates with its closed "NIQ" library; (b) the FiRa session or MAC layer, including STS IV and key setup, which is a closed "uwbstack" library; (c) both ship only as precompiled ARM Cortex-M4/M33 static libraries, with no Xtensa (ESP32) build, and source needs an NDA. One forum user claims to do it privately with reverse-engineered code, but nothing is published. Makerfabs' "Interoperable with Apple U1" line refers to the DW3000 chip family, and Makerfabs itself says it has no files or demos for it.

Facts:
- Qorvo staff (forum, 2023-03-08): "UWBConfigData is defined by Apple and is not open, the way to generate it is by using the NIQ library", specifically "niq_populate_accessory_uwb_config_data()". On 2023-03-28: "Qorvo only has these libraries available for ARM Cortex M4 and M33." A user in 2024 notes "I only find library for m4". https://forum.qorvo.com/t/apple-ni-configuration-data-frame/10796?page=2
- A user in the same thread (2025-02-11) claims it is possible to exchange NI parameters over BLE between iOS and an ESP32 and to "use ESP32/DW3000 for ranging directly with the iPhone (i'm doing it)". In May 2024 the same user posted a reverse-engineered struct with a `uint8_t sts_iv[6]` field, "later used for configuring the FIRA session parameters". No code is released and there is no independent confirmation. https://forum.qorvo.com/t/apple-ni-configuration-data-frame/10796?page=2
- An ESP32 + DW3000 attempt got "Failed to create NINearbyAccessoryConfiguration for 'ESP32'" (error -5888). The reply was to get "niq as well uwbstack library" from Qorvo, and that the reference app "is build for Cortex-M". https://forum.qorvo.com/t/apple-nearby-interaction-invalid-configuration/13473
- The official DW3000 driver is "only available as a pre-compiled library that's been built for ARM processors". https://forum.qorvo.com/t/esp32-dw3000-interaction-with-android-and-ios/14518
- A request for a DW3xxx Xtensa static library got no Qorvo answer. https://forum.qorvo.com/t/dw3xxx-static-library-for-xtensa/12668 . A request for library source to rebuild for ESP32 got the answer "You'll need an NDA". https://forum.qorvo.com/t/dwm33120-mcu-esp32-not-cortex-m4or33-how-to-get-library-source-code-and-rebuild/16955
- The Makerfabs ESP32-DW3000 Arduino library "does not have FiRa implementations nor can it be used for Nearby Interaction with Apple's U1". https://forum.qorvo.com/t/interfacing-dwm3000evb-with-esp32-or-arduino/12206 . There are "no libraries that enable using Makerfab's ESP32-DW3000 for this". https://forum.qorvo.com/t/dw3000evb-interaction-with-apple-u1/13573
- The Makerfabs product page and wiki say "Interoperable with Apple U1 chip", but the product page also says "Makerfabs now do not have related files/demos to support this". https://makerfabs.com/esp32-uwb-dw3000.html , https://wiki.makerfabs.com/ESP32_DW3000_UWB.html
- The only public ESP32 + DW3000 Apple NI attempt found has this README status: "Accessory can be connected to by the demo app, but sends an invalid UWB config." https://github.com/maa-x/ESP32-DW3000-AppleNearbyInteraction
- br101/libdeca is an open DW3000 driver with ESP-IDF support. It does plain two-way ranging, with no FiRa or Apple NI (per the sub-agent's read of the repo). https://github.com/br101/libdeca
- The one ESP32 board claiming a working iPhone demo (through the Qorvo NI app, up to about 9 m) uses an NXP NCJ29D5 UWB chip, not a DW3000, with a closed binary stack (`Forthink.a`). https://github.com/forthink-xyz/esp32-uwb-zero
- The Qorvo QNI package v3.0 (Feb 2023) targets the DWM3001CDK (nRF52833 + DW3000) and nRF52832/833/840 plus DWM3000 shields. Users report that it links prebuilt libraries such as `libdwt_uwb_driver-m4-hfp-6.0.14.a` and a binary-only embedded MAC library. https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390
- Ghost Peak (ETH Zurich, USENIX Security 2022) attacked U1 sessions by listening to and injecting into existing sessions. It describes a U1 "compatibility mode" for third parties whose parameters are "only available to MFi program members"; for that mode the authors used vendor kits, not a homemade accessory stack. https://ar5iv.labs.arxiv.org/html/2111.05313
- MaUWB ESP32-S3: its STM32 AT-command firmware offers only Makerfabs tag/anchor ranging. Its wiki says nothing about Apple or FiRa (per the sub-agent's read). https://wiki.makerfabs.com/

Verdict for the owner's ESP32 + DW3000 boards: does not work with an iPhone using any available software; it is possible in principle with heavy reverse engineering. They remain useful as UWB puck-to-puck ranging anchors and as BLE scanners or advertisers.

Not confirmed:
- The private ESP32/DW3000 success claim (a single forum user).
- Whether Qorvo's newer QM33 SDK adds any non-ARM build. No source says so, and every source found says ARM-only.
- Whether a public Zephyr FiRa MAC exists. None was found. The Qorvo NI package has been run on nRF5340 under Zephyr, but with the same binary libraries (QNI v3.0 thread above).
- Whether the MaUWB STM32 firmware source is public.

## 4. iPhone models, U1 vs U2, and what an iPhone 14 supports

Plain answer: iPhone 11 to 14 have the first-generation chip (U1). iPhone 15 and later have the second-generation chip, except iPhone 16e and 17e, which have no UWB; iPhone Air and "iPhone Duo" are second-generation. With a third-party accessory, an iPhone 14 should give reliable distance (meters, around 60 Hz reported). Instantaneous `direction` is very likely nil: multiple developers report that iPhone 14-family devices return `supportsDirectionMeasurement == false`, and Apple has not explained why. Camera Assistance (iOS 16+) can add `horizontalAngle` and `verticalDirectionEstimate` after ARKit converges, which needs the phone moving with the camera unobstructed. That is useless for a phone in a trapped person's pocket. U2 phones get no benefit with accessories: third-party sessions run "at the performance available on the first-generation chip", and EDM (long range) is Apple-to-Apple only.

Facts:
- Apple support (published 2026-11-09):
  - Second-generation UWB: "iPhone Duo", "iPhone 15 models and later, excluding iPhone 17e and iPhone 16e", "iPhone Air", Apple Watch Series 9+ and Ultra 2+, and AirTag (2nd generation).
  - First-generation: "iPhone 14 models", "iPhone 13 models", "iPhone 12 models", "iPhone 11 models".
  - UWB is unavailable in Armenia, Azerbaijan, Belarus, Kazakhstan, Kyrgyzstan, Russia, Tajikistan, Turkmenistan, Ukraine and Uzbekistan. Japan gets previous-generation performance.
  - https://support.apple.com/109512
- Apple: EDM "is available between devices with second-generation Ultra Wideband (UWB) chips ... both sides have to have the second-generation chip". Second-generation devices "can communicate with first-generation devices and Made for iPhone (MFI)-certified third-party chipsets. However, when communicating with first-generation devices, second-generation devices operate at the performance available on the first-generation chip". Apple's sample treats EDM measurements under 50 m as "good". https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging
- `NINearbyPeerConfiguration.isExtendedDistanceMeasurementEnabled` exists only on the peer (Apple-to-Apple) configuration. `NINearbyAccessoryConfiguration` has no EDM property. https://developer.apple.com/documentation/nearbyinteraction/ninearbypeerconfiguration/isextendeddistancemeasurementenabled and https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration
- Apple on `direction`: "This property is `nil` when direction is unavailable ... On iPhone, it's possible to recieve direction for nearby third-party accessories in sessions that enable isCameraAssistanceEnabled." It is a unit vector (x right, y up, z negative away from the user out of the back). https://developer.apple.com/documentation/nearbyinteraction/ninearbyobject/direction-4qh5w
- Apple on `horizontalAngle`: "The framework sets a value of this property when isCameraAssistanceEnabled is true." https://developer.apple.com/documentation/nearbyinteraction/ninearbyobject/horizontalangle-hsg
- Apple, field of view: works best "Within 9 meters of each other", "In portrait orientation", "Facing each other with their back camera". Direction is only available within "the narrow line of sight illustrated by the conic region". "Camera Assistance provides a direction outside of the narrow line of sight only after first encountering the peer device once within the narrow line of sight." https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session
- WWDC22: "The field of view for direction information corresponds to a cone projecting from the rear of the device". The ARKit trajectory makes "distance, direction, horizontal angle, and vertical direction estimate ... available in more scenarios". `verticalDirectionEstimate` can be same, above, below or aboveOrBelow ("use it to guide the user between floor levels"). https://developer.apple.com/videos/play/wwdc2022/10008/
- Apple: "The maximum detectable range depends on environmental factors ... solid obstructions to the device's line of sight constrict the range." Apple gives no accessory range figure. https://developer.apple.com/documentation/nearbyinteraction/ninearbyobject/distance-676dm
- Developer reports on the iPhone 14 family:
  - "direction is not working in Iphone 14, while it works fine in Iphone 11,12,13". https://developer.apple.com/forums/thread/724997
  - iPhone 14 Pro Max (iOS 16.1.1) and iPhone 14 Pro (iOS 26.2) report `supportsDirectionMeasurement` false; iPhone 12 and 13 report true; the iPhone 16 Pro, 16 Pro Max and 17 reports are false. https://developer.apple.com/forums/thread/720292
  - None of these threads has an Apple staff answer.
- U2 with a Qorvo DWM3001CDK: on an iPhone 15 (iOS 26.3.1), "direction always nil", `supportsDirectionMeasurement = false`. "Camera Assistance ... provides horizontalAngle only after ARKit convergence (~10s), and only while the phone is moving". "NINearbyObject.distance works correctly (~63Hz)". Same issue reported on an iPhone 17 Pro. https://developer.apple.com/forums/thread/822522
- U2 with Murata SR040/SR150: the same missing-direction issue is reported (thread title only was read). https://www.developer.apple.com/forums/thread/802948

Not confirmed:
- Apple-stated range for accessory (non-EDM) sessions. Apple's only number is "within 9 meters" as a best-performance guideline; vendor numbers are in section 2.
- Whether the base iPhone 14 (not Pro) reports `supportsDirectionMeasurement == false`. One forum post says "iPhone 14"; the others are Pro models. Check with `NISession.deviceCapabilities` on the owner's phone.
- Distance accuracy. Apple publishes none. Academic measurements exist (SEEMOO, https://ar5iv.labs.arxiv.org/html/2303.11220), but their figures were not extracted here.

## 5. Background behaviour (Nearby Interaction and Core Bluetooth)

Plain answer: there are three ways to keep ranging when the occupant app is not in the foreground.
- (a) The classic iOS 16 path. The accessory is LE-paired and connected, implements Apple's Nearby Interaction GATT service, and the app uses `init(accessoryData:bluetoothPeerIdentifier:)` with `UIBackgroundModes` `nearby-interaction` (plus `bluetooth-central` to keep the BLE link). The session keeps running, but the app gets no `didUpdate` callbacks in the background: only the accessory receives the measurements, and it must act on them (for example, report distance back over BLE or to the command post).
- (b) The iOS 18.4+ path. If the app starts a Live Activity as it goes to the background, it "can continue ranging in the background with any supported peer or accessory". Apple's text does not repeat the accessory-only caveat for this path.
- (c) DL-TDOA. On iOS 27.2+, a DL-TDOA session can start on a background relaunch (for example from iBeacon region entry), runs for at most 30 s unless an anchor is found, and needs Always location. It requires DL-TDOA anchor firmware on the pucks.

Core Bluetooth: a backgrounded peripheral app advertises no local name, all its service UUIDs go to the Apple "overflow" area (only iOS scanners looking for that UUID see them), and service or manufacturer data cannot be advertised at all. A central can scan in the background only for explicit service UUIDs, with duplicates coalesced.

Facts (Nearby Interaction):
- "While your app is in the foreground, it can freely use Nearby Interaction to range with a peer device or third-party accessory. When the app moves to the background, it can continue ranging only if the peer or accessory is Bluetooth Low Energy (LE)-paired and connected." "In iOS 18.4 and later, your app can continue ranging in the background with any supported peer or accessory if the app starts a Live Activity as it goes to the background." "In iOS 27.2 and later, DL-TDOA sessions can begin when the system relaunches your app in the background." Capability: "add the Background Modes capability, then select Uses Nearby Interaction". https://developer.apple.com/documentation/nearbyinteraction
- Without a background path: "If a user backgrounds the app, Nearby Interaction suspends the session and notifies the delegate through sessionWasSuspended(_:)". "A suspended session won't resume on its own. To resume the session, call run(_:) again". If backgrounded too long, the session is invalidated. https://developer.apple.com/documentation/nearbyinteraction/initiating-and-maintaining-a-session and https://developer.apple.com/documentation/nearbyinteraction/nisessiondelegate/sessionwassuspended(_:)
- `init(accessoryData:bluetoothPeerIdentifier:)` (iOS 16): "Use this initializer when your app interacts with a Bluetooth accessory that's paired to the device to enable interaction while your app is in the background". The steps: the accessory implements the Bluetooth requirements in the spec, and the app "connects and pairs to the accessory using CoreBluetooth" and passes "the accessory's Bluetooth identifier". https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration
- WWDC22 (session 10008), on requirements:
  - "Accessory background sessions require that the accessory is LE-paired to the user's iPhone. Your app is responsible for triggering this process."
  - "Accessory background sessions also require a Bluetooth connection to your accessory. Your app must be able to form this connection even when it's backgrounded."
  - "it must implement the new Nearby Interaction GATT service. The Nearby Interaction service contains a single encrypted characteristic called Accessory Configuration Data ... Your app cannot read from this characteristic directly."
  - Info.plist: "the Nearby Interaction string in the UIBackgroundModes array ... ensure you have 'Uses Bluetooth LE accessories' enabled".
  - https://developer.apple.com/videos/play/wwdc2022/10008/
- WWDC22, on what runs: "When your application is in the background, the NISession will continue to run and will not be suspended, so Ultra Wideband measurements are available on the accessory." "Your application will not receive runtime, and you will not receive didUpdateNearbyObject delegate callbacks until your application returns to the foreground." "Process these measurements directly on your accessory". https://developer.apple.com/videos/play/wwdc2022/10008/
- `UIBackgroundModes` possible values (Apple reference) include `bluetooth-central`, `bluetooth-peripheral` and `nearby-interaction`. https://developer.apple.com/documentation/bundleresources/information-property-list/uibackgroundmodes
- NI GATT service UUID `48fe3e40-0817-4bb2-8633-3073689c2dba` and characteristic `95e8d9d5-d8ef-4721-9a4e-807375f53328`, as quoted from spec R2 by forum users. Implementers report: pairing triggered by raising characteristic security to Just Works; writing the Accessory Configuration Message "skipping the first byte (message type)" (36 bytes becomes 35); and BLE advertising payload limits when adding the service. https://forum.qorvo.com/t/dwm3001cdk-background-mode-ranging-nearby-interaction/12410
- Qorvo QNI v3.0 firmware includes background mode, and Qorvo ships a separate "Qorvo NI Background" iOS app. https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390 , https://apps.apple.com/app/id6444859929
- DL-TDOA: "The system allows the DL-TDOA session to run in the background for up to 30 seconds ... The background session requires Always location authorization". Since iOS 27, "DL-TDOA no longer requires an entitlement". Anchors comply "with the IEEE 802.15.4z standard"; discovery is over BLE (default) or Wi-Fi. https://developer.apple.com/documentation/nearbyinteraction/dl-tdoa-ranging
- Direction in the background: no Apple source says it is available. The accessory receives measurements, and Apple's accessory-side measurement content is in the gated spec.

Facts (Core Bluetooth):
- `startAdvertising`: "The peripheral manager only supports two keys: CBAdvertisementDataLocalNameKey and CBAdvertisementDataServiceUUIDsKey" (so no service data and no manufacturer data, even in the foreground). "While your app is in the background, the local name isn't advertised and all service UUIDs are in the overflow area." In the foreground: up to 28 bytes, plus 10 bytes of scan response for the local name only. https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager/startadvertising(_:)
- "All service UUIDs ... are placed in a special 'overflow' area; they can be discovered only by an iOS device that is explicitly scanning for them." "If all apps that are advertising are in the background, the frequency at which your peripheral device sends advertising packets may decrease." https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html
- Overflow format: per Stack Overflow answers (not Apple), it is Apple manufacturer data (company ID 0x004C) carrying a hashed bitmask of the service UUIDs, so collisions are possible. A non-Apple scanner (ESP32) cannot read the UUID, but can match a known bitmask or connect and enumerate services. https://kiwix.lema.org/stackoverflow.com_en_all_2023-11/questions/58864679/ios-background-ble-advertising-not-detectable-by-android
- Background scanning: "your app must explicitly scan for one or more services by specifying them in the serviceUUIDs parameter. The CBCentralManager scan option has no effect while scanning in the background." https://developer.apple.com/documentation/corebluetooth/cbcentralmanager/scanforperipherals(withservices:options:)
- Background central: `CBCentralManagerScanOptionAllowDuplicatesKey` "is ignored, and multiple discoveries ... are coalesced into a single discovery event"; the scan interval increases. https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html
- State restoration: opt in with `CBCentralManagerOptionRestoreIdentifierKey`. The system tracks scanned services, pending and connected peripherals, and subscribed characteristics, then relaunches the app into the background and calls `centralManager(_:willRestoreState:)` first. Same source as above.
- Runtime: "Upon being woken up, an app has around 10 seconds to complete a task ... Apps that spend too much time executing in the background can be throttled back by the system or killed." With `bluetooth-central`, "the system wakes up your app when any of the CBCentralManagerDelegate or CBPeripheralDelegate delegate methods are invoked". So a screen-off app with a live BLE connection runs in short bursts per BLE event (for example each notification from the puck), indefinitely, as long as events keep arriving and it is not killed. Same source as above.

Not confirmed:
- Whether the iOS 18.4 Live Activity path delivers `didUpdate` callbacks to the app while the screen is off. The doc says "continue ranging" but does not spell out callback delivery. Test on device.
- Whether direction or angle reaches the accessory in a background session.
- Exact background advertising interval of a backgrounded iOS peripheral app. Apple says only "may decrease".
- Whether the owner's iPhone 14 supports DL-TDOA (`supportsDLTDOAMeasurement`). Apple lists no device models.

## 6. Cold detection of phones without an app

Plain answer: UWB ranging to an iPhone whose owner installed nothing is not possible with any public method. Every UWB mode (third-party NI, Find My people Precision Finding, AirTag, car key) is set up by Apple software with consent or pairing, and no source shows the U1/U2 answering arbitrary third-party polls. Passively, iPhones constantly emit Apple Continuity BLE advertisements (Nearby Info) with rotating random addresses. A BLE scanner can therefore honestly report an approximate count of Apple Continuity emitters in range over a time window, with known over- and under-count causes. It cannot give identity, a person count or proof of life. The section 6 sources were gathered by a research sub-agent; the main author spot-checked the Apple NI overview but not every paper.

Facts:
- Nearby Interaction requires that "devices in physical proximity run an app and share their position and device tokens". For accessories, the app must "establish a data link with the accessory, receive its configuration data". https://developer.apple.com/documentation/nearbyinteraction
- Precision Finding between people requires that both "share your location with each other, both have an iPhone 15, and are near each other". https://support.apple.com/guide/personal-safety/ips05ede4573/web
- AirTag Precision Finding is phone-finds-tag, not the reverse. https://support.apple.com/109021
- SEEMOO (Black Hat USA 2021, "What's Really Inside Apple's U1 Chip"): the U1 "only supports interaction with other Apple devices". https://infocondb.org/con/black-hat/black-hat-usa-2021/wibbly-wobbly-timey-wimey-whats-really-inside-apples-u1-chip
- Ghost Peak lists U1 modes (NI, Find My ranging, HomePod handoff, a third-party "compatibility mode"), all session-initiated by Apple software. https://ar5iv.labs.arxiv.org/html/2111.05313
- "Evil Never Sleeps": when the phone is off, "the UWB chip is sleeping". The Bluetooth chip wakes it only for Digital Car Key secure ranging. https://ar5iv.labs.arxiv.org/html/2205.06114
- Celosia and Cunche (PETS 2020): "An iPhone will always include a Nearby Info message". Nearby Info is "continuously transmitted by iPhones, iPads, MacBooks and Watches regardless of the device status (locked or active)". The 15-minute maximum address lifetime was respected, but in power-saving mode some addresses persisted ">4 days". AirDrop messages appear only during an AirDrop attempt. https://petsymposium.org/popets/2020/popets-2020-0003.pdf
- Martin et al. ("Handoff All Your Privacy", PETS 2019): "the random BLE MAC address rotates every 15 minutes". Nearby messages on iOS 12 "never stop transmitting ... over 200 frames per minute". Message contents leak device type and OS version, so phones can probably be separated from Macs and Watches. https://arxiv.org/pdf/1904.10600
- Find My offline finding: lost or offline devices send "one BLE advertisement every two seconds when they lose Internet connectivity", with key and address rotating every 15 minutes. https://arxiv.org/pdf/2103.02282
- The furiousMAC Continuity dissector lists the message types (Nearby Info, Nearby Action, Handoff, AirDrop, offline finding and so on), but gives no rates. https://github.com/furiousMAC/continuity
- Counting distinct randomized addresses gives "inaccurate crowd counting"; trends are usable, absolute counts are not. https://arxiv.org/html/2402.09158v1
- What a passive scanner can honestly say:
  - It can report "N distinct Apple Continuity emitters (filtered to iPhone-like Nearby Info where possible) heard in the last T minutes, RSSI trend per address".
  - Over-count: address rotation inside the window (one phone becomes two) and old and new addresses briefly overlapping; iPads, Macs, Watches and AirPods also emit Continuity; and one person often carries several devices.
  - Under-count: powered-off phones, phones with Bluetooth off, phones out of range through fire walls or concrete, and messages that depend on activity.
  - Identity: none. The addresses are random, and Apple does not expose the phone owner.

Not confirmed:
- Current (iOS 17 to 27) Nearby Info rates with the screen off or in Low Power Mode. The published measurements are from the iOS 12 to 14 era.
- Any published study giving measured over- or under-count percentages for Apple Continuity based counting.
- Whether Find My offline beacons continue with the phone powered off ("Evil Never Sleeps" suggests the Bluetooth chip stays partly active; the exact text was not pulled).

## 7. Swift API surface for the occupant app

Plain answer: below are the current-SDK signatures, read from Apple's documentation JSON on 2026-11-14, and the exact UUIDs and message ids from Apple's public sample zip and Qorvo's forum. Apple's sample uses the Nordic UART Service UUIDs and a one-byte message-id prefix. Current Qorvo QNI firmware advertises a different, Qorvo-specific service (`2E938FD0-...`), so Apple's unmodified sample will not find a current Qorvo board.

Facts (API, from https://developer.apple.com/documentation/nearbyinteraction and its subpages):
- `class NISession`: `class var deviceCapabilities: any NIDeviceCapability`, `var discoveryToken: NIDiscoveryToken?`, `func run(_ configuration: NIConfiguration)`, `var configuration: NIConfiguration?`, `var delegate: (any NISessionDelegate)?`, `var delegateQueue`, `func pause()`, `func invalidate()`, `func setARSession(_:)`, `func worldTransform(for: NINearbyObject) -> simd_float4x4?`, `func updateMotionState(_: NIMotionActivityState, forObjectWithToken: NIDiscoveryToken)`. `class var isSupported: Bool` is deprecated. https://developer.apple.com/documentation/nearbyinteraction/nisession
- `class NINearbyAccessoryConfiguration`: `init(data: Data) throws` (iOS 15), `init(accessoryData: Data, bluetoothPeerIdentifier: UUID) throws` (iOS 16, for background), `init(bluetoothChannelSoundingIdentifier: UUID, previousBluetoothIdentifier: UUID?)` (iOS 27), `var accessoryDiscoveryToken: NIDiscoveryToken`, `var isCameraAssistanceEnabled: Bool` (iOS 16, default false). https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration
- `protocol NISessionDelegate`: `sessionDidStartRunning(_:)`, `session(_:didUpdate: [NINearbyObject])`, `session(_:didGenerateShareableConfigurationData: Data, for: NINearbyObject)`, `session(_:didRemove: [NINearbyObject], reason: NINearbyObject.RemovalReason)` (reasons `.peerEnded`, `.timeout`), `sessionWasSuspended(_:)`, `sessionSuspensionEnded(_:)`, `session(_:didInvalidateWith: any Error)`, `session(_:didUpdateAlgorithmConvergence: NIAlgorithmConvergence, for: NINearbyObject?)`, `session(_:didUpdateDLTDOA: [NIDLTDOAMeasurement])`. https://developer.apple.com/documentation/nearbyinteraction/nisessiondelegate
- `NINearbyObject`: `discoveryToken`, `distance: Float?` (meters), `direction: simd_float3?`, `horizontalAngle: Float?` (radians, iOS 16), `verticalDirectionEstimate: NINearbyObject.VerticalDirectionEstimate`. https://developer.apple.com/documentation/nearbyinteraction/ninearbyobject
- `protocol NIDeviceCapability`: `supportsPreciseDistanceMeasurement`, `supportsDirectionMeasurement`, `supportsCameraAssistance`, `supportsExtendedDistanceMeasurement` (iOS 17), `supportsDLTDOAMeasurement` (iOS 26), `supportsBluetoothChannelSounding` (iOS 27). https://developer.apple.com/documentation/nearbyinteraction/nideviceCapability
- `NIDLTDOAConfiguration(networkIdentifier: Int)` / `init(networkIdentifier:discoveryMethod:)` (iOS 26). It needs Location authorization, and Apple says that "In iOS 27 and later, DL-TDOA no longer requires an entitlement". https://developer.apple.com/documentation/nearbyinteraction/dl-tdoa-ranging
- Info.plist keys: `NSNearbyInteractionUsageDescription` (iOS 15+; "the framework presents a prompt that displays the value of this key"), `NSBluetoothAlwaysUsageDescription` ("required if your app uses the device's Bluetooth interface"), `UIBackgroundModes`, whose documented values include `nearby-interaction`, `bluetooth-central` and `bluetooth-peripheral`. https://developer.apple.com/documentation/bundleresources/information-property-list/nsnearbyinteractionusagedescription , https://developer.apple.com/documentation/bundleresources/information-property-list/nsbluetoothalwaysusagedescription , https://developer.apple.com/documentation/bundleresources/information-property-list/uibackgroundmodes
- Apple's public sample zip (ImplementingSpatialInteractionsWithThirdPartyAccessories.zip, linked from the sample page, no login) uses exactly:
  - `static let serviceUUID = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")`
  - `static let rxCharacteristicUUID = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")`
  - `static let txCharacteristicUUID = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")`
  - `enum MessageId: UInt8 { case accessoryConfigurationData = 0x1; case accessoryUwbDidStart = 0x2; case accessoryUwbDidStop = 0x3; case initialize = 0xA; case configureAndStart = 0xB; case stop = 0xC }`. The first three are "Messages from the accessory" and the last three "Messages to the accessory".
  - The app strips the first byte (`data.advanced(by: 1)`) before `NINearbyAccessoryConfiguration(data:)`. It sends `[0x0B] + shareableConfigurationData` to the accessory. Its Info.plist sets only `NSNearbyInteractionUsageDescription` and `NSBluetoothAlwaysUsageDescription`, with no background modes.
  - Source: https://developer.apple.com/documentation/nearbyinteraction/implementing-spatial-interactions-with-third-party-accessories (zip at https://docs-assets.developer.apple.com/published/e2f511d49fda/ImplementingSpatialInteractionsWithThirdPartyAccessories.zip)
- Qorvo's current NI firmware service, as posted by a forum user in Oct 2024 (role not shown):
  - `struct QorvoNIService { static let serviceUUID = CBUUID(string: "2E938FD0-6A61-11ED-A1EB-0242AC120002"); static let scCharacteristicUUID = CBUUID(string: "2E93941C-6A61-11ED-A1EB-0242AC120002"); static let rxCharacteristicUUID = CBUUID(string: "2E93998A-6A61-11ED-A1EB-0242AC120002"); static let txCharacteristicUUID = CBUUID(string: "2E939AF2-6A61-11ED-A1EB-0242AC120002") }`
  - The post says: "If you're using the last firmware from Qorvo NI release, the device is advertising with QorvoNIService". The firmware file named there is `DWM3001CDK-QANI-FreeRTOS_full_QNI_3_0_0.hex`.
  - Source: https://forum.qorvo.com/t/issues-with-apple-nearby-interaction-app-not-detecting-dwm3001cdk-accessory/20321
- The Apple background "Nearby Interaction" GATT service, as quoted by forum users from Apple's R2 spec: service UUID `48fe3e40-0817-4bb2-8633-3073689c2dba` and Accessory Configuration Data characteristic `95e8d9d5-d8ef-4721-9a4e-807375f53328`. Per the spec as quoted there, "The Nearby Interaction service is not discoverable by iOS apps directly". https://forum.qorvo.com/t/dwm3001cdk-background-mode-ranging-nearby-interaction/12410

Minimal API-correct Swift sketch (not compiled):

```swift
import NearbyInteraction
import CoreBluetooth

final class PuckRanger: NSObject, NISessionDelegate {
    let session = NISession()
    var config: NINearbyAccessoryConfiguration?
    var send: (Data) -> Void = { _ in }          // writes to the puck's RX characteristic

    override init() { super.init(); session.delegate = self }

    // Called with the payload of a 0x01 accessoryConfigurationData message (first byte stripped).
    func handleAccessoryConfig(_ blob: Data, peripheral: CBPeripheral?) throws {
        guard NISession.deviceCapabilities.supportsPreciseDistanceMeasurement else { return }
        let cfg: NINearbyAccessoryConfiguration
        if let p = peripheral {   // background-capable path, requires LE pairing + NI GATT service on accessory
            cfg = try NINearbyAccessoryConfiguration(accessoryData: blob, bluetoothPeerIdentifier: p.identifier)
        } else {
            cfg = try NINearbyAccessoryConfiguration(data: blob)
        }
        cfg.isCameraAssistanceEnabled = false
        config = cfg
        session.run(cfg)
    }

    func session(_ session: NISession, didGenerateShareableConfigurationData shareableConfigurationData: Data, for object: NINearbyObject) {
        guard object.discoveryToken == config?.accessoryDiscoveryToken else { return }
        send(Data([0x0B]) + shareableConfigurationData)   // configureAndStart
    }
    func session(_ session: NISession, didUpdate nearbyObjects: [NINearbyObject]) {
        for o in nearbyObjects { _ = (o.distance, o.direction, o.horizontalAngle) }
    }
    func session(_ session: NISession, didRemove nearbyObjects: [NINearbyObject], reason: NINearbyObject.RemovalReason) {}
    func sessionWasSuspended(_ session: NISession) {}
    func sessionSuspensionEnded(_ session: NISession) { if let c = config { session.run(c) } }
    func session(_ session: NISession, didInvalidateWith error: Error) {}
}
```

Not confirmed:
- The Qorvo UUIDs come from a forum post, not from a Qorvo document I could open. The QNI package that would confirm them sits behind Qorvo's evaluation-tools download.
- The meaning of Qorvo's `scCharacteristicUUID` (it is probably the "secure" or pairing characteristic for background mode, but this is not stated on the page).
- Qorvo's QNI firmware message ids. Forum users report the Qorvo app and firmware follow Apple's sample framing (`0x01` config, `0x0B` configure-and-start), but I did not see the Qorvo source.

## Bottom line for the owner's hardware

Update: the owner's boards are actually DWM3001CDK, the part recommended below, so nothing needs to be bought. See the top section "DWM3001CDK + iPhone 14/15". The ESP32 analysis is kept for reference.


- ESP32 + DW3000 (Makerfabs ESP32 UWB DW3000, MaUWB ESP32-S3) with an iPhone 14: UWB ranging does NOT work with any public software. Apple's config blob comes from Qorvo's closed NIQ library, the FiRa session and STS setup come from a closed MAC library, and both exist only as Cortex-M4/M33 binaries (section 3). Do not plan the demo around it.
- What the ESP32 boards can do this weekend:
  - Puck-to-puck UWB two-way ranging (Makerfabs library), to place the pucks.
  - BLE scanning: count Apple Continuity emitters (no app, no identity, biased count; section 6), and detect the occupant app.
  - The occupant app as a BLE beacon. In the foreground, the app can advertise a service UUID and local name. In the background, a non-Apple scanner sees only Apple's overflow manufacturer bitmask, or must connect to enumerate services (section 5). The better design is the phone as central, connecting to the puck; that also works in the background with `bluetooth-central` and state restoration.
  - RSSI-only proximity.
- iPhone 14 with a proper Apple-interoperable accessory: distance in meters (around 60 Hz reported), with Apple's best-performance guideline of within 9 m line of sight. Direction is likely unavailable (14-family devices report `supportsDirectionMeasurement == false` in developer reports); `horizontalAngle` needs Camera Assistance with the phone moving. Check `NISession.deviceCapabilities` on the actual phone. A U1 phone supports multiple accessory sessions at once, so several pucks can each range the same phone and the command side can multilaterate (section 4, and https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging).
- Background: with a screen-off phone, the app gets no `didUpdate` callbacks in the classic paired path. The puck receives the distances and must report them itself, which suits this product: the puck relays the range to the command post. The iOS 18.4+ Live Activity path is the other option for an occupant app (section 5).
- Exactly what to buy to make iPhone UWB ranging work:
  - Part: Qorvo DWM3001CDK (DW3110 + nRF52833, BLE + UWB on one board, USB-powered, J-Link on board).
  - Price and stock: USD 29.50 each at DigiKey, 1,100 in stock on 2026-11-14 (factory lead time 37 weeks, so buy from stock). https://www.digikey.com/en/products/detail/qorvo/DWM3001CDK/24367348
  - Buy at least 2, ideally 3 to 4 for multilateration.
  - Firmware: the Qorvo Nearby Interaction (QANI) prebuilt hex from the DWM3001CDK "Evaluation Tools" tab, if still ungated. Test first with the free "Qorvo Nearby Interaction" iOS app, then point your own app at `QorvoNIService` `2E938FD0-6A61-11ED-A1EB-0242AC120002` (section 7).
  - The ESP32 boards stay in the puck for mesh, Wi-Fi uplink and BLE scanning. They can talk to the DWM3001CDK over UART if the QANI example is extended (not verified).
- Fallback if no DWM3001CDK can arrive in time: there is no same-day UWB option for the iPhone. Use BLE RSSI from the ESP32 pucks to the occupant app, and say so honestly in the demo.
- Watch item: iOS 27 adds Bluetooth Channel Sounding ranging through `NINearbyAccessoryConfiguration` (paired via AccessorySetupKit, distance only). It needs a Bluetooth 6.0 CS-capable phone and accessory. The classic ESP32 and ESP32-S3 are not CS-capable radios (not verified here), and whether the iPhone 14 supports CS was not confirmed; check `supportsBluetoothChannelSounding`. https://developer.apple.com/documentation/nearbyinteraction/nideviceCapability/supportsbluetoothchannelsounding
