"""Self-check of the three sponsor layers: `python -m sandtable.adapters.check [--live]`.

Per layer: the mode, which variables are present (names only, never values), what it read (counts only,
never values), and exactly what is missing and what to do. `--live` tries every layer live, one at a time
(the others stay fixture), and reports each. Exit code 0 when every layer tried answered.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .. import config

VARS = {
    "costar": ["COSTAR_KIT_DIR", "COSTAR_PROPERTY_ID", "COSTAR_PROPERTY_ADDRESS", "COSTAR_KIT_ZIP_PASSWORD"],
    "matterport": ["MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET", "MATTERPORT_MODEL_ID", "MATTERPORT_SDK_KEY", "MATTERPORT_STOREY", "MATTERPORT_SUITE",
                   "MATTERPORT_WRITE_TAGS"],
    "vexcel": ["VEXCEL_API_KEY", "VEXCEL_OFFLINE"],
    "llm": ["LLM_BACKEND", "ANTHROPIC_API_KEY"],
}
TODO = {
    "costar": "unzip the CoStar Data Extract into data/live/costar/ (python -m sandtable.adapters.costar_extract unzip <zip>) or set COSTAR_KIT_DIR "
              "to its folder; select the building with COSTAR_PROPERTY_ID (or data/live/demo_building.json); then "
              "python -m sandtable.adapters.costar_extract inspect to see what mapped",
    "matterport": "put MATTERPORT_TOKEN_ID, MATTERPORT_TOKEN_SECRET and MATTERPORT_MODEL_ID in .env (the model id is the part after ?m= in the "
                  "showcase link); the SDK key works only from localhost pages",
    "vexcel": "put VEXCEL_API_KEY in .env",
}


def _rel(p) -> str:
    try:
        return str(Path(p).resolve().relative_to(config.ROOT))
    except ValueError:
        return Path(p).name + "/ (outside the project)"


def _clean(exc: Exception) -> str:
    """An error message without URLs' query strings or long digit runs (ids, coordinates)."""
    text = re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]*", r"\1?...", str(exc))
    text = re.sub(r"-?\d+\.\d{3,}", "<n>", text)
    text = re.sub(r"\d{5,}", "<id>", text)
    return f"{type(exc).__name__}: {text[:220]}"


def _vars(layer: str) -> str:
    return ", ".join(f"{n}={'present' if os.environ.get(n) else 'absent'}" for n in VARS[layer])


def _summary(layer: str, p) -> str:
    if layer == "costar":
        site = p.site()
        prop = site["property"]
        filled = sum(1 for v in prop.values() if v not in (None, [], {}))
        rep = getattr(p.costar, "report", {}) or {}
        ctx = site.get("context") or {}
        parts = [f"{filled} property fields filled", f"{len(site.get('tenants', []))} current tenant rows",
                 f"{len(site.get('available_spaces', []))} available spaces"]
        comps = site.get("comparables") or {}
        if comps:
            parts.append(f"{len(comps.get('sales', []))} sale comps, {len(comps.get('leases', []))} lease comps")
        if rep.get("matterport_models") is not None:
            parts.append(f"{rep['matterport_models']} Matterport model ids on record")
        parts.append(f"public context: {'merged' if site.get('footprint') else 'MISSING'} ({len(ctx.get('hydrants', []))} hydrants, {len(ctx.get('fire_stations', []))} stations)")
        if rep.get("property_fields_empty"):
            parts.append("empty in the record: " + ", ".join(rep["property_fields_empty"]))
        return "; ".join(parts)
    if layer == "matterport":
        t = p.twin_raw()
        return (f"{len(t.get('floors', []))} floors, {len(t.get('rooms', []))} rooms, {len(t.get('sweeps', []))} sweeps, {len(t.get('tags', []))} tags, "
                f"{len((t.get('graph') or {}).get('edges', []))} graph edges, synthetic={(t.get('source') or {}).get('synthetic')}")
    a = p.aerial()
    kinds = {}
    for im in a.get("images", []):
        kinds[im.get("kind")] = kinds.get(im.get("kind"), 0) + 1
    return f"{len(a.get('images', []))} images ({', '.join(f'{n} {k}' for k, n in kinds.items())})"


def check_layer(layer: str, mode: str) -> bool:
    from . import build_provider
    modes = {"costar_mode": "fixture", "matterport_mode": "fixture", "vexcel_mode": "fixture", f"{layer}_mode": mode}
    print(f"\n[{layer}] mode={mode}")
    print(f"  variables: {_vars(layer)}")
    if mode == "live":
        config.enter_live([layer])            # what build_provider does first: the paths below are the live ones
    if layer == "costar":
        from .costar_extract import default_kit_dir, discover
        kit = default_kit_dir()
        n = len(discover(kit)) if kit.is_dir() else 0
        idx = config.LIVE_DIR / "costar" / "_index"
        print(f"  extract folder: {'found' if kit.is_dir() else 'absent'} ({n} table files)"
              + (f"; private index: {len(list(idx.glob('property_*.json')))} building record(s){' (used, since there is no extract)' if not n else ''}"
                 if (idx / "enums.json").is_file() else ""))
        b = config.demo_building()
        print(f"  data/live/demo_building.json: {'present, keys ' + ', '.join(sorted(b)) if b else 'absent'}")
        ctx = _rel(config.site_context_json())
        print(f"  public context in use: {re.sub(r'site/[^/]+/', 'site/<demo building>/', ctx)}")
    try:
        p = build_provider(**modes)
        print(f"  OK   read: {_summary(layer, p)}")
        print(f"  label: {p.labels()[layer]['text']}")
        return True
    except Exception as exc:
        print(f"  FAIL {_clean(exc)}")
        if mode == "live":
            print(f"  to do: {TODO[layer]}")
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    live = "--live" in argv
    print("Sandtable adapter check" + (" (--live: every layer tried live, one at a time)" if live else ""))
    ok = True
    for layer in ("costar", "matterport", "vexcel"):
        mode = "live" if live else {"costar": config.COSTAR_MODE, "matterport": config.MATTERPORT_MODE, "vexcel": config.VEXCEL_MODE}[layer]
        ok = check_layer(layer, mode) and ok
    print(f"\n[llm] backend={config.LLM_BACKEND}; variables: {_vars('llm')}")
    cache = config.llm_cache_dir()
    print(f"  replay cache in use: {_rel(cache)} ({len(list(cache.glob('*.json'))) if cache.is_dir() else 0} recordings)"
          + ("; fixtures/llm_cache/ is read-only in this process" if config.live_active() else ""))
    print(f"\n[privacy] live layers in this process: {', '.join(config.live_layers()) or 'none'}; runtime data dir: {_rel(config.DATA_DIR)}; "
          f"private root: {_rel(config.LIVE_DIR)}")
    if config.live_active():
        print("  everything read live stays under the private root; nothing there is ever committed (see docs/TOOLKIT_SWAP.md)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
