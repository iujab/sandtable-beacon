# Notes for a coding agent working in this repository

Read `TASKS.md` first: it says what is finished, what is left and what the owner is doing by hand.

- Two parts: `sandtable-beacon-victim-phones/` (iPhone app, locator, simulator, feed, hardware guides; server on port 1885) and `sandtable-live-fireground-twin/` (command board, command agent, building data adapters; server on port 1810). The command board consumes the phones part's feed, described in `sandtable-beacon-victim-phones/docs/BEACON_FEED.md`. Each part has its own `docs/CONTRACTS.md`, `run_demo.sh`, `run_tests.sh` and `project.json`.
- Hardware: Qorvo DWM3001CDK boards on Qorvo's stock Nearby Interaction firmware (not modified here), an iPhone 14 and an iPhone 15 running the app in `apps/ios`, an Arduino UNO Q. The live demo uses two pucks.
- Honesty rules that must hold in code, screens and write-ups: label simulated data as simulated and stand-in data as stand-in; call something compiled or tested only if it was; every number in a write-up comes from a file in `results/`; never present invented records as sponsor data.
- Keys live in a local `.env` (git-ignored). Never commit keys, the sponsor toolkit, or responses recorded from sponsor APIs without the owner's say-so.
- Plain commit messages, no co-author or "generated with" trailers.
- Keep launchers cross-platform (`python -m ...`); the owner's laptop runs Windows and the UNO Q runs Debian.
