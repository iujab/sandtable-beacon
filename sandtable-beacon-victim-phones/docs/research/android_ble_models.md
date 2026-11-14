# Sandtable Beacon: Android UWB, BLE, cold sensing and noise models

Research notes, compiled Sat Nov 14 2026. Every claim carries the URL it was read from. Items marked "Not confirmed" were searched for and not verified from a primary page.

Status: complete for this pass (about 20 minutes of research). Sections 5-8 were gathered by parallel research passes that opened the cited pages; Sections 1-4 and the bottom line were checked directly.

## 1. Android UWB API (Jetpack androidx.core.uwb and platform android.ranging)

Plain answer: there are two app-level UWB APIs. Jetpack `androidx.core.uwb` went stable (1.0.0) in May 2026; the newest is `1.1.0-alpha03` (Oct 7 2026), which adds a sensor-fusion session type. It needs Android 12+, a UWB phone, the runtime permission `android.permission.UWB_RANGING`, and an app-defined out-of-band (OOB) exchange (usually BLE) of address, complex channel, session id and STS key. Android 16 (API 36) adds a platform `android.ranging` module (`RangingManager`) covering UWB, BLE Channel Sounding, BLE RSSI and Wi-Fi RTT under one permission `android.permission.RANGING`; no Jetpack wrapper for it was found.

Jetpack `androidx.core.uwb` facts:
- Versions: stable `1.0.0` (May 06 2026); latest alpha `1.1.0-alpha03` (Oct 07 2026); `1.1.0-alpha02` (Sep 09 2026) added "odometry-based sensor fusion, which provides software angle-of-arrival estimation"; `1.1.0-alpha01` added `UwbManager.close()`. https://developer.android.com/jetpack/androidx/releases/core-uwb
- Gradle (Kotlin DSL): `implementation("androidx.core.uwb:uwb:1.0.0")` (stable) or `implementation("androidx.core.uwb:uwb:1.1.0-alpha03")`; Java/Rx artifact `androidx.core.uwb:uwb-rxjava3`. https://developer.android.com/jetpack/androidx/releases/core-uwb
- Release-note history useful for reading old samples: `RangingParameters#CONFIG_ID_1` was renamed `CONFIG_UNICAST_DS_TWR` and `CONFIG_MULTICAST_DS_TWR` added (1.0.0-alpha05); Provisioned STS added (alpha06); AOSP backend used when no GMS (alpha04/alpha07); `isAvailable()` (alpha09); `RangingResultFailure` (alpha11). https://developer.android.com/jetpack/androidx/releases/core-uwb
- Requirements per the guide: "Ensure Android devices are running on Android 12 or higher and that they support UWB using `PackageManager#hasSystemFeature("android.hardware.uwb")`"; "If ranging against IoT devices, ensure that they are FiRa MAC 1.3 compliant"; OOB discovery and parameter exchange is left to the app (e.g. `BluetoothLeScanner`, `BluetoothGatt`). https://developer.android.com/develop/connectivity/uwb
- Permission: `android.permission.UWB_RANGING`, added API 31, protection level dangerous (runtime prompt). https://developer.android.com/reference/android/Manifest.permission#UWB_RANGING
- `UwbManager` (interface, `AutoCloseable`): `UwbManager.createInstance(context: Context): UwbManager`; `suspend fun controllerSessionScope(): UwbControllerSessionScope`; `suspend fun controleeSessionScope(): UwbControleeSessionScope`; `clientSessionScope()` is deprecated ("Renamed to controleeSessionScope"); `suspend fun isAvailable(): Boolean`; `setUwbAvailabilityCallback(executor, observer)`, `clearUwbAvailabilityCallback()`, `close()`. Session-scope calls throw `UwbServiceNotAvailableException` (UWB off) or `UwbHardwareNotAvailableException`. https://developer.android.com/reference/kotlin/androidx/core/uwb/UwbManager
- `UwbClientSessionScope` (parent of both scopes): `val localAddress: UwbAddress` ("can only be used for a single ranging session"), `val rangingCapabilities: RangingCapabilities`, `fun prepareSession(parameters: RangingParameters): Flow<RangingResult>`, new overload `prepareSession(parameters: SensorFusionParameters): Flow<SensorFusionResult>`, `suspend fun reconfigureRangeDataNtf(configType, proximityNear, proximityFar)`. https://developer.android.com/reference/kotlin/androidx/core/uwb/UwbControllerSessionScope
- `UwbControllerSessionScope` adds `val uwbComplexChannel: UwbComplexChannel` (send this to the controlee over OOB; a new channel is allocated per session), `suspend fun addControlee(address: UwbAddress)`, `addControlee(address, parameters: RangingControleeParameters)`, `removeControlee(address)`, `reconfigureRangingInterval(intervalSkipCount: Int)`. https://developer.android.com/reference/kotlin/androidx/core/uwb/UwbControllerSessionScope
- `RangingParameters(uwbConfigType: Int, sessionId: Int, subSessionId: Int, sessionKeyInfo: ByteArray?, subSessionKeyInfo: ByteArray?, complexChannel: UwbComplexChannel?, peerDevices: List<UwbDevice>, updateRateType: Int, uwbRangeDataNtfConfig: UwbRangeDataNtfConfig? = null, slotDurationMillis: Long = RANGING_SLOT_DURATION_2_MILLIS, isAoaDisabled: Boolean = false)`. `sessionId` 0 means "created from the hash of controller address and complex channel"; both ends must use the same session id and key. https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingParameters
- Config ids (constants on `RangingParameters`): `CONFIG_UNICAST_DS_TWR = 1` ("unicast STATIC STS DS-TWR ... deferred mode, ranging interval = 240 ms, slot duration = 2400 RSTU, slots per ranging round = 6, hopping mode is enabled. All other MAC parameters use FiRa/UCI default values"); `CONFIG_MULTICAST_DS_TWR = 2` (200 ms, 20 slots/round); `CONFIG_PROVISIONED_UNICAST_DS_TWR = 4`; `CONFIG_PROVISIONED_MULTICAST_DS_TWR = 5`; `CONFIG_PROVISIONED_INDIVIDUAL_MULTICAST_DS_TWR = 7`. Update rates: `RANGING_UPDATE_RATE_AUTOMATIC = 1`, `RANGING_UPDATE_RATE_INFREQUENT = 2`, `RANGING_UPDATE_RATE_FREQUENT = 3`. Slot durations `RANGING_SLOT_DURATION_1_MILLIS = 1L`, `RANGING_SLOT_DURATION_2_MILLIS = 2L`. https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingParameters
  - Caution: the reference text for `INFREQUENT` says "120 millisecond" and `FREQUENT` says "600 millisecond", which reads inverted; verify on device. https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingParameters
- Static STS key format: "If the profile uses STATIC STS, this byte array is 8-byte long with first two bytes as Vendor_ID and next six bytes as STATIC_STS_IV. If the profile uses PROVISIONED STS, this byte array is 16 or 32-byte long." https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingParameters ; guide repeats "Provide the 8-byte key in the `sessionKeyInfo` field" for static STS and notes provisioned STS is supported on all UWB devices running Android 14+. https://developer.android.com/develop/connectivity/uwb
- `UwbComplexChannel(channel: Int, preambleIndex: Int)`. https://developer.android.com/reference/kotlin/androidx/core/uwb/UwbComplexChannel
- Results: `RangingResult.RangingResultPosition` (has `.position: RangingPosition`), `RangingResult.RangingResultPeerDisconnected`, `RangingResultInitialized`, `RangingResultFailure`. `RangingPosition` has `distance: RangingMeasurement?` ("line-of-sight distance in meters"), `azimuth: RangingMeasurement?` (degrees, positive = peer right of boresight, range [-180,180]), `elevation: RangingMeasurement?` (degrees, [-90,90]), `elapsedRealtimeNanos: Long`. Angles are relative to "the normal to the back of the device". https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingPosition ; https://developer.android.com/develop/connectivity/uwb
- Known byte-order bug: on Android 13 and lower the stack reversed byte order of device MAC address, destination MAC address and static STS vendor id; fixed via GMS Core `2320XXXX`; IoT vendors must not reverse. https://developer.android.com/develop/connectivity/uwb
- Official sample app: https://github.com/android/platform-samples/tree/main/samples/connectivity/UwbRanging (linked from https://developer.android.com/develop/connectivity/uwb)

Minimal Kotlin shape (names verified against the pages above; the OOB functions are yours to write):
```kotlin
val mgr = UwbManager.createInstance(context)
val scope = mgr.controllerSessionScope()           // phone as controller
// send scope.localAddress.address and scope.uwbComplexChannel (channel, preambleIndex) to the accessory over BLE
val params = RangingParameters(
    uwbConfigType = RangingParameters.CONFIG_UNICAST_DS_TWR,
    sessionId = 0x1234, subSessionId = 0,
    sessionKeyInfo = byteArrayOf(0x07, 0x08, 1, 2, 3, 4, 5, 6), // 2-byte vendor id + 6-byte static STS IV
    subSessionKeyInfo = null,
    complexChannel = scope.uwbComplexChannel,
    peerDevices = listOf(UwbDevice.createForAddress(accessoryAddrBytes)),
    updateRateType = RangingParameters.RANGING_UPDATE_RATE_AUTOMATIC)
scope.prepareSession(params).collect { r ->
    if (r is RangingResult.RangingResultPosition) { r.position.distance?.value; r.position.azimuth?.value }
}
```
(`UwbDevice.createForAddress` appears in the guide's sample; check its exact overloads in the reference before compiling.)

Platform `android.ranging` (Android 16+) facts:
- `RangingManager` added API 36; get it with `Context.getSystemService(RangingManager.class)`; constants `UWB = 0`, `BLE_CS = 1`, `WIFI_NAN_RTT = 2`, `BLE_RSSI = 3`, `WIFI_STA_RTT = 4` (added 36.1), `WIFI_PD = 5` (API 37); methods `createRangingSession(Executor, RangingSession.Callback)`, `registerCapabilitiesCallback(...)`, `unregisterCapabilitiesCallback(...)`. https://developer.android.com/reference/android/ranging/RangingManager
- Permission: unified `android.permission.RANGING` (in `NEARBY_DEVICES_PERMISSIONS`). Session types: `RANGING_SESSION_OOB` (module does standardized OOB, but not discovery/connection) and `RANGING_SESSION_RAW` (app does OOB). Builders: `OobInitiatorRangingConfig.Builder`, `OobResponderRangingConfig.Builder(deviceHandle)`, `RangingPreference.Builder(DEVICE_ROLE_INITIATOR, config)`, `RawInitiatorRangingConfig.Builder().addRawRangingDevice(...)`, `RawRangingDevice.Builder().setUwbRangingParams(...)`, `UwbRangingParams.Builder(sessionId, UwbRangingParams.CONFIG_UNICAST_DS_TWR, sourceAddress, destinationAddress)`, `SessionConfig.Builder().setAngleOfArrivalNeeded(true)`; results arrive in `RangingData` via `onResults`; `getDistanceStandardDeviationMeters` on 36.1+. https://developer.android.com/develop/connectivity/ranging
- Restrictions: "Third-party apps are only allowed to perform background ranging with ultra-wideband, and only on supported devices. Ranging in the background with other technologies is not allowed." For non-UWB technologies results only go to the initiator. https://developer.android.com/develop/connectivity/ranging
- Android-to-iPhone UWB interop is documented through `android.ranging` with a custom (raw) OOB using `CONFIG_UNICAST_DS_TWR`, session key info = Vendor ID + Static STS IV, matching complex channel, per Apple's Nearby Interaction accessory protocol. https://developer.android.com/develop/connectivity/ranging
- Android 17 (API 37) adds UWB DL-TDoA (`DEVICE_ROLE_DT_TAG`, `DlTdoaRangingParams`), which needs `ACCESS_FINE_LOCATION` instead of `RANGING`. https://developer.android.com/develop/connectivity/ranging
- AOSP sample app for all ranging technologies: https://cs.android.com/android/platform/superproject/main/+/main:packages/modules/Uwb/ranging/test_app/ (linked from https://developer.android.com/develop/connectivity/ranging)

Not confirmed:
- A Jetpack wrapper for `android.ranging` (searched "androidx.ranging"; none found on the AndroidX releases index as of this search).
- Whether `UWB_RANGING` alone is sufficient for `androidx.core.uwb` on Android 16+ phones or whether `RANGING` must also be declared; an AOSP commit adds `RANGING` alongside `UWB_RANGING` for the connectedDevice foreground-service type (https://android.googlesource.com/platform/frameworks/base/+/9840396a0d80a6fe8e0ad54e903d82eed8fd0f3e%5E%21), but I did not open it. Declare both to be safe.
- Exact `UwbDevice.createForAddress` / `UwbAddress` overloads (not opened).

## 2. Android phones with UWB, and AoA support

Plain answer: UWB is in the "Pro/Plus/Ultra/Fold" tier only. Google: Pixel 6 Pro, 7 Pro, 8 Pro, 9 Pro / 9 Pro XL / 9 Pro Fold, 10 Pro / 10 Pro XL / 10 Pro Fold, Pixel Fold, Pixel Tablet (base Pixel 6-10 and "a" models have none). Samsung: Note20 Ultra, S21+ / S21 Ultra through S26+ / S26 Ultra (base S models have none), Z Fold2 onward. Others: Motorola Edge 50 Ultra and Razr Ultra models, Xiaomi MIX4 / 15s Pro / 17 Pro line, a few others. Whether a specific phone gives azimuth/elevation or distance only is best checked at runtime with `RangingCapabilities.isAzimuthalAngleSupported` / `isElevationAngleSupported`; there is no official per-model AoA table. Also check `PackageManager.hasSystemFeature("android.hardware.uwb")`.

Checklist (owner: find your model here, then run the runtime check):

| Vendor | Models with UWB | Source |
|---|---|---|
| Google | Pixel 6 Pro, 7 Pro, 8 Pro, 9 Pro, 9 Pro XL, 9 Pro Fold, 10 Pro, 10 Pro XL, 10 Pro Fold, (11 Pro line Aug 2026), Pixel Fold, Pixel Tablet | Google's list (Jan 2025): "Pixel Pro (6 pro and newer), Fold, Tablet" https://developer.android.com/develop/connectivity/uwb ; per-model rows https://en.wikipedia.org/wiki/List_of_UWB-enabled_mobile_devices ; Pixel 10 Pro spec page lists "Ultra-wideband chip" https://store.google.com/product/pixel_10_pro_specs |
| Samsung | Galaxy Note20 Ultra; S21+ / S21 Ultra; S22+ / Ultra; S23+ / Ultra; S24+ / Ultra; S25+ / Ultra / Edge; S26+ / Ultra; Z Fold2 through Fold7 (Fold8) | Google's list: "Galaxy Note 20, Galaxy Plus and Ultra (S21 and newer), Galaxy Z Fold (Fold2 and newer)" https://developer.android.com/develop/connectivity/uwb ; per-model rows https://en.wikipedia.org/wiki/List_of_UWB-enabled_mobile_devices |
| Motorola | Edge 50 Ultra (Google-listed); Razr 60 Ultra / Razr (2025) Ultra; Signature (2026) | https://developer.android.com/develop/connectivity/uwb ; https://en.wikipedia.org/wiki/List_of_UWB-enabled_mobile_devices |
| Xiaomi | MIX4, Mix Flip, 15s Pro, 17 Pro / 17 Pro Max, 17 Ultra (some China-only) | https://en.wikipedia.org/wiki/List_of_UWB-enabled_mobile_devices |
| Others | Sharp AQUOS R8 (Japan), Meizu 20 Infinity, Blackview BV8900 Pro, NIO Phone 2, Huawei Mate XT (HarmonyOS) | https://en.wikipedia.org/wiki/List_of_UWB-enabled_mobile_devices |
| Apple (reference) | iPhone 11 and later (U1), 15+ (U2); 16e and 17e lack UWB | https://en.wikipedia.org/wiki/List_of_UWB-enabled_mobile_devices |

Facts:
- Runtime capability flags: `RangingCapabilities.isDistanceSupported`, `isAzimuthalAngleSupported`, `isElevationAngleSupported`, `isBackgroundRangingSupported`, `isRangingIntervalReconfigureSupported`, `minRangingInterval`, `supportedChannels`, `supportedConfigIds`, `supportedSlotDurations`, `supportedRangingUpdateRates`, `supportedNtfConfigs`. https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingCapabilities
- Background ranging is supported "on all devices except ... Pixel 6 Pro, Pixel 7 Pro, and Pixel Fold; Samsung phones with Android 13 or lower; Samsung Chinese phones with Android 14 or lower." https://developer.android.com/develop/connectivity/uwb
- Being on a vendor list is not the same as Jetpack access: Google's Jetpack-supported list (Jan 2025) is only Google Pixel Pro/Fold/Tablet, Motorola Edge 50 Ultra, and Samsung Note20 / S21+ and Ultra and newer / Z Fold2+. Xiaomi and others may have UWB hardware without exposing the Jetpack API. https://developer.android.com/develop/connectivity/uwb
- Jetpack 1.1.0-alpha02 adds software AoA via "odometry-based sensor fusion", which may give direction on phones whose hardware reports distance only (unverified on devices). https://developer.android.com/jetpack/androidx/releases/core-uwb

Not confirmed:
- A per-model AoA table. Search-engine summaries suggested Pixel 7 Pro has AoA and Pixel 6 Pro lacks part of it, and an Estimote forum user got null azimuth/elevation on a Galaxy S24+ with distance working, but I could not open the source articles (403) to confirm. Treat AoA as "check at runtime".
- Whether the S25 Edge and Pixel 11 Pro line are in Jetpack's supported set (only on the Wikipedia list).

## 3. Android phone ranging with a third-party UWB board (and the ESP32 + DW3000 question)

Plain answer: yes, an Android phone can range with a third-party board, but only if the board runs a FiRa MAC (Google says "FiRa MAC 1.3 compliant") and both sides agree on config id, session id, static STS key (vendor id + IV), channel/preamble and short addresses, which you exchange over your own OOB link (normally BLE). Confirmed working boards are Qorvo DWM3001CDK running Qorvo's UCI/FiRa firmware (Pixel 8 Pro and Pixel 9 Pro, community repo) and the partner kits Google lists (Estimote UWB, Mobile Knowledge MK UWB Kit, which is NXP-based). THE KEY QUESTION, ESP32 + DW3000 to phone: DOES NOT WORK today with any open implementation I could find. Qorvo's FiRa MAC ships only as a precompiled ARM library, the Makerfabs ESP32 driver is a port of the bare DW3000 driver with plain (non-FiRa) SS/DS-TWR examples, and the one public ESP32-DW3000-to-iPhone attempt stopped at "sends an invalid UWB config". Writing a FiRa-compatible DS-TWR responder with static STS from scratch on the bare driver is theoretically possible but undocumented in public (the FiRa spec is members-only) and not achievable tonight.

Facts:
- Google's checklist: "If ranging against IoT devices, ensure that they are FiRa MAC 1.3 compliant"; OOB parameter exchange is left to the app. https://developer.android.com/develop/connectivity/uwb
- Google-listed compatible third-party SDKs (as of April 2023): Estimote UWB Development Kit and Mobile Knowledge "MK UWB Kit Mobile Edition 2.0". https://developer.android.com/develop/connectivity/uwb
- What must match (from the Jetpack API): config id (`CONFIG_UNICAST_DS_TWR` = static STS, DS-TWR, deferred mode, 240 ms interval, 2400 RSTU slots, 6 slots/round, hopping on, "All other MAC parameters use FiRa/UCI default values"), session id, 8-byte `sessionKeyInfo` = 2-byte Vendor_ID + 6-byte STATIC_STS_IV, `UwbComplexChannel(channel, preambleIndex)`, and 2-byte short addresses. https://developer.android.com/reference/kotlin/androidx/core/uwb/RangingParameters
- Byte-order gotcha for the accessory side: Android 13 and lower reversed the MAC address and static STS vendor id byte order; fixed via GMS Core `2320XXXX`. https://developer.android.com/develop/connectivity/uwb
- DWM3001CDK + Android, working: community repo ranges a DWM3001CDK running Qorvo UCI firmware from `DW3_QM33_SDK_1.0.2` with Pixel 8 Pro and Pixel 9 Pro; OOB is manual (copy the phone address into a Python script, board on USB); defaults preamble index 9, 6 slots per round, hopping enabled, 120 ms ranging span; with the newer firmware both phone-as-controller and phone-as-controlee work, with the old v1.0.1 firmware only phone-as-controlee was reliable; the script reverses address byte order. https://github.com/sasodoma/uwb-ranging
- Same one-way symptom reported on a Qorvo forum thread (DWM3001CDK UCI-FreeRTOS_0_1_1 + Pixel 8 Pro, Android 14, May 2024): module as controller works "very good results"; phone as controller intermittent; no resolution. https://forum.qorvo.com/t/dwm3001cdk-and-google-pixel-8-pro-ranging-only-works-one-way/18083
- Qorvo has no Android sample: staff reply (Jan-Feb 2026) "Qorvo does not have a sample application for this" and pointed to Google's UWB docs; a community member notes OOB both passes parameters and invokes the session, so it cannot simply be hard-coded away on the phone side. https://forum.qorvo.com/t/android-phone-ranging-with-dwm3000-evb-with-nrf52840-dk/24828
- A community member (July 29 2026) says they extended Qorvo DW3_QM33 SDK 1.1.1 so Android can range to QANI-enabled devices (DWM3001CDK, Type2AB, nRF52840) with a Kotlin Multiplatform app, available only by private contact. https://forum.qorvo.com/t/ranging-pixel-6-pro-androidx-uwb-to-dwm3000evb/12347/28
- Qorvo's DW3_QM33 SDK v1.0.2 is described as compliant with FiRa PHY/MAC 2.0 with a UCI GUI and Python API (search-summary of the release thread; I did not open it): https://forum.qorvo.com/t/new-release-of-dw3000-and-qm33-sdk-dw3-qm33-sdk-v1-0-2/19160
- FiRa MAC is closed and precompiled: "The FiRa MAC layer ships as a precompiled `.a` archive" (about 200 KB), source not inspectable; hosts discussed are nRF52833/nRF52840/nRF5340 (April-June 2026). https://forum.qorvo.com/t/using-dw3xxx-sdk-on-upstream-zephyr-not-ncs-official-path/25425
- A community Zephyr 4.2 port of the Qorvo SDK 1.1.1 builds for nRF52833 (DWM3001CDK) but the FiRa MAC binary stops after "Block 0"; README says other SoC families including ESP32 "won't build without extra work". https://github.com/amstrdm/dw3xxx-sdk-zephyr
- ESP32 + DW3000 with phones (Qorvo forum, July 2023): "Possible" but "probably not easy"; "The only official [drivers] are only available as a pre-compiled library that's been built for ARM processors"; the Makerfabs repo ports an older driver to ESP32 using the standard Decawave API; nobody reported success; the asker switched to DWM3001CDK. https://forum.qorvo.com/t/esp32-dw3000-interaction-with-android-and-ios/14518
- Makerfabs ESP32 UWB DW3000 product page: claims "Interoperable with Apple U1 chip" and "Fully aligned with FiRa PHY, MAC, and certification development" (chip-level marketing) and then "Makerfabs now do not have related files/demos to support this." Price US$43.80; library https://github.com/Makerfabs/Makerfabs-ESP32-UWB-DW3000. https://makerfabs.com/esp32-uwb-dw3000.html
- Public ESP32-DW3000 to iPhone attempt: "Accessory can be connected to by the demo app, but sends an invalid UWB config." 4 commits, no success reported. https://github.com/maa-x/ESP32-DW3000-AppleNearbyInteraction
- Community statement (March 2023): "the drivers for the DW3000 on ESP32 do not implement FiRa (the spec used for UWB on iPhones)"; a later reply notes the chip itself "is FiRa capable". https://community.home-assistant.io/t/anyone-use-uwb-ultra-wide-bandwidth-for-room-level-presence-detection/134374
- Qorvo's Apple path (QANI) and Android path (FiRa/UCI) use different firmware binaries; QANI firmware is driven over BLE from the phone (search summary of Qorvo threads; I did not open them individually): https://forum.qorvo.com/t/about-qorvo-nearby-interactions-for-android/24986
- Android can also range to an iPhone over UWB through `android.ranging` raw sessions using `CONFIG_UNICAST_DS_TWR` and vendor id + static STS IV per Apple's accessory protocol. https://developer.android.com/develop/connectivity/ranging

What is missing for ESP32 + DW3000 to range with a phone:
1. A FiRa MAC on the ESP32 (Xtensa for classic ESP32 and ESP32-S3): Qorvo's is ARM-only binary; no open-source FiRa MAC was found (searched GitHub/forums for "FiRa ESP32 DW3000", "open source FiRa MAC", "uwb-stack zephyr fira", "Jetpack UWB DW3000 ESP32").
2. Exact FiRa frame/IE formats, static STS derivation from vendor id + IV, deferred DS-TWR message sequence and timing (members-only FiRa spec).
3. A BLE OOB service that hands the phone the board's short address and receives the phone's address and complex channel (doable on ESP32; this part is easy).
Status: does not work (no open implementation); unknown whether a from-scratch port is feasible without FiRa documents.

Not confirmed:
- Whether the MaUWB ESP32-S3 variant's UWB module (reportedly a DW3000 behind an AT-command MCU) exposes anything FiRa-like; no phone-compatibility claim found.
- NXP SR150/SR040 and Murata Type2BP/Type2AB kits with Jetpack UWB: Mobile Knowledge kit is Google-listed, but I did not open an NXP or Murata page confirming firmware versions.
- The exact firmware version Google tested for the Estimote kit.

## 4. Background behaviour: UWB and BLE on Android

Plain answer: UWB ranging can be started from the background only on phones that report `isBackgroundRangingSupported` (not Pixel 6 Pro / 7 Pro / Fold, not older Samsung), and even then Jetpack says the app "doesn't receive ranging reports when running in the background" until it returns to the foreground. So for an occupant app that must keep ranging with the screen off, run a foreground service of type `connectedDevice` (allowed when the app holds `UWB_RANGING` or a Bluetooth runtime permission) and plan for UWB to be best-effort while backgrounded; BLE advertising from that service is the reliable floor. BLE scans without filters stop when the screen turns off, so scan with `ScanFilter`s. For the platform `android.ranging` module, background ranging is UWB-only.

Facts:
- UWB background: "An app running in background can start a UWB ranging session if the device supports it ... The app doesn't receive ranging reports when running in the background; the app receives ranging reports when it moves to the foreground." Also: the app initiating sessions "must be a foreground app or service unless background ranging is supported"; when moved to background "The UWB session will however continue to be maintained in the lower layers." https://developer.android.com/develop/connectivity/uwb
- Background-ranging device exceptions: Pixel 6 Pro, Pixel 7 Pro, Pixel Fold; Samsung on Android 13 or lower; Samsung Chinese phones on Android 14 or lower. https://developer.android.com/develop/connectivity/uwb
- `android.ranging`: "Third-party apps are only allowed to perform background ranging with ultra-wideband, and only on supported devices. Ranging in the background with other technologies is not allowed." https://developer.android.com/develop/connectivity/ranging
- Foreground service type (Android 14+ must declare): `android:foregroundServiceType="connectedDevice"`, manifest permission `FOREGROUND_SERVICE_CONNECTED_DEVICE`, constant `ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE`; prerequisite is one of: manifest `CHANGE_NETWORK_STATE`/`CHANGE_WIFI_STATE`/`CHANGE_WIFI_MULTICAST_STATE`/`NFC`/`TRANSMIT_IR`, or a granted runtime permission among `BLUETOOTH_CONNECT`, `BLUETOOTH_ADVERTISE`, `BLUETOOTH_SCAN`, `UWB_RANGING`, or `UsbManager.requestPermission()`. Also requires base `FOREGROUND_SERVICE`. https://developer.android.com/develop/background-work/services/fgs/service-types
- Bluetooth permissions (target Android 12+): `BLUETOOTH_SCAN`, `BLUETOOTH_ADVERTISE`, `BLUETOOTH_CONNECT` are runtime permissions shown as "Nearby devices"; legacy `BLUETOOTH`/`BLUETOOTH_ADMIN` with `android:maxSdkVersion="30"`; `android:usesPermissionFlags="neverForLocation"` on `BLUETOOTH_SCAN` lets you drop `ACCESS_FINE_LOCATION` but "some BLE beacons are filtered from the scan results"; Android 10-11 background discovery needs `ACCESS_BACKGROUND_LOCATION`. https://developer.android.com/develop/connectivity/bluetooth/bt-permissions
- Scanning: "For unfiltered scans, scanning is stopped on screen off to save power. Scanning is resumed when screen is turned on again. To avoid this, do filtered scanning by using proper ScanFilter." `startScan(filters, settings, PendingIntent)` delivers results to a not-always-running process. https://developer.android.com/reference/android/bluetooth/le/BluetoothLeScanner
- Advertising: `BluetoothAdapter.getBluetoothLeAdvertiser()`; "An advertiser can broadcast up to 31 bytes of advertisement data represented by `AdvertiseData`" (legacy); `startAdvertising(AdvertiseSettings, AdvertiseData, AdvertiseCallback)` and overload with `scanResponse`; `startAdvertisingSet(AdvertisingSetParameters, AdvertiseData, scanResponse, periodicParameters, periodicData, duration, maxExtendedAdvertisingEvents, AdvertisingSetCallback)` (API 26) where data "must not exceed `BluetoothAdapter.getLeMaximumAdvertisingDataLength()`" and connectable adverts add 3 bytes of flags; `BLUETOOTH_ADVERTISE` "is always enforced". https://developer.android.com/reference/android/bluetooth/le/BluetoothLeAdvertiser
- `AdvertisingSetParameters.Builder`: `setLegacyMode(true)` = 4.x-compliant adverts; `setConnectable`, `setScannable`, `setInterval(int)` in 0.625 ms units, valid 160 (100 ms) to 16777215, recommended `INTERVAL_LOW`/`INTERVAL_MEDIUM`/`INTERVAL_HIGH`; `setTxPowerLevel(int)` in dBm, range [-127, 20], recommended `TX_POWER_ULTRA_LOW`/`LOW`/`MEDIUM`/`HIGH`; `setIncludeTxPower`, `setPrimaryPhy`, `setSecondaryPhy` only for non-legacy. https://developer.android.com/reference/android/bluetooth/le/AdvertisingSetParameters.Builder
- Budget for a legacy 31-byte advert: 3 bytes flags (if connectable) + service-data AD (1 length + 1 type 0x16 + 2-byte 16-bit UUID + payload) leaves about 24 bytes of payload if nothing else is included (arithmetic from the 31-byte limit above; use `AdvertiseData.Builder().addServiceData(ParcelUuid, ByteArray)` and `setIncludeDeviceName(false)`).

Not confirmed:
- Doze specifics for a `connectedDevice` foreground service holding BLE advertising (FGS processes are exempt from most Doze app restrictions, but I did not open the Doze page in this session).
- Android 15/16 changes specific to `connectedDevice` FGS beyond the Android 14 type requirement (the types page shows Android 15 BOOT_COMPLETED restrictions only for dataSync, mediaPlayback, mediaProjection, phoneCall).
- Exact millisecond values of `INTERVAL_LOW/MEDIUM/HIGH` (commonly 100/250/1000 ms; not opened).

## 5. Wi-Fi RTT / FTM as an extra rung

Plain answer: the classic ESP32 (ESP32-WROVER on the Makerfabs DW3000 board) does NOT support FTM; ESP32-S2, S3, C2, C3, C5, C6, C61 do, as responder in SoftAP mode and initiator in station mode, and Arduino-ESP32 exposes it (`WiFi.softAP(..., true)` and `WiFi.initiateFTM()`). So a MaUWB ESP32-S3 board can be an FTM responder; an ESP32-WROVER board cannot. Android `WifiRttManager` (Android 9+) can range to 802.11mc responders, but phone-to-ESP32 evidence is thin: an S2 + Pixel 4a test was flaky, Espressif warned accuracy would be "very low", and fixes for Pixel phones landed in March 2024. Expect 1-3 m error; treat it as a coarse rung, not a precision one. iOS has no public RTT API.

Facts (ESP32 side):
- FTM example supported targets: ESP32-C2, C3, C5, C6, C61, S2, S3, S31; classic ESP32 and H2 not listed. https://github.com/espressif/esp-idf/tree/master/examples/wifi/ftm
- Classic ESP32 SoC caps list no `SOC_WIFI_FTM_SUPPORT`; ESP32-C3 caps list it. https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/soc_caps.html ; https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-reference/system/soc_caps.html
- ESP32-S3: "FTM Initiator in station mode", "FTM Responder in AP mode"; "Distance measurement using RTT is not accurate"; recommends ESP-to-ESP. https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/wifi-driver/security-and-roaming.html
- ESP-IDF API: `wifi_ap_config_t.ftm_responder` (bool), `esp_wifi_ftm_initiate_session(wifi_ftm_initiator_cfg_t *cfg)` (fields `resp_mac[6]`, `channel`, `frm_count` 0/16/24/32/64, `burst_period` in 100 ms units, `secondary_chan`), event `WIFI_EVENT_FTM_REPORT` with `wifi_event_ftm_report_t` (`rtt_raw`, `rtt_est` ns, `dist_est` cm), `esp_wifi_ftm_end_session()`, `esp_wifi_ftm_resp_set_offset(int16_t offset_cm)`, `esp_wifi_ftm_get_report(...)`; scan records carry `ftm_responder`/`ftm_initiator` bits. https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/network/esp_wifi.html
- Example guidance: RSSI at least -70 dBm, line of sight; `ftm -R -o <cm>` sets responder offset. https://github.com/espressif/esp-idf/tree/master/examples/wifi/ftm
- Arduino-ESP32: `libraries/WiFi/examples/FTM/FTM_Responder` uses `WiFi.softAP(WIFI_FTM_SSID, WIFI_FTM_PASS, 1, 0, 4, true)` (last arg enables FTM responder); `FTM_Initiator` uses `WiFi.initiateFTM(FTM_FRAME_COUNT, FTM_BURST_PERIOD)` and `ARDUINO_EVENT_WIFI_FTM_REPORT`; has an `ESP_IDF_VERSION < 6.0.0` guard so it tracks core 3.x. Header comment "SUPPORTED ONLY BY ESP32-S2 AND ESP32-C3" is stale. https://github.com/espressif/arduino-esp32/tree/master/libraries/WiFi/examples/FTM ; https://raw.githubusercontent.com/espressif/arduino-esp32/master/libraries/WiFi/examples/FTM/FTM_Initiator/FTM_Initiator.ino
- Accuracy: ESP32-S2 to ESP32-S2 indoors about 1 m error at 0.5 m and 2-3 m error at 1.0-1.5 m; Pixel 4a to Google Wi-Fi AP sub-metre. https://ar5iv.labs.arxiv.org/html/2303.03766 ; Android docs: 3+ AP multilateration "typically accurate within 1-2 meters". https://developer.android.com/develop/connectivity/wifi/wifi-rtt ; "a meter or two is more common". https://people.csail.mit.edu/bkph/ftmrtt_issues
- Phone vs ESP32 SoftAP evidence: esp-idf issue #6810 "Fail ESP32S2 FTM test with Android Smartphone": Pixel 4a with WifiRttScan worked only when connected and failed about half the time; maintainer saw no issue on Mi 11X Pro; Espressif: "the accuracy will be very low"; closed 2024-03-20 after FTM refactor and "more tests with Pixel devices". https://github.com/espressif/esp-idf/issues/6810

Facts (Android side), all from https://developer.android.com/develop/connectivity/wifi/wifi-rtt :
- Android 9 (API 28)+; 802.11az NTB ranging on Android 15 (API 35) via `WifiRttManager.CHARACTERISTICS_KEY_BOOLEAN_NTB_INITIATOR` and `ScanResult.is80211azNtbResponder()`.
- `PackageManager.FEATURE_WIFI_RTT`; `WifiRttManager.isAvailable()`; `ACTION_WIFI_RTT_STATE_CHANGED`; some phones cannot range while own hotspot is on.
- Permissions: `ACCESS_WIFI_STATE`, `CHANGE_WIFI_STATE`; `NEARBY_WIFI_DEVICES` when targeting 33+; `ACCESS_FINE_LOCATION` (maxSdkVersion 32) below; Location and Wi-Fi scanning on; foreground app or foreground service.
- API: `context.getSystemService(Context.WIFI_RTT_RANGING_SERVICE)`; `RangingRequest.Builder().addAccessPoint(scanResult)`; filter with `ScanResult.is80211mcResponder()`; `startRanging(request, executor, callback)`; `RangingResult.getDistanceMm()`, `getDistanceStdDevMm()`, `getRssi()`, `getNumSuccessfulMeasurements()`.
- Device table ("far from comprehensive"): Pixel 1 through Pixel 9 series, Galaxy S20/Note10 families, several Galaxy S/A models, many Xiaomi; Pixel 10 not listed.
- Android 16 `RangingManager.WIFI_STA_RTT` (36.1) also wraps STA-to-AP RTT. https://developer.android.com/reference/android/ranging/RangingManager
- iOS: "Windows 10 has no API for FTM RTT. Nor does Apple iOS." (researcher page, not Apple). https://people.csail.mit.edu/bkph/ftmrtt_issues

Not confirmed:
- A published successful Android to ESP32-S3/C3 SoftAP ranging with accuracy numbers (esp32.com threads were bot-blocked).
- Official Apple statement on RTT; Pixel 10 / Galaxy S25-S26 RTT support.

## 6. Bluetooth Channel Sounding as a future rung

Plain answer: Channel Sounding works today on all Pixel 10 models and on Pixel 9 with Android 16 QPR2+, through `android.ranging` (`RangingManager.BLE_CS`, `BleCsRangingParams`). An Apple engineer says iPhone 17 (N1 chip) supports it on iOS 27 via Core Bluetooth. No Espressif chip has a Channel Sounding controller (C5, C61, H2 all "unsupported"), so the pucks would need a Nordic nRF54L15 (DK about US$39) or similar. Nordic claims about +/-1 m up to 20 m with a Pixel 10.

Facts:
- "Google Pixel 10 - any model works"; needs Android 16 QPR2 or Android 17; Pixel 9 works on latest Android 16; reference app nRF Toolbox 4.1.4+ (source: github.com/NordicSemiconductor/Android-nRF-Toolbox, `ChannelSoundingManager.kt`); board firmware "Channel Sounding Reflector with Ranging Responder" sample, nRF Connect SDK 3.0.1+, `android_ranging.conf`. https://devzone.nordicsemi.com/nordic/nordic-blog/b/blog/posts/evaluating-bluetooth-channel-sounding-with-our-open_2d00_source-android-app-on-google-pixel-10
- Nordic staff: tested on Pixel 9/10; Galaxy S26+/S26 Ultra "will both add Channel Sounding support it seems"; OnePlus 13 "should support ... but not tested"; a Bluetooth 6.0 label does not guarantee CS. https://devzone.nordicsemi.com/f/nordic-q-a/127111/ble-channel-sounding-with-nrf54l15-which-smartphones-are-actually-supported
- Apple engineer (June 2026): "For iOS 27, all iPhones with the N1 chipset will support channel sounding (iPhone 17)". https://developer.apple.com/forums/thread/833591
- Android: `android.ranging.ble.cs.BleCsRangingParams` (API 36) with `getPeerBluetoothAddress()`, `getLocationType()`, `getSightType()`, `getSecurityLevel()`, `getRangingUpdateRate()`. https://developer.android.com/reference/android/ranging/ble/cs/BleCsRangingParams ; BLE CS is foreground-only for third-party apps and data goes only to the initiator. https://developer.android.com/develop/connectivity/ranging
- nRF54L15-DK: US$39.00 at DigiKey. https://www.digikey.com/en/products/detail/nordic-semiconductor-asa/NRF54L15-DK/25601516
- Nordic press release (2025-10-28): "±1 m up to 20 m range"; all nRF54L SoCs support the reflector role. https://www.nordicsemi.com/Nordic-news/2025/10/Nordics-Android-app-and-nRF54L-Series-evaluate-Bluetooth-Channel-Sounding-using-the-Google-Pixel-10?lang=en
- Silicon Labs xG24: "sub-meter" accuracy (2024-09-03). https://news.silabs.com/2024-09-03-Silicon-Labs-Bluetooth-R-Channel-Sounding-Provides-Sub-Meter-Accuracy-to-Drive-Secure-Fine-Ranging
- Espressif: Channel Sounding "unsupported" on ESP32-C61, C5, H2 controllers; host stacks list it as "experimental" but the controller limits it. https://docs.espressif.com/projects/esp-idf/en/latest/esp32c61/api-guides/ble/ble-feature-support-status.html ; https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-guides/ble/ble-feature-support-status.html ; https://docs.espressif.com/projects/esp-idf/en/latest/esp32h2/api-guides/ble/ble-feature-support-status.html ; https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/ble/host-feature-support-status.html

Not confirmed:
- Official Samsung CS support statement; ESP32-C6 controller status page (not opened); TI CC27xx / NXP KW47 prices; whether iPhone 17 ranges to third-party reflectors such as nRF54L15.

## 7. Cold sensing (no app on the victim phone)

Plain answer: a passive scanner can hear BLE advertisements and Wi-Fi probe requests, but idle phones do not reliably advertise. Android phones are scanners for Fast Pair (the accessory advertises), Quick Share is visible to everyone only for 10 minutes, and Find Hub beacons are mainly for offline or powered-off phones; iPhones send Continuity advertisements mostly in response to user actions, plus Find My beacons when offline. Addresses rotate about every 15 minutes on both platforms. The honest output is a band count of radio emitters heard in a window, not people and not identities.

Facts:
- Fast Pair: the Seeker (phone) scans as GAP Central; the Provider (accessory) advertises. https://developers.google.com/nearby/fast-pair/specifications/service/provider
- Find Hub (formerly Find My Device): supported devices "like the Pixel 8 series" can be found for "several hours after it's turned off", only if the user enabled network finding and Bluetooth/Location were on at shutdown. https://support.google.com/android/answer/3265955?hl=en
- Quick Share visibility: "Your devices" (screen off OK, own devices only), "Contacts" (screen on and unlocked only), "Everyone for 10 minutes" (then reverts). https://support.google.com/files/answer/7577382
- Exposure Notifications API "no longer available" since Sep 18 2023. https://developers.google.com/android/exposure-notifications?hl=en
- Apple Continuity messages are embedded in BLE advertisements and leak identifiers, counters and battery levels usable for tracking (PoPETs 2020). https://crysp.petsymposium.org/popets/2020/popets-2020-0003.pdf ; they are sent in response to actions such as lock/unlock, copy/paste, calls (PoPETs 2019 "Handoff All Your Privacy"). https://arxiv.org/abs/1904.10600
- Apple Find My offline finding: an offline device "begins periodically broadcasting the derived public key"; "Approximately every 15 minutes, the public key is replaced". https://support.apple.com/guide/security/sec6cbc80fd0/web ; powered-off finding since iOS 15 on iPhone 11/12+. https://9to5mac.com/2021/07/21/ios-15-here-are-the-devices-that-support-find-my-when-turned-off/
- Address rotation: Android CDD "MUST implement a Resolvable Private Address (RPA) timeout no longer than 15 minutes, and rotate the address at timeout". https://android.googlesource.com/platform/docs/source.android.com/+/a13d876%5E%21/ ; 15 minutes "typically used by default" on iOS, Android, Windows. https://scitepress.org/publishedPapers/2023/121396/pdf/index.html
- Probe-request randomisation (iOS 8+, Android 6.0+, Windows 10, Linux 3.18) and de-randomisation by timing "in up to 75% of the cases" (WiSec 2016). https://papers.mathyvanhoef.com/wisec2016.pdf
- Counting literature: RateCount (2025) counts devices from probe-request rate with an unbiased estimator (no numbers in abstract). https://www.arxiv.org/abs/2507.03873 ; a 2025 BLE occupancy study only claims low/medium/high levels; wearables inflate counts, IoT adds noise, per-site calibration needed (read via a blog summary, not the paper). https://www.beaconzone.co.uk/blog/?p=10054
- ESP32 capabilities: Wi-Fi sniffer via `esp_wifi_set_promiscuous()` with `wifi_promiscuous_cb_t` callback (runs in Wi-Fi task; post heavy work elsewhere; throughput drops). https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/wifi-driver/wifi-modes.html ; NimBLE scanning via `ble_gap_disc` with `ble_gap_disc_params`. https://docs.espressif.com/projects/esp-techpedia/en/latest/esp-friends/advanced-development/ble-application-note/broadcasting.html
- Prior art: ESP32-Paxcounter "counts how many mobile devices are around", "does not persistenly store MAC adresses and does no kind of fingerprinting". https://github.com/cyberman54/ESP32-Paxcounter
- Legal/ethical: Spanish DPAs' 2024 guidance says Wi-Fi tracking usually processes personal data under GDPR. https://www.aepd.es/guides/wi-fi-tracking-technologies-guidance-for-data-controllers.pdf ; US FTC v. Nomi (2015): hashed MAC addresses were still persistent identifiers. https://www.ftc.gov/news-events/press-releases/2015/04/retail-tracking-firm-settles-ftc-charges-it-misled-consumers ; design implication: count in RAM, discard addresses each window, never store or transmit them.

What a passive scanner can honestly say: "In the last N minutes (N < 15), this puck heard K distinct BLE/Wi-Fi emitters above RSSI threshold T", reported as a band (0, 1-3, 4-10, 10+) with low confidence. It is not a headcount (one person may carry phone + watch + earbuds; many phones are silent; Bluetooth may be off), it cannot say which room (RSSI does not resolve walls), and zero does not mean empty. Label it "possible occupancy signal"; never a reason to skip a search.

Not confirmed:
- Whether online Android phones emit Find Hub beacons continuously; how long a powered-off iPhone stays findable; Quick Share advertisement format; Apple manufacturer id 0x004C field layouts; the `passive` field name in `ble_gap_disc_params` (check `ble_gap.h`); quantitative airport-study correlations (page blocked); UK ICO Wi-Fi analytics guidance (not reachable); US wiretap-law applicability to receiving broadcasts.

## 8. Noise models for the simulator

Plain answer: BLE RSSI is a weak distance signal (5 dB noise even in clean LOS, 10-20 dB swings from body and orientation, about 20 dB per blockwork wall; fitted indoor exponent about 2.8-3.4 with 8-11 dB shadowing; about 3 m RMSE). UWB is tight at chip level (DW3000: +/-6 cm calibrated, 1.5 cm sigma) but phones measure about 15-18 cm RMSE with rare multi-metre outliers; a single wall adds roughly 0 to +13 cm bias; body-worn real-world RMSE about 0.9 m. Indoor compass error is typically above 20 degrees; barometers give about 0.12 hPa per metre and 0.37-0.62 hPa per floor, usable only as relative pressure because per-phone offsets reach 1.4 hPa and weather drift 2 hPa.

(a) BLE RSSI:
- Phone RSSI at 1 m about -62 dBm (Pixel 2 pair); LOS std "around 5dB"; fluctuations "±5dB or greater"; receiver floor "around -90dB". https://arxiv.org/pdf/2006.06822
- Body: person rotating at 1 m changes RSSI about 20 dB; pocket front vs back about 15 dB; torso in path about 10 dB. Orientation change about 20 dB. Plasterboard stud wall "little signal attenuation"; 15 cm blockwork wall about 20 dB. https://arxiv.org/pdf/2006.06822
- NIST: body blocking 11.55 dB mean (std 5.52); orientation 9.96 dB mean (std 6.68); same-model unit-to-unit 3 dB mean (std 1.54); single fit over 144,581 samples sigma^2 = 129.15 (sigma about 11.4 dB). (Raspberry Pi wearables, not phones.) https://nvlpubs.nist.gov/nistpubs/ir/2022/NIST.IR.8437.pdf
- ITU-R P.1238-3 (1.8-2 GHz rows): N = 28 residential, 30 office, 22 commercial (n = 2.8/3.0/2.2); shadow sigma 8/10/10 dB; office floor loss 15 + 4(n-1) dB. https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1238-3-200304-S!!PDF-E.pdf
- Per-material 2.4 GHz (vendor database): drywall partition 5.4 dB, lime brick 4.3 dB, heavy concrete 22.8 dB, chipboard 0.5 dB. https://blog.ibwave.com/?p=3095
- Exposure-Notification calibration examples: Samsung XCover TX -24 dBm / RX correction 5 dB; Samsung S7 TX -33 dBm / RX correction 10 dB (9 dB TX spread across two models). Log-distance fit PL0 = 47.51 dB at d0 about 2.5 m, gamma = 3.36, sigma = 8.48 dB; body shielding spread up to 11.69 dB (trunk); distance RMSE 3 m BLE vs 0.9 m UWB. https://arxiv.org/pdf/2101.09075
- GAEN attenuation = TX power (in beacon metadata) - (filtered RSSI + calibration). https://arxiv.org/pdf/2006.08543
- iBeacon Measured Power = averaged RSSI at 1 m over at least 10 s. https://developer.apple.com/ibeacon/Getting-Started-with-iBeacon.pdf

(b) UWB TWR:
- DW3000 datasheet v1.1: ranging accuracy +/-6 cm typical after calibration (about +/-15 cm without), ranging std 1.5 cm at -85 dBm LOS; PDoA +/-10 deg (std 5 deg), AoA about 2x better. (Third-party mirror of the Qorvo PDF.) https://atta.szlcsc.com/upload/public/pdf/source/20220621/2EAA42E8F3E2D83DDB8686FF8DCFAD44.pdf
- Android: UWB measures "to an accuracy of 10 cm". https://developer.android.com/develop/connectivity/uwb
- Apple HIG: UWB field of view "similar to that of the Ultra Wide camera"; outside it, distance only; bodies and large objects reduce accuracy (no numbers). https://developer.apple.com/design/human-interface-guidelines/nearby-interactions
- Phones measured (Pixel 6 Pro, Galaxy S21 Ultra, iPhone 12 Pro, DW3000EVB): uncalibrated RMSE 15.0-17.7 cm (DW3000 16.0); only 20-60% of readings within +/-10 cm; outliers up to -5 m and +2.79 m; max range seen iPhone 40 m (capped), Galaxy 23 m (capped), Pixel 11.6 m. https://ar5iv.labs.arxiv.org/html/2303.11220
- NLOS bias per material (DW1000, 3.86 m, medians read from plot): drywall about 0, concrete about +0.09 m, paver brick about +0.13 m; refraction adds positive bias. https://nsl.cs.uh.edu/papers/uwbnlos-cpsbench18.pdf
- Body shadowing "significantly attenuates the direct-path" signal (abstract, no number). https://research.ucc.ie/en/publications/the-effect-of-human-body-shadowing-on-indoor-uwb-toa-based-rangin/

(c) Phone sensors:
- Compass: raw error "can be 26°" walking indoors; over 95% of a test corridor above 23°; 45-50° in high-interference areas. https://sinrgwebpage.web.illinois.edu/papers/walkcompass.pdf
- Bosch BMP390: relative accuracy +/-0.03 hPa ("equivalent to ±25 cm"); absolute +/-0.50 hPa. https://www.bosch-sensortec.com/products/environmental-sensors/pressure-sensors/pressure-sensors-bmp390.html
- FloorPair: 0.12 hPa per metre; floor-to-floor -0.37 to -0.62 hPa; same-model drift up to 2 hPa; four devices at one spot differed up to 1.4 hPa; floor-pair method "near 100%". https://pmc.ncbi.nlm.nih.gov/articles/PMC6720727/

Suggested simulator parameters:

| Parameter | Value | Source |
|---|---|---|
| BLE RSSI at 1 m (phone) | -62 dBm, plus per-model offset | https://arxiv.org/pdf/2006.06822 |
| Path-loss exponent n | 3.0 (2.2-3.4 range) | ITU-R P.1238-3; https://arxiv.org/pdf/2101.09075 |
| Shadowing sigma | 8 dB (5 dB clean LOS, 11 dB mixed) | ITU-R P.1238-3; NIST IR 8437 |
| Per-model TX offset | uniform +/-4.5 dB | https://arxiv.org/pdf/2101.09075 |
| Same-model unit offset | N(3, 1.54) dB | NIST IR 8437 |
| Body shadowing | N(11.5, 5.5) dB clipped 0-20 | NIST IR 8437; https://arxiv.org/pdf/2006.06822 |
| Orientation term | N(0, 6.7) dB | NIST IR 8437 |
| Wall: plasterboard | 0-5 dB | https://arxiv.org/pdf/2006.06822 ; https://blog.ibwave.com/?p=3095 |
| Wall: brick/blockwork | 4-20 dB | https://blog.ibwave.com/?p=3095 ; https://arxiv.org/pdf/2006.06822 |
| Wall: heavy concrete | about 23 dB | https://blog.ibwave.com/?p=3095 |
| Floor loss (office) | 15 + 4(n-1) dB | ITU-R P.1238-3 |
| BLE drop threshold | -90 to -100 dBm | https://arxiv.org/pdf/2006.06822 |
| UWB LOS (DW3000-DW3000) | bias +/-6 cm, sigma 1.5 cm (use 5 cm uncalibrated) | DW3000 datasheet |
| UWB LOS (phone) | sigma about 14 cm, RMSE about 16 cm | https://ar5iv.labs.arxiv.org/html/2303.11220 |
| UWB outliers | rare, -5 m to +2.8 m | https://ar5iv.labs.arxiv.org/html/2303.11220 |
| UWB NLOS bias per wall | +0 to +0.13 m (more for composite walls) | https://nsl.cs.uh.edu/papers/uwbnlos-cpsbench18.pdf |
| UWB body-worn realistic RMSE | 0.9 m | https://arxiv.org/pdf/2101.09075 |
| UWB AoA error | about +/-5 deg inside FoV only | DW3000 datasheet; Apple HIG |
| UWB max LOS range (phone) | 11.6-40 m by model | https://ar5iv.labs.arxiv.org/html/2303.11220 |
| Compass heading error indoors | slow-varying bias 20-26 deg, 45-50 deg near interference | https://sinrgwebpage.web.illinois.edu/papers/walkcompass.pdf |
| Baro gradient / per floor | 0.12 hPa/m; 0.37-0.62 hPa/floor | https://pmc.ncbi.nlm.nih.gov/articles/PMC6720727/ |
| Baro relative noise | +/-0.03 hPa | Bosch BMP390 page |
| Baro per-device offset / drift | up to 1.4 hPa / 2 hPa | https://pmc.ncbi.nlm.nih.gov/articles/PMC6720727/ |
| Wi-Fi FTM phone/ESP32 | 1-3 m error | Section 5 sources |

Not confirmed:
- iBeacon -59 dBm typical Measured Power; full GAEN per-device CSV (Google URL deprecated); ITU-R P.2040 material values; a 2.4 GHz row in P.1238; a cited LOS exponent of 1.6-2.2; Apple's numeric AoA accuracy or FoV; FiRa accuracy claims; DW3000 max LOS range number; phone UWB range through walls; BMP388/BMP580 specs.

## Bottom line for the owner's hardware

Runs tonight on 5 ESP32 + DW3000 boards plus an unknown Android phone:
1. Puck-to-puck UWB ranging (DW3000 to DW3000) with the Makerfabs ESP32 library (https://github.com/Makerfabs/Makerfabs-ESP32-UWB-DW3000); its examples are SS-TWR (a Qorvo forum user notes DS-TWR must be adapted: https://forum.qorvo.com/t/esp32-dw3000-ds-twr-sample-code/14367, search summary). Use this for puck self-localisation (simulate with sigma 5 cm, NLOS +0-13 cm per wall).
2. Phone-to-puck ranging over BLE RSSI: the opt-in app advertises a 16-bit service-data beacon from a `connectedDevice` foreground service (`BLUETOOTH_ADVERTISE`, `FOREGROUND_SERVICE_CONNECTED_DEVICE`); pucks scan with NimBLE and estimate distance with the Section 8 model. Works on any BLE Android phone. Expect metres of error; use it for "which puck is nearest" and trend, not position.
3. Cold sensing: NimBLE passive scan plus Wi-Fi promiscuous probe sniffing on the pucks, reporting a device-count band per window, addresses discarded (Section 7).
4. Phone sensors from the app: barometer floor estimate (relative to a reference puck/phone at a known floor) and compass heading with a 20-30 degree error budget.
5. Wi-Fi FTM: only if the boards are the MaUWB ESP32-S3 variant (S3 supports FTM responder; the classic ESP32-WROVER does not) and only if the phone supports RTT (Pixel 1-9 and many Samsung/Xiaomi per Google's table). Expect 1-3 m, flaky (Section 5).
6. Phone UWB on the owner's phone: works only phone-to-phone (two UWB Android phones via Jetpack, or Android-to-iPhone via `android.ranging`), not phone-to-puck. First check `PackageManager.hasSystemFeature("android.hardware.uwb")` and the Section 2 list.

Does NOT run tonight:
- Phone-to-puck UWB with the ESP32 + DW3000 boards. No open FiRa MAC for ESP32 exists; Qorvo's FiRa MAC is a closed ARM binary (Section 3).

Needs a part they do not have:
- Qorvo DWM3001CDK (nRF52833 + DW3110, runs Qorvo UCI/FiRa firmware; community-proven with Pixel 8 Pro / 9 Pro via https://github.com/sasodoma/uwb-ranging): about US$29.50 (distributor listing seen in search results, not opened) https://rfmw.com/products/detail/dwm3001cdk-qorvo/692466/ ; DigiKey listing https://www.digikey.com/en/products/detail/qorvo/DWM3001CDK/24367348 . Requires a UWB phone from Section 2 and manual or BLE OOB.
- Nordic nRF54L15-DK for Bluetooth Channel Sounding (about +/-1 m to 20 m claimed): US$39.00 https://www.digikey.com/en/products/detail/nordic-semiconductor-asa/NRF54L15-DK/25601516 . Requires a Pixel 9 (Android 16 QPR2+) or any Pixel 10.
- An ESP32-S2/S3/C3/C6 board if the owner's boards turn out to be classic ESP32 and they want an FTM rung (any S3 dev board; price not researched here).
