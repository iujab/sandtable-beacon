# Can thrown pucks find the phones of the people inside? What works, what does not, and why

Written Sat Nov 14 2026 from web research done the same day. The full source lists are in `docs/research/ios_ni.md`, `docs/research/puck_hardware.md` and `docs/research/android_ble_models.md`; the links below are the load-bearing ones. Nothing here has been run on hardware. Simulated numbers come from `results/eval_layouts.json` and `results/eval_beacon.json` and are marked as simulated.

The owner's hardware, as confirmed at noon: five Qorvo DWM3001CDK boards (nRF52833 + DW3110, Bluetooth on board, on-board J-Link), an iPhone 14 (first-generation UWB chip, U1), an iPhone 15 (second-generation chip, U2), a Mac with Xcode, an Arduino UNO Q with a battery bank, an Android phone.

## 1. Can a UWB board range a victim's phone over UWB at all?

**Yes, for an iPhone whose owner opened our app, with exactly the boards the owner has.**

- Apple lets a third-party accessory range an iPhone through the Nearby Interaction framework (`NINearbyAccessoryConfiguration`). The accessory must carry a UWB chipset whose vendor implements Apple's accessory protocol; the app and the accessory swap two opaque configuration blobs over any link (Bluetooth in practice), then the phone and the accessory range each other over UWB. [Apple: Nearby Interaction](https://developer.apple.com/nearby-interaction/), [NINearbyAccessoryConfiguration](https://developer.apple.com/documentation/nearbyinteraction/ninearbyaccessoryconfiguration)
- The Qorvo DWM3001CDK is the cheapest kit with a working stack: Qorvo's "Nearby Interaction" firmware (QANI) ships as a prebuilt image for this board, with the UWB driver, the configuration-blob generator and the MAC as closed Cortex-M4 libraries. [Qorvo forum: QNI package v3.0](https://forum.qorvo.com/t/qorvo-nearby-interaction-software-package-v-3-0-release/13390), [working report, Feb 2026](https://forum.qorvo.com/t/how-to-use-dwm3001cdk-with-apple-ni/23229). Price for reference: USD 29.50 at [DigiKey](https://www.digikey.com/en/products/detail/qorvo/DWM3001CDK/24367348).
- Plan: **stock, unmodified Qorvo firmware** on the boards, our own iOS app, our own server. No firmware is built here. The stock firmware advertises a Qorvo Bluetooth service (`2E938FD0-6A61-11ED-A1EB-0242AC120002`); the app connects, exchanges the blobs with the message ids of Apple's sample (`0x0A` initialize, `0x01` accessory configuration, `0x0B` configure and start), and runs one `NISession` per puck. [Qorvo forum: service UUIDs](https://forum.qorvo.com/t/issues-with-apple-nearby-interaction-app-not-detecting-dwm3001cdk-accessory/20321), [Apple sample](https://developer.apple.com/documentation/nearbyinteraction/implementing-spatial-interactions-with-third-party-accessories)
- What each phone gives: **distance on both** (about 60 updates a second reported with this board). **Direction on neither**: developers report `supportsDirectionMeasurement == false` and a nil `direction` for accessories on the iPhone 14 family and later, including an iPhone 15 with this exact board. Camera assistance can add a horizontal angle, but only after about ten seconds of moving the phone with the camera seeing a lit scene. [Apple forum 822522](https://developer.apple.com/forums/thread/822522), [720292](https://developer.apple.com/forums/thread/720292). The second-generation chip brings no extra range with accessories. [Apple: extended distance is Apple-to-Apple only](https://developer.apple.com/documentation/nearbyinteraction/extending-advanced-direction-finding-and-ranging)
- One iPhone can run several accessory sessions at once, so both pucks can range the same phone. **One board serves one phone at a time**: forum reports say the stock firmware is built for a single connection and stops advertising while connected ([24451](https://forum.qorvo.com/t/does-dw3-qm33-sdks-qani-project-support-multiple-fira-sessions/24451), [23706](https://forum.qorvo.com/t/dwm3001cdk-apple-nearby-interaction-with-multiple-iphonse-simultaneously/23706), [18890](https://forum.qorvo.com/t/dwm3001cdk-firmware-to-do-nearby-interaction-twr-with-multiple-phones-via-multiplexing/18890)). So two victims on two boards either take turns (the app's "Take turns" switch: range a board for about 4 s, release it, take the other; never tested) or each puck position carries two boards, one per phone (the app's per-board toggles).
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
- **Android phone: a power source, not a host.** It can power puck B over USB-OTG, which is what the owner chose. It cannot read the stock firmware, which prints nothing on a serial port. What can go wrong: some phones switch the 5 V off when no USB device shows up, or after an idle timeout, so use the board's J-Link port (its chip enumerates as a USB device); keep a second battery bank as the fallback. The same phone can also be the Wi-Fi hotspot that the laptop and both iPhones join.
- **Where the server runs tonight:** on the laptop, on a phone hotspot (venue Wi-Fi usually blocks device-to-device traffic). The iPhones post to it over plain HTTP; the app's Info.plist carries the App Transport Security exception, and the board shows a QR code that fills in the address.
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

<!-- LAYOUT_NUMBERS:BEGIN -->
**Simulated** (`results/eval_layouts.json`; 50 trials per row, phone 2 to 8 m away in the fire room, pucks thrown not placed; noise model with sources in `beacon/models.py`). Bearing error is measured after choosing the nearer of the two mirror solutions, so the mirror ambiguity is on top of it:

| Layout | Bearing error to the phone, median | Sideways (cross-range) error, median | Mirror ambiguity left | A position is published | Position error when published, median | Area the phone is confined to, median |
|---|---|---|---|---|---|---|
| Two boards on one device, 0.1 m apart | 22 degrees | 1.71 m | 100% | 0% | n/a | 5.8 m2 |
| Two boards on one device, 0.3 m apart | 17 degrees | 1.34 m | 100% | 0% | n/a | 4.8 m2 |
| Two boards on one device, 0.5 m apart | 14 degrees | 1.29 m | 98% | 0% | n/a | 4.2 m2 |
| Two separate pucks 2 m apart | 8 degrees | 0.64 m | 72% | 28% | 0.77 m | 2.0 m2 |
| Two separate pucks 3 m apart | 5 degrees | 0.38 m | 60% | 40% | 0.36 m | 1.4 m2 |
| Two separate pucks 4 m apart | 5 degrees | 0.36 m | 42% | 58% | 0.43 m | 1.2 m2 |
| Three separate pucks (thrown) | n/a | n/a | n/a | 80% | 0.22 m | 0.8 m2 |

Two boards on one device never produce a position: the locator publishes a ring. Two separate pucks a few metres apart confine the phone to a much smaller area with a several times better bearing, and the twin removes the mirror solution in part of the cases even with thrown pucks (the placed layouts are in `results/eval_two_puck.json`).
<!-- LAYOUT_NUMBERS:END -->

**Decision (the owner, 12:35): two separately powered pucks.** Puck A on a battery bank beside the UNO Q, puck B on the Android phone over USB-OTG. Do not mount two boards on one device.

**Making two pucks give one answer.** Two rings always leave two mirror points across the line through the pucks. Put both pucks just inside the wall the door is in (0.35 m inside, about 4 m apart: one beside the door, one slid along the wall; `beacon/layout.py`). Then the mirror image of anyone in the room falls outside the room:

- for a phone deep in the room it falls beyond the corridor, two walls away, where a ranging link is not expected at all: the twin removes it;
- for a phone within about 2.75 m of that wall it falls in the corridor, where the crew is standing. The twin does not forbid it, so the board dismisses it only on something that was said: the occupant's own suite in the app, or command's "we are searching this room" switch. It is shown as dismissed, with the reason;
- a careless layout (pucks mid-room) leaves two candidates inside the room that nothing can decide, and the board says "cannot choose".

<!-- TWO_PUCK_NUMBERS:BEGIN -->
**Simulated** (`results/eval_two_puck.json`; 800 phone positions per layout over the fire room; what a real iPhone would deliver: UWB distances only, no line-of-sight flag; noise model with sources in `beacon/models.py`):

| Layout | One position | of which by the twin alone | of which after the occupant's stated suite | Cannot choose (two spots) | Ring or less | Wrong position, share of all trials | Position error, median / 90th percentile |
|---|---|---|---|---|---|---|---|
| Both pucks just inside the wall the door is in (recommended) | 77% | 48% | 29% | 0% | 23% | 1.1% | 0.26 m / 0.76 m |
| Both pucks along the outside wall | 83% | 78% | 5% | 0% | 17% | 1.8% | 0.26 m / 0.76 m |
| Both pucks mid-room (careless) | 18% | 17% | 0% | 57% | 25% | 0.0% | 0.50 m / 0.96 m |
| Three pucks: the door-wall pair plus one in the far corner | 88% | 84% | 5% | 3% | 9% | 1.0% | 0.20 m / 0.61 m |

"Ring or less" is mostly a phone that only one puck reached (a blocked link in the simulation). A wrong position is one more than three sigma or 1.5 m from the truth.

When the occupant's stated suite is false (the phone is really in a neighbouring room, 300 trials): the statement produces a wrong position in 4.3% of trials with the door-wall layout, and in 13.8% for the suite that shares that wall. That is the price of dismissing a spot on a statement, and why the board shows the dismissal.
<!-- TWO_PUCK_NUMBERS:END -->

**Better if you have it:** a third puck, not in line with the other two, gives a position with no assumption at all.

**Real fire:** separate pucks, thrown apart, three or more per room. If one throw is all there is, the device needs a rigid baseline of half a metre or more, or better a dual-antenna radio (DW3120 or DW3220 class) that measures angle from phase.

## 7. What is still unknown until the first power-up

In the order the owner will meet them: whether the boards already carry the Nearby Interaction firmware; whether Qorvo's download form accepts the owner's email address; the boards' Bluetooth names (the app shows a key per board); whether one board serves two phones at once; the real range error of an iPhone 14 and 15 against this board in the room; what the debug channel prints.
