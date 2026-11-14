# Deviations from INTERFACE.md v1 on real boards

The wire format is kept exactly. These are the places where real hardware cannot (yet, or ever) deliver what the
frozen text describes. Nothing here has been observed on hardware: the firmware is compile-verified only, so this
list is what the design and the vendors' documents imply. Update it after the first real bring-up.

| INTERFACE.md says | On real boards | Why |
|---|---|---|
| Boards connect over "USB serial \| Wi-Fi UDP \| BLE" | USB serial and Wi-Fi UDP only; **no BLE transport** | not built: serial + UDP cover the laptop and the UNO Q, and BLE would add a second radio stack on the ESP32 next to Wi-Fi |
| `status.battery` e.g. `{"T1":3.91}` | present only for boards that can measure it: MaUWB (divider on GPIO 4). Makerfabs ESP32 UWB DW3000 / DW1000 boards report none unless built with `-DUK_VBAT_PIN=<gpio>` | no battery divider pin is documented for those boards |
| `event` `button` "if the board has a button" | the ESP32 BOOT button (GPIO 0) is used as button 1 | the boards have no dedicated user button |
| `range.fp`, `range.los` | absent on MaUWB boards (and on any board running vendor firmware through an adapter); `rssi` only on MaUWB firmware older than v1.1.2 | the module's AT firmware does not report first-path power; newer versions removed RSSI too. The solver then weights those ranges equally |
| `range.raw` | equals `d` on kit firmware unless a hub-side `POST /bias` correction is active | the antenna-delay correction happens inside the chip's timestamps, so there is no separate uncorrected value to report |
| `profile` `mesh5` | DW3000 / DW1000 kit firmware only. **Not on MaUWB** (tag-to-anchor ranges only) and not with vendor firmware adapters | the MaUWB module's closed firmware fixes the topology |
| `POST /calibrate` (anchor self-calibration) | DW3000 / DW1000 kit firmware only (`SURVEY` is broadcast over UWB, one board on USB is enough). Not on MaUWB or vendor firmware: use `POST /anchors` (tape measure) | same reason |
| ~10 Hz | one tag with four anchors is designed for 10 Hz; **two tags share the air time** (expect roughly half each, with occasional gaps); MaUWB resolution is 1 cm and its own TDMA sets the rate | physics and the module firmware |
| `pos` for every tag | only after the anchors' coordinates are set (`anchors.calibrated` is `"none"` and `anchors` is `{}` until then) | the hub cannot know where the anchors are |
| `hello.nodes` is fixed for a connection | nodes that appear later (a board powered on late) cause a new `hello` on the live connection | documented under "v1 additions" |
| Commands to any node | a firmware command (`ANTDLY`, `CAL`, `ROLE` ...) reaches a board only over its own USB port or over Wi-Fi UDP; only `SURVEY` is relayed over UWB | there is no general command relay in the over-the-air protocol |

Simulator-only features (`/sim/*`, `sim-truth`) answer HTTP 409 on a real-board hub, as the interface intends.
