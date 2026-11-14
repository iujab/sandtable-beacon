# Sandtable Beacon

Find the people still inside a burning building by their phones, and show them on a command board drawn from the building's own data.

A fire crew throws two small UWB pucks into a room. The phones of the people trapped there answer, each puck measures its distance to each phone, and the result appears on a command and reporting board: the room from the building's 3D twin, who is expected to be in it from the building record, and the outside from aerial imagery. An AI command agent sets the tenants on record against the phones still inside, routes the rescue and writes the report.

Built at HackPrinceton Fall 2026 (November 13 to 15) on CoStar building data, Matterport 3D twins and Vexcel aerial imagery, with Qorvo DWM3001CDK UWB boards and iPhones.

![A phone located in a room by two pucks](sandtable-beacon-victim-phones/figures/ui_03_fix.png)

## What is in this repository

| Folder | What it is |
|---|---|
| `sandtable-beacon-victim-phones/` | The phones and pucks: the iPhone app, the locator that turns distances into positions, a simulator, the feed, and the hardware guides |
| `sandtable-live-fireground-twin/` | The command and reporting board: the building map from the three data layers, the command agent, and two secondary simulated modes (tagged crews, thrown radar pucks) |
| `TASKS.md` | What is done, what is left, and the step-by-step list for the hardware |

## Run it

```bash
cd sandtable-live-fireground-twin
bash run_demo.sh --storyboard      # http://127.0.0.1:1810
```

See `TASKS.md` for Windows, for running the phones part alone, and for the real-hardware steps.

## What is real and what is not, today

- The phone ranging, the locator and the command board run end to end on a **simulator**. Every screen that shows simulated data says so.
- The building record, the twin and the aerial view are **labelled stand-ins** built from open data until the sponsor toolkit is plugged in.
- The iPhone app is **source code that has not been compiled yet**. The UWB boards run Qorvo's stock firmware, which this project does not modify.
- Nothing here is a certified life-safety system.

Each folder's README carries its own limitations, honestly stated.
