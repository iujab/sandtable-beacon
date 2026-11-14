# Can thrown pucks find the phones of the people inside? What works, what does not, and why

Written Sat Nov 14 2026 from web research done the same day. The full source lists are in `docs/research/ios_ni.md`, `docs/research/puck_hardware.md` and `docs/research/android_ble_models.md`; the links below are the load-bearing ones. Nothing here has been run on hardware. Simulated numbers come from `results/eval_layouts.json` and `results/eval_beacon.json` and are marked as simulated.

The owner's hardware, as confirmed at noon: five Qorvo DWM3001CDK boards (nRF52833 + DW3110, Bluetooth on board, on-board J-Link), an iPhone 14 (first-generation UWB chip, U1), an iPhone 15 (second-generation chip, U2), a Mac with Xcode, an Arduino UNO Q with a battery bank, an Android phone.

## 1. Can a UWB board range a victim's phone over UWB at all?

**Yes, for an iPhone whose owner opened our app, with exactly the boards the owner has.**

- Apple lets a third-party accessory range an iPhone through the Nearby Interaction framework (`NINearbyAccessoryConfiguration`). The accessory must carry a UWB chipset whose vendor implements Apple's accessory protocol; the app and the accessory swap two opaque configuration blobs over any link (Bluetooth in practice), then the phone and the accessory range each other over UWB. [Apple: Nearby Interaction](https://developer.apple.com/nearby-interaction/), [NINearbyAccessoryConfiguration](https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration)
- The Qorvo DWM3001CDK is the cheapest kit with a working stack: Qorvo's "Nearby Interaction" firmware (QANI) ships as a prebuilt image for this board, with the UWB driver, the configuration-blob generator and the MAC as closed Cortex-M4 libraries. [Qorvo forum: QNI package v3.0](https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390), [working report, Feb 2026](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229). Price for reference: USD 29.50 at [DigiKey](https://www.digikey.com/en/products/detail/qorvo/DWM3001CDK/24367348).
- Plan: **stock, unmodified Qorvo firmware** on the boards, our own iOS app, our own server. No firmware is built here. The stock firmware advertises a Qorvo Bluetooth service (`2E938FD0-6A61-11ED-A1EB-0242AC120002`); the app connects, exchanges the blobs with the message ids of Apple's sample (`0x0A` initialize, `0x01` accessory configuration, `0x0B` configure and start), and runs one `NISession` per puck. [Qorvo forum: service UUIDs](https://forum.qorvo.com/t/issues-with-apple-nearby-interaction-app-not-detecting-dwm3001cdk-accessory/20321), [Apple sample](https://developer.apple.com/documentation/nearbyinteraction/implementing-spatial-interactions-with-third-party-accessories)
- What each phone gives: **distance on both** (about 60 updates a second reported with this board). **Direction on neither**: developers report `supportsDirectionMeasurement == false` and a nil `direction` for accessories on the iPhone 14 family and later, including an iPhone 15 with this exact board. Camera assistance can add a horizontal angle, but only after about ten seconds of moving the phone with the camera seeing a lit scene. [Apple forum 822522](https://developer.apple.com/forums/thread/822522), [720292](https://developer.apple.com/forums/thread/720292). The second-generation chip brings no extra range with accessories. [Apple: extended distance is Apple-to-Apple only](https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging)
- One iPhone can run several accessory sessions at once, so several pucks can range the same phone. How many phones one board serves at once is not documented for the stock firmware: assume one, which is why the app has a "max pucks" setting (three for one phone, two for the other, out of five boards).
- Where the number comes out: with the stock firmware the phone app receives the distance, and the board only writes it to its debug channel (SEGGER RTT on the J-Link port), not to a serial port. [Qorvo forum 13613](https://forum.qorvo.com/t/dwm3001cdk-nearby-interaction-trying-to-get-ranging-via-usb-uart/13613). So tonight **the app is the reporter**: it posts each range to the Beacon server over Wi-Fi. A puck host reading the debug channel is written as an experimental second path.
- What would not have worked: an ESP32 driving a DW3000 (the boards we first assumed). The radio is capable but the two closed libraries exist only for Cortex-M4 and M33. [Qorvo forum 10796](https://forum.qorvo.com/t/apple-ni-configuration-data-frame/10796?page=2)
- Android phones (not needed now): the same board is community-proven with Jetpack UWB on Pixel Pro models, but only Pro, Ultra and Fold class phones have UWB, and a backgrounded app gets no ranges. Details in `docs/research/android_ble_models.md`.

## 2. Can it be done cold, to a phone whose owner installed nothing?

**No. Not over UWB, and not with any identity.**

- Every UWB mode of an iPhone is started by Apple software with consent or pairing; nothing answers a stranger's poll. [Apple: Nearby Interaction](https://developer.apple.com/documentation/nearbyinteraction), [Ghost Peak](https://ar5iv.labs.arxiv.org/html/2111.05313)
- What a radio can hear cold is Bluetooth advertising: iPhones emit Apple Continuity messages constantly, from a random address that rotates about every 15 minutes. [Celosia and Cunche, PETS 2020](https://petsymposium.org/popets/2020/popets-2020-0003.pdf), [Martin et al., PETS 2019](https://arxiv.org/pdf/1904.10600)
- So the honest cold product is **a count band of devices near a scanner, by signal strength, with no identity**: it over-counts (watches, laptops, earbuds, address rotation) and under-counts (phones off or out of range). Devices are not people. [why absolute counts are unreliable](https://arxiv.org/html/2402.09158v1)
- On the owner's hardware: the stock puck firmware does not scan. The UNO Q's own radio can (`python -m beacon.host --cold-scan`, counts only, written but never run). It is a minor rung.
- Everything else needs the opt-in app: status, people count, UWB ranging.

## 3. One puck or two? What each geometry gives

| Pucks ranging the phone | What the geometry supports | What the board draws |
|---|---|---|
| 1 | a circle around the puck | a ring, clipped where the twin says nobody can be (outside the building, inside a wall) |
| 2 | two points, mirror images across the line between the pucks | two candidates; one is deleted when it falls outside the building, in a wall, or behind a wall while the link is clearly line of sight |
| 3, not in a line | one point | a fix with its uncertainty |
| 1 + the phone's own bearing | one point | a fix, but the bearing is not available on an iPhone 14 or 15 without camera assistance, so do not count on it |

The locator never publishes `x`, `y` unless the measurements support a single point. Simulated numbers for each row are in section 6.

## 4. Will the phone keep answering with the screen off?

**As built tonight: no. Keep the app open; it keeps the screen awake.** The two ways to change that, neither built:

- The paired path (iOS 16 and later): the accessory is Bluetooth-paired and implements Apple's background service; the session then keeps running with the app in the background, but **the app receives nothing; only the accessory has the measurements**. That suits this product (the puck reports), and Qorvo's firmware package includes a background mode, but its ranges come out of the debug channel only. [WWDC22 session 10008](https://developer.apple.com/videos/play/wwdc2022/10008/)
- The Live Activity path (iOS 18.4 and later): an app that starts a Live Activity as it goes to the background may keep ranging. Whether the app keeps receiving distance callbacks with the screen off is not stated; it must be tested on a device. [Apple: Nearby Interaction overview](https://developer.apple.com/documentation/nearbyinteraction)
- Bluetooth alone survives the background better: a central with a live connection is woken for each event. That keeps the status channel alive, not the ranging.

## 5. What does a puck need, and what should host it?

- **Power only.** With the stock firmware the board needs 5 V on either micro-USB port and nothing else; the range returns through the iPhone app. [Qorvo product brief](https://www.firaconsortium.org/sites/default/files/2024-08/DWM3001CDK%20Product%20Brief.pdf)
- **Recommended tonight: one USB battery bank per puck.** Some banks switch off at this low current; plugging into the J-Link port adds 10 to 15 mA of load, or use a wall adapter.
- **UNO Q as a host:** useful as the command post in a box (it can run the Beacon server) and as a diagnostic reader of one puck's debug channel. It acts as a USB host only when powered through a USB-C dongle with power pass-through. See `hardware/UNO_Q.md`.
- **Android phone as a host: not worth it.** It can power a puck over USB-OTG, which is a fine emergency supply. It cannot read the stock firmware, which prints nothing on a serial port. Use it as a power source or leave it out.
- Placement matters more than the host: board upright, 15 cm or more off the floor, component-free side toward the phone. A board lying flat on the floor is the worst case.
- The pucks cannot measure their own positions tonight: the stock firmware does not range board to board. Their positions are measured with a tape and clicked onto the plan.

## 6. Two boards on one thrown device, or two separate pucks? (the owner's two questions)

Closed form first. Two boards a distance b apart see a range difference of b cos(theta) for a far phone at angle theta from the line through them, so the bearing error is sqrt(2) x (range error) / (b x |sin theta|). A constant offset between the two links does not average away.

| Range error per link | b = 0.1 m | b = 0.3 m | b = 0.5 m |
|---|---|---|---|
| 5 cm | about 40 degrees: useless | 13.5 degrees | 8 degrees |
| 14 cm (phones in rooms) | useless | 38 degrees | 23 degrees |
| a 10 cm offset between the two links | useless | 19 degrees of bias | 11.5 degrees of bias |

At 5 m, 20 degrees of bearing error is 1.8 m sideways, and the mirror ambiguity is still there. Two separate pucks 3 m apart with the phone 5 m out give about 2.5 times the range error along the line between them and 0.7 times across it: tens of centimetres, plus the same mirror ambiguity, which the twin can often remove.

<!-- LAYOUT_NUMBERS: filled from results/eval_layouts.json -->

**Recommendation.**
- **Live demo:** three separately powered pucks about 2 to 3 m apart, not in a line (a fix), with the fourth and fifth boards for the second phone (two candidates that the twin prunes). Do not mount two boards on one device.
- **Real fire:** separate pucks, thrown apart, three or more per room. If one throw is all there is, the device needs a rigid baseline of half a metre or more, or better a dual-antenna radio (DW3120 or DW3220 class) that measures angle from phase.

## 7. What is still unknown until the first power-up

In the order the owner will meet them: whether the boards already carry the Nearby Interaction firmware; whether Qorvo's download form accepts the owner's email address; the boards' Bluetooth names (the app shows a key per board); whether one board serves two phones at once; the real range error of an iPhone 14 and 15 against this board in the room; what the debug channel prints.
