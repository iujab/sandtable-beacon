"""Debug helper: run the pre-plan in replay mode outside the server and show the first request that misses the cache."""
import asyncio, json, difflib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandtable import config
from sandtable.adapters import get_provider
from sandtable.spatial import Twin
from sandtable.incident import Incident
from sandtable.agent import CommandAgent
from sandtable.agent import llm as L

async def main():
    p = get_provider(); twin = Twin(p.twin_raw()); events = []
    inc = Incident(twin, p, events.append)
    agent = CommandAgent(p, twin, inc, events.append, L.LLM(backend="replay"))
    orig = agent.llm.complete
    async def spy(system, messages, tier="main", image_path=None, image_sha=None):
        try:
            return await orig(system, messages, tier, image_path, image_sha)
        except L.ReplayMiss as miss:
            if image_path and not image_sha: image_sha = L.file_sha(image_path)
            want = L.canonical_request(tier, system, messages, image_sha)
            best = None
            for f in config.LLM_CACHE_DIR.glob("*.json"):
                c = json.loads(f.read_text()); r = c["request"]
                have = L.canonical_request(c["tier"], r["system"], r["messages"], r["image_sha"])
                ratio = difflib.SequenceMatcher(None, want[-6000:], have[-6000:]).quick_ratio() if len(r["messages"]) == len(messages) else 0
                if best is None or ratio > best[0]: best = (ratio, f.name, have)
            print("MISS at", len(messages), "messages; closest cache file", best[1], "ratio", round(best[0], 3))
            a, b = want, best[2]
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    print("first difference at char", i); print("  WANT:", a[max(0, i-200):i+300]); print("  HAVE:", b[max(0, i-200):i+300]); break
            else:
                print("one is a prefix of the other; lengths", len(a), len(b))
            raise
    agent.llm.complete = spy
    config.REPLAY_PACE_S = 0
    inc.start()
    plan = await agent.run_preplan()
    print("preplan backend:", plan["backend"])

asyncio.run(main())
