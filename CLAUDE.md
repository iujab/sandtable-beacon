# Notes for a coding agent working in this repository

Read `RUNBOOK.md` (how to run every level) and `TASKS.md` (what is finished, what is left, what the owner is doing by hand) first.

- Two parts: `sandtable-beacon-victim-phones/` (the beacon: iPhone app in `apps/ios`, Beacon server and locator, simulator, board-to-board ranging firmware in `hardware/twr_firmware`, hardware guides; server on port 1885) and `sandtable-live-fireground-twin/` (the command board: building layers from CoStar, Matterport and Vexcel, the AI command agent, the report; port 1810). The command board consumes the beacon's feed (`sandtable-beacon-victim-phones/docs/BEACON_FEED.md`) and vendors a copy of the beacon module in `vendor/beacon` (regenerate with `scripts/vendor_beacon.py`; never hand-edit). Each part has its own `docs/CONTRACTS.md`, `run_demo.sh`, `run_tests.sh` and `project.json`.
- Hardware: Qorvo DWM3001CDK boards. A board runs ONE firmware: Qorvo's stock Nearby Interaction firmware (ranges iPhones through the app; cannot range another board) or our SS-TWR firmware (ranges another board; cannot talk to phones). iPhone 14 and 15 give distance only. Arduino UNO Q optional (Debian; `python3 -m venv` needs `--without-pip` plus get-pip there).
- Detection is opt-in: only phones running the app with SOS on are found. Never add code, copy or claims implying the system detects phones that have not opted in; a phone transmits no UWB until an app on it opens a session.
- Honesty rules that must hold in code, screens and write-ups: label simulated data as simulated and stand-in data as stand-in; call something compiled, tested or run on hardware only if it was; every number in a write-up comes from a file in `results/`; never present invented records as sponsor data.
- Recorded agent runs replay by prompt hash: changing prompt text, or the facts fed into a prompt, turns recorded turns into fallbacks. Check with `scripts/check_replay.py` (command board).
- Keys live in a local `.env` (git-ignored). Never commit keys, the sponsor toolkit, the private live bundle, or anything fetched from sponsor APIs; live data stays under `data/live/`.
- Plain commit messages, no co-author or "generated with" trailers.
- Keep launchers cross-platform (`python -m ...`); the owner's laptop runs Windows, the UNO Q runs Debian, the iPhone app builds on a Mac.
