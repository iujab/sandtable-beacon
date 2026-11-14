# Privacy

Beacon locates people's phones, so the privacy design is part of the product, not an afterthought.

## Opt-in by design

- A phone is ranged, located or named on the board only if its owner installed the Beacon app and pressed a status button. UWB ranging cannot be started any other way: the phone's operating system requires the app, a permission prompt (Nearby Interactions) and a session the app itself starts.
- The app shows exactly what it sends (the last batch, as text, on the main screen). "Stop sharing" ends the sessions and tells the server to forget the phone at once.
- "I am out" marks the person as accounted for and stops locating them.

## What the app sends

| Sent | Not sent |
|---|---|
| a random 8-digit id that changes every 10 minutes | name, phone number, Apple ID, contacts, photos |
| distance to each puck, Bluetooth signal strength of each puck | GPS position, Wi-Fi networks, browsing or app data |
| the status the person chose, how many people are with them, whether they can move, a note they typed | microphone or camera content (the camera is used only if the person switches on camera-assisted direction, and no image leaves the phone) |
| battery level, whether the phone is still, barometric pressure, the storey and suite they picked | anything while the app is closed |

## Phones with no app

- They are never ranged and never appear as a dot. The optional cold scan (a puck host listening to Bluetooth advertising) produces three numbers per puck: how many devices were heard at near, middle and far signal strength in a ten-second window.
- Addresses are held only as salted hashes in memory for that window, with a salt that is random per run; they are never written to disk, never sent to the server and never logged. The board shows "about 3 devices", with the caveat that devices are not people.

## What the server keeps, and for how long

- Phones: the latest status and geometry while the phone is heard. A phone that goes quiet is marked stale after 20 seconds and deleted after 15 minutes (`RETENTION_S` = 900), or at once on "Stop sharing" and on Reset.
- The event timeline of the incident (phone appeared, located, lost) lives in memory until Reset. Nothing is written to disk except the puck placement table in live mode (`data/pucks_live.json`: puck ids, positions and Bluetooth names; no phone data).
- The building record in this repository is public open data with tenant names withheld; with live CoStar data the same rule applies on screen: the kind of business and the suite, not a person.

## Limits of these promises

- A random id that is stable for 10 minutes is still a pseudonym: anyone who can see the feed can follow one phone for that long. The feed is meant for the incident's own command post.
- This is a hackathon prototype: the link between the app and the server is plain HTTP on a local network, with no authentication. A real deployment needs transport security and an access model for the feed.
- Not a life-safety product.
