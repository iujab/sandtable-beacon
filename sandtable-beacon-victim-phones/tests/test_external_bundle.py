"""The module runs on a twin and a record supplied from outside the repository (BEACON_TWIN_JSON / BEACON_SITE_JSON /
BEACON_BUNDLE_DIR, beacon/config.py), and writes nothing derived from them into the tree.

The "external" bundle here is synthetic: made in a temporary directory from the fixture twin, with the rooms renamed and
three of them merged into one suite (as a scan of a single suite would be), plus an invented record with head counts.
Each case runs in a subprocess because the configuration is read at import time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _make_bundle(tmp: Path) -> Path:
    twin = json.loads((ROOT / "fixtures" / "building" / "twin.json").read_text(encoding="utf-8"))
    ren = {r["id"]: "x" + r["id"] for r in twin["rooms"]}
    merged = {"xr309", "xr310", "xr311"}
    for r in twin["rooms"]:
        r["id"] = ren[r["id"]]
        if r["id"] in merged:
            r["suite"], r["label"] = "9000", {"xr309": "Office A", "xr310": "Open office", "xr311": "Meeting room"}[r["id"]]
    for d in twin["doors"]:
        d["rooms"] = [ren.get(x, x) for x in d.get("rooms") or []]
    for key in ("sweeps", "tags", "stairs"):
        for o in twin.get(key, []):
            if o.get("room") in ren:
                o["room"] = ren[o["room"]]
    for n in (twin.get("graph") or {}).get("nodes", []):
        if n.get("room") in ren:
            n["room"] = ren[n["room"]]
    twin["source"] = {"kind": "test-external", "synthetic": True, "note": "synthetic external bundle for a test"}
    site = {"schema": "sandtable.site/1", "property": {"name": "Test building", "address": "1 Test St"},
            "tenants": [{"tenant_id": "t1", "name": "EXTERNAL TENANT NAME", "suite": "9000", "floor": 12, "sf": 2800, "employees": 14,
                         "industry": "Offices of lawyers", "naics": "541110", "life_hazard": None, "hazmat": None,
                         "source": "test", "synthetic": True}],
            "available_spaces": [], "open_data": []}
    bundle = tmp / "bundle"
    bundle.mkdir()
    (bundle / "twin.json").write_text(json.dumps(twin), encoding="utf-8")
    (bundle / "site.json").write_text(json.dumps(site), encoding="utf-8")
    return bundle


SCRIPT = r'''
import json, os, sys, time
from pathlib import Path
from fastapi.testclient import TestClient
from beacon import config, server, llm
out = {"external": config.EXTERNAL_BUNDLE, "data_dir": str(config.DATA_DIR), "llm_cache": str(llm.CACHE_DIR),
       "scenario": config.DEFAULT_SCENARIO, "labels": config.labels()}
if os.environ.get("T_FAST") == "1":
    print("RESULT " + json.dumps(out))
    sys.exit(0)
app = server.create_app(source="sim", story=True)
with TestClient(app) as c:
    out["bundle_room"] = c.get("/api/bundle").json()["config"]["room"]
    geoms, t0, b = {}, time.time(), None
    while time.time() - t0 < 40:
        b = c.get("/api/beacons").json()
        for p in b["phones"]:
            geoms.setdefault(p["id"], [])
            if not geoms[p["id"]] or geoms[p["id"]][-1] != p["geometry"]:
                geoms[p["id"]].append(p["geometry"])
            assert (p["x"] is None and p["y"] is None) or p["geometry"] == "fix"
        if len(b["phones"]) >= 3 and any(g and g[-1] == "fix" for g in geoms.values()):
            break
        time.sleep(0.3)
    acc = b.get("accountability") or {}
    out["geoms"] = geoms
    out["room"] = b.get("room")
    out["rows"] = [{k: r.get(k) for k in ("suite", "rooms", "tenant", "expected", "phones_opt_in", "safe_outside", "state")} for r in acc.get("suites", [])]
    out["costar_label"] = (acc.get("source") or {}).get("text")
    out["layout"] = c.get("/api/layout").json()
    out["directory"] = c.get("/api/directory").json()
print("RESULT " + json.dumps(out))
'''


def _run(tmp_path: Path, env_extra: dict) -> dict:
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("BEACON_") or k in ("COSTAR_MODE", "MATTERPORT_MODE"):
            env.pop(k)
    env.update(env_extra)
    env["PYTHONPATH"] = str(ROOT)
    env["LLM_BACKEND"] = "replay"
    p = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=110)
    assert p.returncode == 0, p.stderr[-3000:]
    line = next(x for x in p.stdout.splitlines() if x.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


def _tree_state() -> dict:
    watch = [ROOT / "fixtures", ROOT / "data"]
    state = {}
    for base in watch:
        if base.is_dir():
            for f in base.rglob("*"):
                if f.is_file() and "clean_clone_test" not in f.parts and f.name != "demo.log":
                    state[str(f)] = (f.stat().st_size, f.stat().st_mtime_ns)
    return state


def test_runs_on_an_external_bundle_and_writes_nothing_into_the_tree(tmp_path):
    bundle = _make_bundle(tmp_path)
    before = _tree_state()
    out = _run(tmp_path, {"BEACON_BUNDLE_DIR": str(bundle), "BEACON_DATA_DIR": str(tmp_path / "priv"),
                          "BEACON_COSTAR_MODE": "live", "BEACON_MATTERPORT_MODE": "live"})
    after = _tree_state()
    assert before == after, sorted(set(after.items()) ^ set(before.items()))[:5]       # nothing created or touched in fixtures/ or data/
    assert out["external"] is True and out["scenario"] == "auto"
    assert Path(out["data_dir"]).name == "live" and str(tmp_path) in out["data_dir"]
    assert out["llm_cache"].startswith(out["data_dir"])                                # recorded prompts never go to fixtures/
    assert out["labels"]["costar"] == {"mode": "live", "real_sponsor_data": True, "text": "Building record: CoStar (live)."}
    assert out["labels"]["matterport"]["mode"] == "live"
    # the scene was built for the loaded twin, in one of its rooms, and the walk-through reached a position
    assert out["room"] and out["room"].startswith("x") and out["bundle_room"].startswith("x")
    assert any(g[-1] == "fix" for g in out["geoms"].values()), out["geoms"]
    # three rooms that share a suite number are ONE row, joined to the record although the storeys differ (12 vs the twin's floor)
    row = next(r for r in out["rows"] if r["suite"] == "9000")
    assert sorted(row["rooms"]) == ["xr309", "xr310", "xr311"] and row["tenant"] == "EXTERNAL TENANT NAME"
    assert row["expected"]["high"] == 14 and "record head count" in row["expected"]["basis"]
    assert len([r for r in out["rows"] if r["suite"] == "9000"]) == 1
    assert out["costar_label"] == "Building record: CoStar (live)."
    assert out["layout"]["pucks"] and any(d["suite"] == "9000" for d in out["directory"])


def test_twin_and_site_paths_and_unstated_provenance(tmp_path):
    bundle = _make_bundle(tmp_path)
    out = _run(tmp_path, {"BEACON_TWIN_JSON": str(bundle / "twin.json"), "BEACON_SITE_JSON": str(bundle / "site.json"),
                          "BEACON_DATA_DIR": str(tmp_path / "live"), "T_FAST": "1"})
    assert out["external"] is True
    assert out["data_dir"] == str(tmp_path / "live")                 # a directory already named live is used as it is
    assert out["labels"]["costar"]["mode"] == "external" and "not stated" in out["labels"]["costar"]["text"]
    assert out["labels"]["costar"]["real_sponsor_data"] is False


def test_fixture_bundle_is_the_default():
    env = {k: v for k, v in os.environ.items() if not k.startswith("BEACON_")}
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run([sys.executable, "-c",
                        "from beacon import config; print(config.EXTERNAL_BUNDLE, config.TWIN_JSON.name, config.DATA_DIR.name, "
                        "config.LLM_CACHE_DIR.parent.name, config.DEFAULT_SCENARIO, config.labels()['costar']['mode'])"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]
    assert p.stdout.split() == ["False", "twin.json", "data", "fixtures", "suite-310", "fixture"]
