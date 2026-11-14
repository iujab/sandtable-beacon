"""Record the Beacon command agent's turn with a REAL model, then check that replay reproduces it exactly.

    .venv/bin/python scripts/record_agent.py                       # record every situation (claude-cli, sonnet), then verify replay
    .venv/bin/python scripts/record_agent.py --situation sample     # one situation
    .venv/bin/python scripts/record_agent.py --check                # no model calls: verify that replay reproduces the recorded runs
    .venv/bin/python scripts/record_agent.py --brief-file b.json    # record the situation of a saved GET /api/brief (name: "brief-file")

Situations (built without the server, deterministic):
  sample     fixtures/beacon/sample_beacons.json (the hand-made sample; also the fake engine's "fix" phase)
  suite-310  the scripted scene fixtures/beacon/scenarios/suite-310.json run through the real simulator, session and
             locator on a FIXED clock (0.1 s steps from a fixed epoch, 30 s), so its brief is the same on every run
  walk-through  the scripted walk-through itself (beacon/story.py, what `python -m beacon.demo --story` plays), run in
             process on W3's server state with the simulator, real time; the brief is captured at the exact moment the
             story calls the command agent (step 8). The captured brief is kept in RUNS.json, so --check needs no story
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
SITUATIONS = ("sample", "walk-through", "suite-310", "suite-310+309")
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


async def walkthrough_brief(script: str = "suite-310", timeout_s: float = 150.0) -> dict:
    """Plays the real walk-through on the server's state (sim engine, wall clock) and returns brief_of(session) at the
    moment the story asks the command agent for a plan. The agent itself is replaced by a stub that only captures."""
    from beacon import server
    state = server.AppState(source="sim", story=False)
    captured: dict = {}

    class Capture:
        busy, backend, last, log = False, "capture", None, []

        async def run(self, reason: str = "manual") -> dict:
            captured["brief"] = json.loads(json.dumps(brief_of(state.session)))
            return {}

        def status(self) -> dict:
            return {"busy": False, "backend": "capture", "plan": None, "log": []}
    state.agent = Capture()
    await state.startup()
    try:
        state.start_story(script)
        t0 = time.time()
        while "brief" not in captured and time.time() - t0 < timeout_s:
            await asyncio.sleep(0.25)
    finally:
        await state.shutdown()
    if "brief" not in captured:
        raise SystemExit("the walk-through did not reach the command-agent step")
    return captured["brief"]


def tool_sweep_key(brief: dict) -> str:
    """Hash of everything the read tools say about this brief (what later prompts are built from)."""
    import hashlib
    from beacon.tools import ToolBox
    from beacon.twin import Twin
    box = ToolBox(brief, Twin(config.twin_raw()), config.site_raw(), record_path=Path(tempfile.mkdtemp()) / "rc.json")
    out = [box.call(n, {}) for n in ("get_phones", "suite_accountability", "next_puck_plan")]
    out += [box.call("route_to", {"target": p["id"]}) for p in brief.get("phones") or []]
    return hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()


def first_key(brief: dict) -> str:
    from beacon.agent import TASK, BeaconAgent, situation
    from beacon.llm import request_key
    agent = BeaconAgent(brief, None, None, None, llm=LLM("replay"), pace_s=0)
    user = TASK.replace("{situation}", json.dumps(situation(brief, agent.twin), sort_keys=True))
    return request_key("main", agent.system, [{"role": "user", "content": user}])


def prune_orphans(index: dict) -> int:
    keep = {k for r in index["runs"].values() for k in r["keys"]}
    n = 0
    for f in CACHE_DIR.glob("*.json"):
        if f.name != "RUNS.json" and f.stem not in keep:
            f.unlink()
            n += 1
    return n


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
    if args.stability:
        rec = index["runs"].get("walk-through") or {}
        want = (rec.get("keys") or [None])[0]
        want_sweep = tool_sweep_key(rec["brief"]) if rec.get("brief") else None
        hits = 0
        for i in range(args.stability):
            b = await walkthrough_brief()
            k, sw = first_key(b), tool_sweep_key(b)
            same = k == want and sw == want_sweep
            hits += same
            print(f"   walk-through #{i + 1}: phones {[(p['id'], p.get('geometry')) for p in b.get('phones') or []]}; first prompt "
                  f"{'= recorded' if k == want else 'DIFFERS'}; tool results {'= recorded' if sw == want_sweep else 'DIFFER'}")
        print(f"== stability: {hits} of {args.stability} walk-throughs give exactly the recorded prompts")
        return 0 if hits == args.stability else 1
    for name in names:
        t0 = time.time()
        if name == "walk-through":
            stored = (index["runs"].get(name) or {}).get("brief")
            source = stored if (args.check and stored) else await walkthrough_brief()
        else:
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
            if name == "walk-through":
                index["runs"][name]["brief"] = brief
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
    if not args.check:
        print(f"== removed {prune_orphans(index)} cache files no recorded run uses")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--situation", choices=SITUATIONS)
    ap.add_argument("--check", action="store_true", help="no model calls: verify that replay reproduces the recorded runs")
    ap.add_argument("--brief-file", help="a saved GET /api/brief (or Beacons object) to record instead")
    ap.add_argument("--stability", type=int, default=0, metavar="N",
                    help="no model calls: play the walk-through N times and check that each gives exactly the recorded prompts")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
