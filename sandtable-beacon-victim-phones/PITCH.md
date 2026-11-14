# Beacon: the two-minute demo

Two versions of the same script: **live** (two DWM3001CDK pucks, the iPhone 14 and the iPhone 15, the board on the laptop) and **simulated** (the board's walk-through and the simulated phone page). Say which one you are showing. The single ah-ha is marked.

## Table set-up (before the judge arrives)

- A taped rectangle on the floor or table standing in for Suite 310 (at exercise scale 3: 3.17 m by 2.88 m), with one long edge marked "door wall". `hardware/LAYOUT.md` has the drawing.
- Puck A (battery bank, beside the UNO Q) at the door mark; puck B (on the Android phone's USB-OTG) in the presenter's hand.
- The board open on the laptop, `--source live --lan`, exercise scale set, puck A already clicked onto the plan; search room off.
- The Beacon app open on the iPhone 14, lying inside the rectangle, far from the door wall. The iPhone 15 in a teammate's pocket.
- Fallback ready in a second browser tab: the same board with `--story` (simulated).

## Script

| Time | Say | Do | On the board |
|---|---|---|---|
| 0:00 | "In smoke you cannot see a person three metres away. Cameras cannot, LiDAR cannot. But nearly everyone trapped in a building is holding a radio that is accurate to a hand's width: their phone." | Hold up the iPhone | Suite 310 on the twin, one puck at the door, no phones |
| 0:12 | "This is the occupant's side. No account, no name. One button." | Press **Trapped**, set 2 people, cannot move | A phone card appears: trapped, 2 people |
| 0:22 | "The crew's first puck hears it over ultra-wideband. One puck gives a distance, so the honest answer is a ring, and the building's twin cuts away the parts that are outside the wall." | Point at the ring | A ring around puck A, clipped by the twin |
| 0:35 | "Second puck. Dropped anywhere, two rings cross in two places, and our board will not pick one for you." | Set puck B down mid-room, click it onto the plan | Two "?" spots: **cannot choose** |
| 0:50 | **Ah-ha.** "Slide it to the wall the door is in. Now the mirror spot is on the other side of that wall, where nobody in this room can be. Two pucks, one position." | Slide puck B along the door wall, drag it on the plan | The second spot is crossed out with its reason; one position, to about half a metre |
| 1:05 | "Walk it." | A teammate carries the iPhone across the rectangle | The dot follows |
| 1:15 | "Now the part only the building record can do. The record says a dentist leases this suite: sedated patients, about four people at this hour, at most nine. Two phones account for three people. One person pressed I am out. The rest are unaccounted for." | Second victim presses **Injured** on the iPhone 15; a third presses **I am out** (simulated phone tab) | Accountability row for 310: expected, inside, safe outside, unaccounted band |
| 1:35 | "The command agent turns that into orders: rescue team to this spot by this stair, and the next puck goes to Suite 301, the outpatient department nobody has scanned yet." | Press **Ask the command agent** | Agent steps, radio orders, the next-puck marker at Suite 301's door |
| 1:50 | "Everything here that is simulated says so. With no app we show at most a count of Bluetooth devices, never a dot. And the same feed drives the Sandtable command board." | Point at the SIM or LIVE badge and the feeds panel | |
| 2:00 | Stop. | | |

If the live path fails at any step, say "this part is simulated" and switch to the walk-through tab: it plays the same sequence (ring, cannot choose, slide to the wall, one position, second phone, accountability, agent) in about 40 seconds.

## The 20-second version inside Sandtable's own demo

"Those pucks do one more thing. If a trapped person opens our app, each puck ranges their iPhone over ultra-wideband. One puck: a ring. Two pucks on the door wall: one position, because the twin knows what is behind that wall. And the tenant roster tells command how many people that suite should still hold." (Show: ring, slide the puck, the dot, the accountability row.)

## What to say when asked

- **Is that real?** "The positions on this screen are [live from these two boards and this phone / simulated, with the noise model on screen]. The firmware on the boards is Qorvo's stock image; the app and everything behind it are ours."
- **Does the victim need your app?** "Yes. A phone's ultra-wideband radio only answers an app its owner opened, by Apple's design, and we think that is right. Without the app we can only count Bluetooth devices nearby, with no identity, and we show it as a count."
- **Who installs an app before a fire?** "The building does it: the app's suite list comes from the building record, so a property manager can roll it out with the tenant directory. Schools, hospitals and high-rises already push emergency apps. And the drill version is useful on its own."
- **How accurate?** "Simulated, with a noise model built from published measurements: about a quarter of a metre median with two well-placed pucks, and it gives one position for about three quarters of the spots in the room; the rest stay a ring, and it says so. We have not measured it on these boards yet." (Numbers: `results/eval_two_puck.json`.)
- **Why two pucks and not one device with two boards?** "We simulated it: two single-antenna boards 30 cm apart give a bearing error of about 17 degrees and never a position. Two pucks four metres apart do."
- **Screen off?** "Tonight the app must stay open. Apple offers two background paths; with the pucks reporting ranges themselves the phone could go dark. That is firmware we did not write this weekend."
- **Two victims?** "One board serves one phone at a time on the stock firmware, so the phones take turns, or each puck position carries two boards."
- **What is the CoStar data doing?** "It is the roster. Tenants by suite, kind of business, area: who should be there, who is vulnerable, which suites are empty. The twin gives the walls that make two pucks enough. Neither is decoration: remove either and the answer on screen changes." (Honest: fixture data here, labelled; the adapter mapping is in `docs/COSTAR_TIE_IN.md`.)
- **Privacy?** "Opt-in, a random id that rotates, the app shows exactly what it sends, stop sharing deletes it, fifteen-minute retention. `docs/PRIVACY.md`."
