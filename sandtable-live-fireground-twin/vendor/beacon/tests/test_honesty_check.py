"""The honesty watchdog (scripts/honesty_check.py, SIMULATED) runs, writes valid JSON and checks the invariants."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

if importlib.util.find_spec("beacon.session") is None or importlib.util.find_spec("beacon.locate") is None:
    pytest.skip("beacon.session or beacon.locate is missing", allow_module_level=True)

import honesty_check as hc  # noqa: E402


def test_short_watchdog_writes_a_report(tmp_path):
    out = tmp_path / "honesty_check.json"
    code = hc.main(["--short", "--workers", "2", "--out", str(out)])
    d = json.loads(out.read_text())
    assert d["simulated"] is True and set(d["scenarios"]) == {n for n, _, _ in hc.SCENARIOS}
    assert code == (0 if d["pass"] else 1)
    for s in d["scenarios"].values():
        assert s["steps"] > 0 and 0.0 <= s["wrong_share_of_fix_steps"] <= 1.0
        assert len(s["per_seed"]) == 2
    assert {d["scenarios"]["two-phones-board-sharing-turns"]["board_sharing"], d["scenarios"]["bye"]["board_sharing"]} == {"turns", "pairs"}
    assert "/home/" not in out.read_text()


def test_the_checker_catches_a_coordinate_leak_and_a_dot_on_a_stale_phone():
    r = hc.Run("unit")
    try:
        r.check({"phones": [{"id": "B1", "eid": "00000001", "geometry": "ring", "x": 1.0, "y": 2.0, "stale": False, "report": {}},
                            {"id": "B2", "eid": "00000002", "geometry": "fix", "x": 1.0, "y": 2.0, "stale": True, "report": {}}]})
        assert set(r.violations) == {"coordinates without a fix", "stale phone with a dot"}
        assert not r.result()["pass"]
    finally:
        r.close()
    assert hc.session_mod.time is not r.clock                 # the session's clock is restored
