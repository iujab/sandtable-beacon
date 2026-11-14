"""Run the command agent's real vision tool (ToolBox.t_look_at_aerial) on live Vexcel imagery.

    python scripts/live_vision_probe.py <site_dir> <cache_dir> <out_json> [--site <site.json>] [--backend claude-cli]

<site_dir>   a fetched Vexcel site, e.g. data/live/vexcel/<site>/ (from scripts/live_vexcel.py fetch)
<cache_dir>  the private model cache, e.g. data/live/llm_cache (must be under data/live/; never fixtures/llm_cache)
<out_json>   where the answers go, under data/live/
--site       a site bundle (sandtable.site/1) for sides A-D, hydrants and the engine; without it the footprint
             comes from Vexcel's structure layer and the questions use compass directions.

Everything this writes (the annotated image copies, the model cache, the answers) stays under data/live/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandtable import config                                  # noqa: E402
from sandtable.adapters.vexcel_live import VexcelLive          # noqa: E402
from sandtable.agent.llm import LLM                             # noqa: E402
from sandtable.agent.tools import ToolBox                       # noqa: E402

QUESTIONS = [
    ("ortho", "access", "For a pre-incident plan: on each side of the outlined building (north, east, south, west), what is there "
                        "(street with lane count, alley, driveway, parking, plaza, neighbouring wall) and where could an engine and an "
                        "aerial ladder truck set up with clear access? Name overhead obstructions you can see (trees, wires, canopies)."),
    ("ortho", "parking", "Where are the parking areas and parking structures around the outlined building, roughly how many marked "
                         "stalls does each have (count them where you can), and how many vehicles are parked right now?"),
    ("roof", "roof", "What is on the roof of the outlined building: helipad or helistop markings, mechanical units, a penthouse, "
                     "stair or elevator housings, hatches, skylights, antennas, solar panels, parapets? Say where each one is."),
    ("ortho", "exposures", "Exposures: which buildings or hazards are directly adjacent to the outlined building on each side "
                           "(north, east, south, west), how close are they, and how tall do they look relative to it?"),
    ("oblique_w", "facade", "This is an oblique view looking west, so it shows the building's east face. How many storeys does the "
                            "building have, where are the ground-floor entrances, loading doors or garage openings on this face, and "
                            "is anything visible that matters to firefighters (fire department connection, balconies, scaffolding)?"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site_dir", type=Path)
    ap.add_argument("cache_dir", type=Path)
    ap.add_argument("out_json", type=Path)
    ap.add_argument("--site", type=Path, default=None)
    ap.add_argument("--backend", default="claude-cli")
    ap.add_argument("--only", default="", help="comma-separated question keys")
    args = ap.parse_args()

    live = config.LIVE_DIR.resolve()
    for p in (args.site_dir, args.cache_dir, args.out_json):
        if live not in p.resolve().parents and p.resolve() != live:
            sys.exit(f"{p} is not under {config.LIVE_DIR}: live imagery and anything derived from it stay there")
    config.DATA_DIR = config.LIVE_DIR             # the tool writes its annotated copy under DATA_DIR

    bundle = json.loads((args.site_dir / "aerial.json").read_text(encoding="utf-8"))
    lat, lon = bundle["site"]["lat"], bundle["site"]["lon"]
    if args.site:
        site = json.loads(args.site.read_text(encoding="utf-8"))
    else:
        s = bundle.get("structure") or {}
        if not s.get("footprint"):
            sys.exit("no footprint in the bundle; pass --site")
        site = {"property": {"lat": lat, "lon": lon}, "footprint": s["footprint"], "sides": {}, "context": {"hydrants": [], "apparatus_positions": []}}
    v = VexcelLive(os.environ.get("VEXCEL_API_KEY", ""), site, out_root=args.site_dir.parent)
    v._bundles[args.site_dir.name] = bundle
    provider = SimpleNamespace(site=lambda: site, vexcel=v, modes={"costar": "live" if args.site else "fixture", "matterport": "fixture", "vexcel": "live"})
    llm = LLM(backend=args.backend, record=True, cache_dir=args.cache_dir)
    tb = ToolBox(provider, None, None, llm)
    ids = {im["id"] for im in bundle["images"]}
    todo = [q for q in QUESTIONS if q[0] in ids and (not args.only or q[1] in args.only.split(","))]

    async def ask(image_id: str, key: str, question: str) -> dict:
        try:
            out = await tb.t_look_at_aerial(image_id, question)
            return {"key": key, "image": image_id, "question": question, "answer": out["answer"], "source": out["source"]}
        except Exception as exc:                  # report and keep the other answers
            return {"key": key, "image": image_id, "question": question, "error": f"{type(exc).__name__}: {exc}"}

    async def run() -> list[dict]:
        # one at a time: ToolBox.annotate_aerial writes one annotated copy per image id
        return [await ask(*q) for q in todo]

    answers = asyncio.run(run())
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps({"site": args.site_dir.name, "backend": args.backend, "calls": llm.calls, "answers": answers}, indent=1), encoding="utf-8")
    for a in answers:
        print(f"[{a['key']} on {a['image']}] " + (a.get("answer") or a.get("error", "")))
    print(f"model calls: {llm.calls}")


if __name__ == "__main__":
    main()
