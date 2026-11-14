"""Smoke test of the SIMULATED evaluation: `scripts/eval_beacon.py --quick` runs fast and writes valid JSON."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

if importlib.util.find_spec("beacon.locate") is None:
    pytest.skip("beacon.locate is missing (W1)", allow_module_level=True)


def test_quick_eval_runs_and_writes_valid_json(tmp_path):
    res, fig = tmp_path / "results", tmp_path / "figures"
    t0 = time.time()
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_beacon.py"), "--quick", "--workers", "2",
                        "--results-dir", str(res), "--figures-dir", str(fig)],
                       cwd=ROOT, capture_output=True, text=True, timeout=60)
    took = time.time() - t0
    assert p.returncode == 0, p.stderr[-3000:]
    assert took < 20.0, f"--quick took {took:.1f} s"
    b = json.loads((res / "eval_beacon.json").read_text())
    lay = json.loads((res / "eval_layouts.json").read_text())
    for d in (b, lay):
        assert d["simulated"] is True and d["quick"] is True
        assert d["noise_model"]["noise"]["uwb"]["sigma_los_m"] > 0
        assert d["trial_counts"] and d["base_seed"]
    methods = {a["method"] for a in b["arms"]}
    assert methods == {"ble-rssi", "uwb-twr", "uwb-twr+bearing"}
    assert {a["pucks"] for a in b["arms"]} == {1, 2, 3, 4, 5}
    assert {a["twin"] for a in b["arms"] if a["exp"] == "main"} == {True, False}
    assert any(a["exp"] == "mismatch" for a in b["arms"])
    for a in b["arms"]:
        assert abs(sum(a["geometry_share"].values()) - 1.0) < 1e-6
    assert "band_covers_true_advertising" in b["cold"]
    assert lay["recommendation"]
    assert {r["layout"] for r in lay["rows"]} == {"one-device", "two-pucks", "three-pucks", "one-puck-bearing"}
    for name in ("eval_error_vs_pucks", "eval_area_vs_pucks", "eval_bearing", "eval_twin_pruning", "eval_geometry_share",
                 "eval_storyboard", "eval_layouts", "eval_layouts_diagram"):
        assert (fig / f"{name}.png").stat().st_size > 10_000, name
