"""Self-check of the three sponsor layers: `python -m sandtable.adapters.check`.

Prints the mode of each layer, whether each key is present (never its value), and reads each layer once.
Exit code 0 when every configured layer answers.
"""
from __future__ import annotations

import os
import sys

from .. import config

KEYS = {"costar": ["COSTAR_API_KEY", "COSTAR_BASE_URL", "COSTAR_PROPERTY_ID"],
        "matterport": ["MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET", "MATTERPORT_MODEL_ID", "MATTERPORT_SDK_KEY"],
        "vexcel": ["VEXCEL_API_KEY"], "llm": ["ANTHROPIC_API_KEY"]}


def main() -> int:
    print("Sandtable adapter check")
    print(f"  modes: costar={config.COSTAR_MODE} matterport={config.MATTERPORT_MODE} vexcel={config.VEXCEL_MODE} llm={config.LLM_BACKEND}")
    for layer, names in KEYS.items():
        print(f"  {layer:10s} keys: " + ", ".join(f"{n}={'present' if os.environ.get(n) else 'absent'}" for n in names))
    ok = True
    try:
        from . import build_provider
        p = build_provider()
    except Exception as exc:
        print(f"  FAILED to build the provider: {exc}")
        return 1
    for name, fn in (("costar", lambda: p.site()), ("matterport", lambda: p.twin_raw()), ("vexcel", lambda: p.aerial())):
        try:
            data = fn()
            if name == "costar":
                pr = data["property"]
                detail = f"{pr.get('address')}, {pr.get('city')}: {pr.get('stories')} floors, {pr.get('rba_sf')} sq ft, {len(data.get('tenants', []))} tenant rows"
            elif name == "matterport":
                detail = f"{data['model'].get('name')}: {len(data['floors'])} floors, {len(data['rooms'])} rooms, {len(data['sweeps'])} sweeps, {len(data['graph']['edges'])} graph edges, synthetic={data['source'].get('synthetic')}"
            else:
                detail = ", ".join(f"{i['id']} ({i['kind']}, {i.get('gsd_m')} m/px, {i['source']})" for i in data["images"])
            print(f"  OK   {name:10s} [{p.modes[name]}] {detail}")
        except Exception as exc:
            ok = False
            print(f"  FAIL {name:10s} [{p.modes[name]}] {type(exc).__name__}: {exc}")
    for layer, lab in p.labels().items():
        if isinstance(lab, dict) and "text" in lab:
            print(f"  label {layer:10s}: {lab['text']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
