# Sandtable Beacon

Find the people still inside a burning building, and show them on a command board built from the building's own data.

![The command board during the hands-free beacon exercise](sandtable-live-fireground-twin/figures/ui_beacon_05_two_puck_moment_plan.png)

*The command board in the hands-free exercise on the stand-in building (phones, pucks and crews simulated; the agent's turns are replays of a recorded real model run). Two pucks on the door wall of Suite 310: phone B1 is located to within 0.4 m because its mirror-image spot falls outside the building and is ruled out; phone B2 is still one of two places inside the suite, so the agent orders a search. Every layer carries its source in the footer.*

## The story

1. **The alarm comes in.** The command board pulls the building: the CoStar record (storeys, construction, sprinklers, elevators, who leases which floor and how many people work there, life hazards such as a clinic with sedated patients), the Matterport 3D scan of the interior, and Vexcel aerial imagery. Before anyone goes in, the crew sees the floor plan, the doors and stairs, the sides of the building, the hydrants, and who should be inside.
2. **A firefighter throws the beacon in.** The beacon is two small UWB pucks (Qorvo DWM3001CDK boards). The responder marks on the plan which room it went into.
3. **The pucks measure their own spacing** by ranging each other, so their positions on the plan come from a measurement, not a guess.
4. **The people inside are found.** Occupants who have the Sandtable Beacon app and press SOS are ranged by both pucks. Each phone's distances reach the board, which places the person on the plan exactly as far as the measurement supports (a ring, two candidate spots, or a fix with its uncertainty), with their status (trapped, injured, able to move) and how many people are with them.
5. **Command.** An AI command agent sets the phones against the building record ("the record expects about 25 to 62 people on this floor; phones report 3; 5 suites have no puck in range"), routes a rescue crew through the twin, issues radio orders and writes the report the command post keeps.

**Only phones running the app are found.** A phone transmits no UWB until an app on it opens a ranging session, so phones without the app are not detected. The board says so, and uses the building record to show where people are expected but nobody has reported.

## Run it in five minutes (no hardware, no keys)

```bash
git clone https://github.com/iujab/sandtable-beacon.git
cd sandtable-beacon/sandtable-live-fireground-twin
bash run_demo.sh --storyboard        # open http://127.0.0.1:1810
```

Windows, the real building's data, and the real boards and phones: **[RUNBOOK.md](RUNBOOK.md)**, step by step.

## What is in this repository

| Folder | What it is |
|---|---|
| `sandtable-live-fireground-twin/` | The command and reporting board: the building from its three data layers, the AI command agent, the report; also two secondary simulated modes (tagged crews, thrown radar pucks) |
| `sandtable-beacon-victim-phones/` | The beacon: the iPhone app, the server and locator that turn puck-to-phone distances into positions, a simulator, the board-to-board ranging firmware, and the hardware guides |
| `RUNBOOK.md` | How to run everything, from the laptop demo to the real boards |
| `TASKS.md` | What is done, what is left, and the owner's hardware checklist |

## What is real today

- **Runs end to end on a simulator**, with the AI command agent replaying recorded real model turns. Every screen labels what is simulated (SIM), stand-in (STAND-IN), recorded (REPLAY) or live.
- **Ran privately on live sponsor data** (CoStar record, Matterport scan, Vexcel imagery) for one real building. None of that data is in this repository; the repository runs on labelled stand-ins.
- **On real hardware so far:** two DWM3001CDK boards ranged each other with our board-to-board firmware (about 1.57 m apart: 4.9 ranges a second, every poll of the last 50 answered, spread ±0.04 m; repeatability only, as no tape-measured reference was taken: `sandtable-beacon-victim-phones/results/twr_measured_2026-11-15.json`); with Qorvo's phone firmware, the owner's iPhone connected to each board and showed a distance in Qorvo's app (reported by the owner, 2026-11-15).
- **Not yet:** our iPhone app has not been compiled and run; no phone position from real boards has been measured, so every phone-position accuracy figure is simulated.
- Not a certified life-safety system.
