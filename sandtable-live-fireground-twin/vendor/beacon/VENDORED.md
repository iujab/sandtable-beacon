# Vendored: the Beacon module (lane L16)

This directory is a copy of `../sandtable-beacon-victim-phones/` (sibling lane **L16**, "Sandtable Beacon"),
made by `scripts/vendor_beacon.py` on 2026-11-14T12:28:56-0600 from commit `ccf1ea5811f3`. It is that team's work, credited
to them; it is copied (not linked) so this repository stands alone. Do not edit it here: change the sibling project and
re-run `python scripts/vendor_beacon.py`.

Copied: `beacon/` (the server, locator, tracker, simulator), `fixtures/`, `web/`, `apps/`, `hardware/`, `docs/`, the README and
requirements. Skipped: `data/`, virtualenvs, caches and everything the sibling's `.gitignore` ignores.

## How it runs here

`python -m sandtable.demo` starts it by itself on port 1885 when nothing answers there (and stops it on exit), then points
the board at it (`BEACON_URL`). By hand:

```bash
cd vendor/beacon && python -m beacon.demo --port 1885      # simulated world, http://127.0.0.1:1885
```

Its feed contract is `docs/BEACON_FEED.md`. If it is absent or fails to start, the Sandtable board relays the
hand-made sample (`fixtures/beacon/sample_beacons.json`), labelled SAMPLE on screen.
