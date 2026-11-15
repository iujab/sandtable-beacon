# Vendored: the Beacon module (lane L16)

A copy of `../sandtable-beacon-victim-phones/` (sibling lane **L16**, "Sandtable Beacon"), made by `scripts/vendor_beacon.py` on 2026-11-15T03:17:42-0600.

Source: commit `0fa2d55793296d9bb77d82a8ad901b13bce19002` (2026-11-15T03:16:32-06:00): "Honesty check re-run after the name-retention fix: all scenes pass". Copied with `git archive`, so only committed files are here.

It is that team's work, credited to them; it is copied (not linked) so this repository stands alone. Do not edit it here: change the sibling project and re-run `python scripts/vendor_beacon.py`. This file is generated from the copy.

## What was copied

| Directory | Files | Bytes |
|---|---|---|
| top level | 4 | 28,803 |
| `apps/` | 18 | 160,498 |
| `beacon/` | 30 | 652,927 |
| `docs/` | 11 | 287,723 |
| `figures/` | 24 | 5,875,388 |
| `fixtures/` | 26 | 582,526 |
| `hardware/` | 18 | 277,307 |
| `results/` | 5 | 401,468 |
| `scripts/` | 6 | 163,382 |
| `tests/` | 19 | 251,628 |
| `web/` | 16 | 320,290 |
| **total** | **177** | **9,001,940** |

Skipped: nothing among the committed files of the copied directories. Never copied: `data/`, virtualenvs, caches, build outputs, `.env*`, keys and signing material, any `live` directory.

## How it runs here

`python -m sandtable.demo` starts it by itself on port 1885 when nothing answers there (and stops it on exit), then points the board at it (`BEACON_URL`). By hand: `cd vendor/beacon && python -m beacon.demo --port 1885` (simulated world). Its feed contract is `docs/BEACON_FEED.md`; its own tests run with `cd vendor/beacon && python -m pytest -q tests`. If it is absent or fails to start, the Sandtable board relays the hand-made sample (`fixtures/beacon/sample_beacons.json`), labelled SAMPLE on screen.

## Every file

| File | Bytes |
|---|---|
| `README.md` | 23,817 |
| `apps/ios/SandtableBeacon.xcodeproj/project.pbxproj` | 11,924 |
| `apps/ios/SandtableBeacon.xcodeproj/project.xcworkspace/contents.xcworkspacedata` | 135 |
| `apps/ios/SandtableBeacon.xcodeproj/xcshareddata/xcschemes/SandtableBeacon.xcscheme` | 2,912 |
| `apps/ios/SandtableBeacon/ContentView.swift` | 18,237 |
| `apps/ios/SandtableBeacon/Info.plist` | 2,831 |
| `apps/ios/SandtableBeacon/Models.swift` | 19,445 |
| `apps/ios/SandtableBeacon/PuckLink.swift` | 25,748 |
| `apps/ios/SandtableBeacon/PuckManager.swift` | 25,716 |
| `apps/ios/SandtableBeacon/Reporter.swift` | 13,031 |
| `apps/ios/SandtableBeacon/SandtableBeaconApp.swift` | 1,965 |
| `apps/ios/SandtableBeacon/Sensors.swift` | 8,522 |
| `apps/ios/SandtableBeacon/SetupView.swift` | 10,864 |
| `apps/ios/SandtableBeacon/Theme.swift` | 2,763 |
| `apps/ios/project.yml` | 1,473 |
| `apps/ios/tools/pbxproj_check.log` | 997 |
| `apps/ios/tools/pbxproj_check.py` | 10,314 |
| `apps/ios/tools/syntax_check.log` | 594 |
| `apps/ios/tools/syntax_check.py` | 3,027 |
| `beacon/__init__.py` | 0 |
| `beacon/agent.py` | 29,127 |
| `beacon/agent_routes.py` | 4,222 |
| `beacon/autoscene.py` | 7,080 |
| `beacon/baseline.py` | 6,560 |
| `beacon/config.py` | 8,923 |
| `beacon/demo.py` | 5,500 |
| `beacon/feeds.py` | 16,980 |
| `beacon/host/__init__.py` | 1,091 |
| `beacon/host/__main__.py` | 48 |
| `beacon/host/cli.py` | 10,526 |
| `beacon/host/cold.py` | 8,841 |
| `beacon/host/core.py` | 17,606 |
| `beacon/host/parse.py` | 10,824 |
| `beacon/host/sources.py` | 11,174 |
| `beacon/host/twr.py` | 8,205 |
| `beacon/layout.py` | 9,474 |
| `beacon/llm.py` | 9,048 |
| `beacon/locate.py` | 62,890 |
| `beacon/models.py` | 12,377 |
| `beacon/prune.py` | 3,805 |
| `beacon/report.py` | 52,484 |
| `beacon/roster.py` | 21,550 |
| `beacon/server.py` | 54,444 |
| `beacon/session.py` | 96,849 |
| `beacon/sim.py` | 66,402 |
| `beacon/story.py` | 20,720 |
| `beacon/tools.py` | 42,869 |
| `beacon/track.py` | 24,514 |
| `beacon/twin.py` | 28,794 |
| `docs/BEACON_FEED.md` | 38,163 |
| `docs/CONTRACTS.md` | 29,479 |
| `docs/COSTAR_TIE_IN.md` | 10,003 |
| `docs/IOS_BUILD.md` | 32,558 |
| `docs/IOS_HANDOFF.md` | 3,991 |
| `docs/PHONE_FEASIBILITY.md` | 17,193 |
| `docs/PRIVACY.md` | 3,018 |
| `docs/ROOM_LOCATION.md` | 4,422 |
| `docs/research/android_ble_models.md` | 52,784 |
| `docs/research/ios_ni.md` | 64,314 |
| `docs/research/puck_hardware.md` | 31,798 |
| `figures/eval_area_vs_pucks.png` | 117,533 |
| `figures/eval_bearing.png` | 123,809 |
| `figures/eval_error_vs_pucks.png` | 171,410 |
| `figures/eval_geometry_share.png` | 81,259 |
| `figures/eval_layouts.png` | 269,238 |
| `figures/eval_layouts_diagram.png` | 119,813 |
| `figures/eval_storyboard.png` | 160,299 |
| `figures/eval_twin_pruning.png` | 141,283 |
| `figures/eval_two_puck.png` | 169,902 |
| `figures/eval_two_puck_map.png` | 281,466 |
| `figures/ui_00_no_pucks.png` | 283,921 |
| `figures/ui_01_ring.png` | 316,114 |
| `figures/ui_02_cannot_choose.png` | 298,383 |
| `figures/ui_03_fix_two_pucks.png` | 291,832 |
| `figures/ui_04_accountability.png` | 318,575 |
| `figures/ui_05_phone.png` | 143,662 |
| `figures/ui_05b_phone_full.png` | 261,941 |
| `figures/ui_06_agent.png` | 346,638 |
| `figures/ui_07_floor.png` | 429,966 |
| `figures/ui_08_1280x720.png` | 265,250 |
| `figures/ui_09_layout.png` | 375,591 |
| `figures/ui_10_baseline_mismatch.png` | 317,695 |
| `figures/ui_11_baseline_anchored.png` | 317,132 |
| `figures/ui_report.png` | 272,676 |
| `fixtures/beacon/ingest_example.json` | 739 |
| `fixtures/beacon/ios_batch_examples.json` | 6,768 |
| `fixtures/beacon/sample_beacons.json` | 32,365 |
| `fixtures/beacon/scenarios/crowded.json` | 3,280 |
| `fixtures/beacon/scenarios/empty-room.json` | 604 |
| `fixtures/beacon/scenarios/suite-310.json` | 3,710 |
| `fixtures/building/site.json` | 21,725 |
| `fixtures/building/twin.json` | 41,869 |
| `fixtures/llm_cache/11dea0ad7bc46eeb40e34be1fe77523c02aa30d7d972ae75e174c32830cb48c3.json` | 25,909 |
| `fixtures/llm_cache/18c6b4d61cd32ce91418bb9ac4c1b5af35b2c798937e732d908bebe41398e0d0.json` | 32,173 |
| `fixtures/llm_cache/372b833bfb0a68c5cb6f5dc9a065c0a9828e612e29e49c22f548995abbbcdd82.json` | 25,881 |
| `fixtures/llm_cache/43826e88f7ea1cab3514e8b3fb88c4bdca9391ddf11d565e9aedd5a957eac076.json` | 34,932 |
| `fixtures/llm_cache/529bd0657a9ebb7006198482fc0f47d1892e6da409ea62d26952d3d9c74c810e.json` | 30,240 |
| `fixtures/llm_cache/58791946f7764f339c81c0a75d96fbfcb47716cfd378c428d8ad83fcca100fb2.json` | 31,056 |
| `fixtures/llm_cache/62a4dd319555045a162497990591b6f0c6d006b89ae98af763ec8769f93f0e42.json` | 35,395 |
| `fixtures/llm_cache/670234f0cdb9f00947c93a1d5bcfffef6a30616449d1ad2faa288b828b295427.json` | 25,100 |
| `fixtures/llm_cache/76077bdcee59c7a1af9cc8ac38c11252e06dab2e3786ddc95edd51b2d72e0c59.json` | 8,871 |
| `fixtures/llm_cache/91f9636e327333af83351f3abcbe5e2e447eb370cf56856ccd66c9808e93804b.json` | 26,664 |
| `fixtures/llm_cache/94a096e537fce4f194bc0b8d4390e625df0da948229f2ca2cf0da96820628eab.json` | 8,887 |
| `fixtures/llm_cache/9684f711cb6e8d8839809c32f2067a875980ee33cd6ba63b419fe7cdf21b3597.json` | 33,288 |
| `fixtures/llm_cache/RUNS.json` | 45,186 |
| `fixtures/llm_cache/a836a4d4a02a7b55dcd5e7a99b9eb6aa0b097ee1f02d5c287b2f17a1ea7a5497.json` | 28,598 |
| `fixtures/llm_cache/abc48f8a2100cc1fef6ccddf38b4eb42dde137ea5bbfb42acd03a332003221ac.json` | 8,705 |
| `fixtures/llm_cache/f4ff09d1b0f6a7fce38767a4a89e3288e51f92e5271f8524d0991634973d2a43.json` | 8,761 |
| `fixtures/llm_cache/fb1ccfd360ac53c3eebcd5824da69045e60222dedba60feb7b57878da0a974ca.json` | 29,277 |
| `fixtures/llm_cache/ff4c47da6132cbc2c6e82968ab2695a8c915231e08d6ebb61287f44122f720d4.json` | 32,543 |
| `hardware/BOARD_TO_BOARD.md` | 8,901 |
| `hardware/BRINGUP.md` | 40,241 |
| `hardware/FLASH.md` | 10,319 |
| `hardware/LAYOUT.md` | 5,994 |
| `hardware/PUCK.md` | 16,947 |
| `hardware/UNO_Q.md` | 15,046 |
| `hardware/jlink/.gitignore` | 119 |
| `hardware/jlink/flash_qani.jlink` | 1,425 |
| `hardware/layout_table.py` | 14,336 |
| `hardware/puck_sketch.svg` | 5,593 |
| `hardware/two_puck_layout.svg` | 8,027 |
| `hardware/twr_firmware/Makefile.twr` | 4,239 |
| `hardware/twr_firmware/README.md` | 13,862 |
| `hardware/twr_firmware/app.c.ed` | 10,312 |
| `hardware/twr_firmware/build_logs/build_INITIATOR.log` | 57,697 |
| `hardware/twr_firmware/build_logs/build_RESPONDER.log` | 57,697 |
| `hardware/twr_firmware/dwm3001c.ld` | 1,177 |
| `hardware/twr_firmware/prepare.py` | 5,375 |
| `requirements.txt` | 309 |
| `results/eval_beacon.json` | 119,984 |
| `results/eval_layouts.json` | 240,559 |
| `results/eval_two_puck.json` | 22,485 |
| `results/honesty_check.json` | 15,059 |
| `results/twr_measured_2026-11-15.json` | 3,381 |
| `run_demo.sh` | 2,945 |
| `run_tests.sh` | 1,732 |
| `scripts/eval_beacon.py` | 104,553 |
| `scripts/fill_numbers.py` | 11,948 |
| `scripts/honesty_check.py` | 21,094 |
| `scripts/record_agent.py` | 13,481 |
| `scripts/shots.cjs` | 11,318 |
| `scripts/stop_demo.sh` | 988 |
| `tests/conftest.py` | 1,458 |
| `tests/test_agent.py` | 15,702 |
| `tests/test_baseline.py` | 18,528 |
| `tests/test_eval_smoke.py` | 2,647 |
| `tests/test_external_bundle.py` | 8,031 |
| `tests/test_feeds.py` | 9,034 |
| `tests/test_honesty_check.py` | 1,935 |
| `tests/test_host.py` | 21,861 |
| `tests/test_ios_wire.py` | 13,503 |
| `tests/test_locate.py` | 27,521 |
| `tests/test_models.py` | 5,261 |
| `tests/test_quality_locator.py` | 8,342 |
| `tests/test_quality_misc.py` | 6,690 |
| `tests/test_report.py` | 7,376 |
| `tests/test_roster.py` | 13,179 |
| `tests/test_server.py` | 22,941 |
| `tests/test_session.py` | 25,126 |
| `tests/test_sim.py` | 23,056 |
| `tests/test_track.py` | 19,437 |
| `web/app.html` | 6,663 |
| `web/app.js` | 47,340 |
| `web/common.js` | 6,000 |
| `web/index.html` | 9,323 |
| `web/phone.css` | 9,620 |
| `web/phone.html` | 5,363 |
| `web/phone.js` | 18,461 |
| `web/plan.js` | 52,466 |
| `web/style.css` | 36,837 |
| `web/vendor/README.md` | 437 |
| `web/vendor/fonts/barlow-400.woff2` | 21,144 |
| `web/vendor/fonts/barlow-500.woff2` | 20,960 |
| `web/vendor/fonts/barlow-600.woff2` | 21,796 |
| `web/vendor/fonts/barlow-condensed-600.woff2` | 21,352 |
| `web/vendor/fonts/barlow-condensed-700.woff2` | 21,440 |
| `web/vendor/fonts/jetbrains-mono-400.woff2` | 21,088 |
