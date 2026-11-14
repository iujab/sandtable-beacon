"""Matterport live helper. Every response is cached under data/live/matterport/cache/ and everything it writes
stays under data/live/ (git-ignored, never published). Keys come from the environment (sandtable/config.py loads them).

    python scripts/live_matterport.py search "<text>" [--n 10]
    python scripts/live_matterport.py inspect  [<model id> | pick]      # counts: floors, rooms, walls, doorways, sweeps
    python scripts/live_matterport.py normalize [<model id> | pick] [--refresh] [--storey N] [--suite S]
    python scripts/live_matterport.py render   [<model id> | pick] [--floor ID]

`pick` (the default) is the model in data/live/demo_building.json; MATTERPORT_MODEL_ID also works.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandtable import config  # noqa: E402  (loads the keys into the environment)
from sandtable.adapters import matterport_queries as Q  # noqa: E402
from sandtable.adapters.matterport_live import LIVE_DIR, GraphClient, MatterportLive  # noqa: E402

PICK = LIVE_DIR.parent / "demo_building.json"


def _client() -> GraphClient:
    for k in ("MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET"):
        if not os.environ.get(k):
            sys.exit(f"{k} is not set (put it in the bench .secrets/matterport.env)")
    return GraphClient(os.environ["MATTERPORT_TOKEN_ID"], os.environ["MATTERPORT_TOKEN_SECRET"])


def _model_id(arg: str | None) -> tuple[str, dict]:
    pick = json.loads(PICK.read_text(encoding="utf-8")) if PICK.exists() else {}
    if arg in (None, "", "pick"):
        mid = pick.get("model_id") or os.environ.get("MATTERPORT_MODEL_ID")
        if not mid:
            sys.exit("no model: pass a model id, or write data/live/demo_building.json, or set MATTERPORT_MODEL_ID")
        return mid, pick if pick.get("model_id") == mid else {}
    return arg, pick if pick.get("model_id") == arg else {}


def _adapter(mid: str, pick: dict, a) -> MatterportLive:
    storey = a.storey if getattr(a, "storey", None) is not None else pick.get("floor")
    suite = getattr(a, "suite", None) or pick.get("suite")
    return MatterportLive(os.environ["MATTERPORT_TOKEN_ID"], os.environ["MATTERPORT_TOKEN_SECRET"], mid, refresh=getattr(a, "refresh", False),
                          storey=int(storey) if storey is not None else None, suite=suite)


def cmd_search(a) -> None:
    data = _client().query(Q.SEARCH, {"query": a.text, "pageSize": min(a.n, 25)})["models"]
    print(f"{data['totalResults']} results (showing {len(data['results'])})")
    for m in data["results"]:
        addr = (m.get("address") or {}).get("address")
        print(f"  {m['id']}  {m['name']!r}  [{m['state']}/{m['visibility']}]  {addr}  floors={(m.get('floors') or {}).get('count')} "
              f"rooms={(m.get('rooms') or {}).get('count')} sweeps={((m.get('assets') or {}).get('locations') or {}).get('count')}")


def cmd_inspect(a) -> None:
    mid, pick = _model_id(a.model)
    m = _adapter(mid, pick, a).raw_model()
    print(f"{m['name']!r} [{m.get('state')}/{m.get('visibility')}] geocoordinates={(m.get('geocoordinates') or {}).get('source')}")
    for f in sorted(m.get("floors") or [], key=lambda f: f.get("sequence", 0)):
        rooms = [r for r in m.get("rooms") or [] if (r.get("floor") or {}).get("id") == f["id"]]
        locs = [l for l in m.get("locations") or [] if (l.get("floor") or {}).get("id") == f["id"]]
        ops = [o["type"] for e in f.get("edges") or [] for o in e.get("openings") or []]
        print(f"  floor {f.get('label')!r}: {len(rooms)} rooms ({sum(1 for r in rooms if (r.get('boundary') or {}).get('edges'))} with a boundary), "
              f"{sum(1 for e in f.get('edges') or [] if e.get('type') == 'wall')} wall edges, openings {dict((t, ops.count(t)) for t in set(ops))}, "
              f"{len(locs)} sweeps ({sum(len(l.get('neighbors') or []) for l in locs)} neighbour refs)")
        for r in rooms:
            d = r.get("dimensions") or {}
            print(f"     room label={r.get('label')!r} class={[c['label'] for c in r.get('classifications') or []]} tags={r.get('tags')} "
                  f"area={d.get('areaFloor') and round(d['areaFloor'], 1)} sweeps={sum(1 for l in locs if (l.get('room') or {}).get('id') == r['id'])}")
    print(f"  mattertags: {len(m.get('mattertags') or [])}")


def validate(twin: dict, pick: dict) -> dict:
    """The checks the rest of the system needs: every room reachable from an exit, locate/route work, to_geo lands on the building."""
    from sandtable.spatial import Twin, geo_distance_m
    t = Twin(twin)
    ex = t.exits()
    rooms = [r for r in twin["rooms"] if r["floor"] == t.exercise_floor]
    unreachable = [r["label"] for r in rooms if not t.route(r["id"], "exit")["ok"]]
    allrooms_unreach = [r["label"] for r in twin["rooms"] if ex and not t.route(r["id"], ex[0]["id"])["ok"]]
    c = t.room_centre(rooms[0]["id"]) if rooms else [0, 0]
    loc = t.locate(*c)
    far = max(rooms, key=lambda r: math.dist(t.room_centre(r["id"]), t.resolve(ex[0]["id"])["at"])) if rooms and ex else None
    r = t.route(far["id"], "exit") if far else {"ok": False, "length_m": 0, "steps": []}
    xs = [p[0] for p in t.floor()["outline"]]
    ys = [p[1] for p in t.floor()["outline"]]
    lat, lon = t.to_geo((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    out = {"exercise_floor_label": t.floor()["label"], "rooms_on_floor": len(rooms), "exits": [e["label"] for e in ex],
           "rooms_without_route_to_exit": unreachable, "rooms_unreachable_anywhere": allrooms_unreach,
           "locate_room_centre": {"room_label": loc["room_label"], "node": bool(loc["node"]), "sweep": bool(loc["sweep"])},
           "farthest_room_to_exit": {"from": far["label"] if far else None, "ok": r["ok"], "length_m": r["length_m"], "steps": [s["text"] for s in r["steps"]]},
           "plan_centre_geo": [round(lat, 6), round(lon, 6)]}
    if pick.get("lat") is not None:
        out["plan_centre_to_building_record_m"] = round(geo_distance_m(lat, lon, pick["lat"], pick["lon"]), 1)
    return out


def cmd_normalize(a) -> None:
    mid, pick = _model_id(a.model)
    ad = _adapter(mid, pick, a)
    ad.refresh = True if a.refresh or a.rebuild else False
    if a.rebuild and not a.refresh:                       # re-normalise from the cached API responses, without refetching
        ad.refresh = False
        p = ad.twin_path()
        if p and p.exists():
            p.unlink()
    twin = ad.get_model()
    s = twin["source"]
    print(f"twin: {len(twin['floors'])} floors, {len(twin['rooms'])} rooms, {len(twin['walls'])} walls, {len(twin['doors'])} doors, "
          f"{len(twin['stairs'])} exits/stairs, {len(twin['sweeps'])} sweeps, {len(twin['graph']['nodes'])} nodes, {len(twin['graph']['edges'])} edges; "
          f"API requests sent this run: {ad.client.requests_sent}")
    print("counts:", json.dumps(s["counts"]))
    print("georef:", json.dumps({k: v for k, v in twin["georef"].items() if k != "origin"}))
    print("approximations:", *(["  - " + x for x in s["approximations"]] or ["  none"]), sep="\n")
    v = validate(twin, pick)
    print("validation:", json.dumps(v, indent=1))
    (LIVE_DIR / f"validation_{mid}.json").write_text(json.dumps(v, indent=1), encoding="utf-8")


def cmd_render(a) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    mid, pick = _model_id(a.model)
    twin = _adapter(mid, pick, a).get_model()
    fid = a.floor or twin["exercise_floor"]
    fl = next(f for f in twin["floors"] if f["id"] == fid)
    fig, ax = plt.subplots(figsize=(14, 10), dpi=110)
    colours = {"suite": "#e8eef7", "corridor": "#f4f1e6", "stair": "#f6d3d0", "lobby": "#e4f2e2", "restroom": "#eee4f4", "mechanical": "#e6e6e6", "elevator": "#d9d9d9"}
    o = fl["outline"]
    ax.fill([p[0] for p in o], [p[1] for p in o], color="#fafafa", ec="#999", lw=1, ls="--", zorder=0)
    for r in twin["rooms"]:
        if r["floor"] != fid:
            continue
        p = r["polygon"]
        ax.fill([q[0] for q in p], [q[1] for q in p], color=colours.get(r["kind"], "#fff"), ec="#7a8aa0", lw=0.6, zorder=1)
        cx, cy = sum(q[0] for q in p) / len(p), sum(q[1] for q in p) / len(p)
        ax.text(cx, cy, f"{r['label']}\n{r['kind']} {r['area_m2']:.0f} m²", ha="center", va="center", fontsize=7, zorder=6)
    for w in twin["walls"]:
        if w["floor"] == fid:
            ax.plot([w["a"][0], w["b"][0]], [w["a"][1], w["b"][1]], color="#222", lw=2, zorder=3)
    nodes = {n["id"]: n for n in twin["graph"]["nodes"]}
    for e in twin["graph"]["edges"]:
        a_, b_ = nodes[e["a"]], nodes[e["b"]]
        if a_["floor"] == fid and b_["floor"] == fid:
            ax.plot([a_["at"][0], b_["at"][0]], [a_["at"][1], b_["at"][1]], color="#3b82f6" if not e["via"] else "#f59e0b", lw=0.7, alpha=0.7, zorder=4)
    for n in nodes.values():
        if n["floor"] == fid:
            ax.plot(*n["at"], marker={"sweep": "o", "stair": "s", "door": "D", "exit": "*", "room": "^"}.get(n["kind"], "o"),
                    ms={"exit": 14}.get(n["kind"], 4), color={"exit": "#dc2626", "door": "#f59e0b", "room": "#16a34a"}.get(n["kind"], "#1e40af"), zorder=5)
    for d in twin["doors"]:
        if d["floor"] == fid:
            ax.text(d["at"][0], d["at"][1] + 0.35, d["id"] + (" EXIT" if d["kind"] == "exit" else ""), fontsize=6, color="#b45309", ha="center", zorder=7)
    for s in twin["stairs"]:
        n = nodes.get(s.get("node") or "")
        if n and n["floor"] == fid:
            ax.annotate(s["label"], n["at"], xytext=(8, -12), textcoords="offset points", fontsize=8, color="#dc2626", weight="bold", zorder=8)
    b = math.radians(twin["georef"]["bearing_deg"])
    xs, ys = [p[0] for p in o], [p[1] for p in o]
    nx, ny = math.cos(b), math.sin(b)                     # +north in the building frame: x = cos b, y = sin b
    ox, oy = max(xs) - 1.5, max(ys) - 1.5
    ax.annotate("N", (ox + 1.2 * nx, oy + 1.2 * ny), (ox, oy), arrowprops={"arrowstyle": "->"}, fontsize=10, ha="center")
    ax.set_aspect("equal")
    ax.set_title(f"normalised twin, storey {fl['label']} ({fl.get('name')}): {sum(1 for r in twin['rooms'] if r['floor'] == fid)} rooms, "
                 f"blue = Matterport sweep links, orange = through a door, red star = exit", fontsize=9)
    out = LIVE_DIR / f"render_{mid}_{fl['label']}.png"
    fig.savefig(out, bbox_inches="tight")
    print("wrote", out.relative_to(config.ROOT))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("text")
    s.add_argument("--n", type=int, default=10)
    for name in ("inspect", "normalize", "render"):
        p = sub.add_parser(name)
        p.add_argument("model", nargs="?", default="pick")
        p.add_argument("--refresh", action="store_true", help="refetch from the API (otherwise the cache is used)")
        p.add_argument("--rebuild", action="store_true", help="re-normalise from cached API responses")
        p.add_argument("--storey", type=int, default=None)
        p.add_argument("--suite", default=None)
        p.add_argument("--floor", default=None)
    a = ap.parse_args()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    {"search": cmd_search, "inspect": cmd_inspect, "normalize": cmd_normalize, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    main()
