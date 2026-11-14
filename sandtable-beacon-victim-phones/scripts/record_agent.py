"""Record the Beacon command agent's turn with a REAL model, then check that replay reproduces it exactly.

    .venv/bin/python scripts/record_agent.py                       # record every situation (claude-cli, sonnet), then verify replay
    .venv/bin/python scripts/record_agent.py --situation sample     # one situation
    .venv/bin/python scripts/record_agent.py --check                # no model calls: verify that replay reproduces the recorded runs
    .venv/bin/python scripts/record_agent.py --brief-file b.json    # record the situation of a saved GET /api/brief (name: "brief-file")

Situations (built without the server, deterministic):
  sample     fixtures/beacon/sample_beacons.json (the hand-made sample; also the fake engine's "fix" phase)
  suite-310  the scripted scene fixtures/beacon/scenarios/suite-310.json run through the real simulator, session and
             locator on a FIXED clock (0.1 s steps from a fixed epoch, 30 s), so its brief is the same on every run
  suite-310+309  the same scene plus an occupant sheltering in Suite 309, which the record lists as vacant and which
             they registered in the app (a disagreement between the record and the phones)

Recording uses LLM_BACKEND (default here: claude-cli) with LLM_RECORD=1: every model reply is written to
fixtures/llm_cache/<sha256 of the canonical request>.json by beacon/llm.py. Nothing is hand-written. The index
fixtures/llm_cache/RUNS.json lists, per situation, the request keys of the run and the plan it produced.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beacon import config  # noqa: E402
from beacon.agent import BeaconAgent  # noqa: E402
from beacon.llm import CACHE_DIR, LLM  # noqa: E402
from beacon.tools import brief_of  # noqa: E402

INDEX = CACHE_DIR / "RUNS.json"
SITUATIONS = ("sample", "suite-310", "suite-310+309")
SCENE_EPOCH = 1794679200.0          # fixed clock for the scripted scene (Sat Nov 14 2026, 12:00 CST); never shown anywhere
SCENE_SECONDS = 30.0
SCENE_DT = 0.1


def sample_source() -> dict:
    return json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))


# The suite-310 scene plus one opt-in occupant sheltering next door in Suite 309, which the building record lists as
# vacant (no active business registration) and which they registered in the app: the record disagrees with the phones.
EXTRA_309 = {"key": "shelter-309", "room": "r309", "x": 3.6, "y": 12.4, "opt_in": True, "uwb": True, "chip": "U2", "model": "iPhone 15",
             "pointing": False, "report": {"status": "sheltering", "people": 1, "can_move": True, "battery_pct": 77, "floor_reported": 3,
                                           "home_suite": "309", "platform": "ios", "safe_outside": False}}


def scene_session(name: str = "suite-310", seconds: float = SCENE_SECONDS, extra_phones: list | None = None):
    """The scripted scene through the real simulator, session and locator on a fixed clock. Returns the session."""
    from beacon.session import BeaconSession
    from beacon.sim import World
    twin_raw, site = config.twin_raw(), config.site_raw()
    s = BeaconSession(twin_raw, site, labels=config.labels(site, twin_raw), source="sim")
    w = World(twin_raw, seed=config.SIM_SEED)
    sc = json.loads((config.SCENARIOS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    if extra_phones:
        sc["phones"] = list(sc.get("phones") or []) + [dict(p) for p in extra_phones]
    w.load_scenario(sc)
    s.set_pucks([dict(p) for p in w.pucks], room=sc.get("room"), floor=sc.get("floor"))
    n = int(round(seconds / SCENE_DT))
    now = SCENE_EPOCH
    for k in range(n):
        now = SCENE_EPOCH + k * SCENE_DT
        for o in w.step(now) or []:
            try:
                s.ingest(o)
            except ValueError:
                pass
        if k % 2 == 0:
            s.step(now)
    s.step(now)
    return s


def situation_source(name: str, brief_file: str | None = None):
    if name == "sample":
        return sample_source()
    if name == "suite-310":
        return scene_session("suite-310")
    if name == "suite-310+309":
        return scene_session("suite-310", extra_phones=[EXTRA_309])
    if name == "brief-file":
        return json.loads(Path(brief_file).read_text(encoding="utf-8"))
    raise SystemExit(f"unknown situation {name!r}")


def comparable(plan: dict) -> dict:
    """The plan without the backend label (record says claude-cli, replay says replay)."""
    return {k: v for k, v in plan.items() if k != "backend"}


async def run_once(source, llm: LLM, record_path: Path) -> tuple[dict, list[dict], BeaconAgent]:
    msgs: list[dict] = []
    agent = BeaconAgent(source, None, None, msgs.append, llm=llm, record_path=record_path, pace_s=0)
    plan = await agent.run("record")
    return plan, msgs, agent


def keys_of(llm: LLM) -> list[str]:
    return list(getattr(llm, "keys", []))


class KeyedLLM(LLM):
    """An LLM that remembers the request keys of its calls (for the index)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.keys: list[str] = []
        self.models: list[str] = []

    async def complete(self, system, messages, tier="main"):
        text, meta = await super().complete(system, messages, tier)
        self.keys.append(meta["key"])
        if meta.get("model"):
            self.models.append(meta["model"])
        return text, meta


def load_index() -> dict:
    if INDEX.is_file():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return {"note": "Index of the command agent's recorded runs (scripts/record_agent.py). Every other file in this directory is one real "
                    "model reply recorded by beacon/llm.py; nothing here is hand-written.", "runs": {}}


def save_index(index: dict) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(INDEX)


async def main_async(args) -> int:
    names = [args.situation] if args.situation else list(SITUATIONS)
    if args.brief_file:
        names = ["brief-file"]
    index = load_index()
    tmpdir = Path(tempfile.mkdtemp(prefix="beacon-record-"))
    failures = 0
    for name in names:
        t0 = time.time()
        source = situation_source(name, args.brief_file)
        brief = brief_of(source)
        print(f"== {name}: situation built in {time.time() - t0:.1f} s; phones {[p['id'] + ':' + str(p.get('geometry')) for p in brief.get('phones') or []]}")
        if not args.check:
            backend = os.environ.get("LLM_BACKEND", "claude-cli")
            llm = KeyedLLM(backend=backend, record=True)
            t1 = time.time()
            plan, msgs, agent = await run_once(source, llm, tmpdir / "record_checks.json")
            print(f"   recorded with {backend}: backend {plan['backend']}, {llm.calls['live']} live calls, {llm.calls['replay']} cache hits, "
                  f"{time.time() - t1:.0f} s, models {sorted(set(llm.models))}")
            if plan["backend"] == "reflex-fallback":
                err = next((m["text"] for m in msgs if m.get("kind") == "error"), "")
                print(f"   FAILED: the model run fell back: {err}")
                failures += 1
                continue
            index["runs"][name] = {"keys": llm.keys, "models": sorted(set(llm.models)), "recorded_with": backend,
                                   "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": len(llm.keys), "plan": comparable(plan)}
            save_index(index)
        rec = index["runs"].get(name)
        if rec is None:
            print(f"   no recorded run for {name}")
            failures += 1
            continue
        llm = KeyedLLM(backend="replay")
        plan, msgs, agent = await run_once(source, llm, tmpdir / "record_checks.json")
        same = plan["backend"] == "replay" and comparable(plan) == rec["plan"] and llm.keys == rec["keys"]
        print(f"   replay: backend {plan['backend']}, {llm.calls['replay']} hits, {llm.calls['miss']} misses -> "
              + ("IDENTICAL plan" if same else "DIFFERENT"))
        if not same:
            failures += 1
        else:
            print(f"   summary: {plan['summary']}")
            for o in plan["orders"]:
                print(f"   order [{o['priority']}] {o['to']}: {o['text']}")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--situation", choices=SITUATIONS)
    ap.add_argument("--check", action="store_true", help="no model calls: verify that replay reproduces the recorded runs")
    ap.add_argument("--brief-file", help="a saved GET /api/brief (or Beacons object) to record instead")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
