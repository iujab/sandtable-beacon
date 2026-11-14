#!/usr/bin/env python3
"""Small helper: merge fields into project.json atomically. Usage: update_project.py '<json patch>' (progress keys are merged)."""
import datetime, json, os, sys
from pathlib import Path
path = Path(__file__).resolve().parent.parent / "project.json"
doc = json.loads(path.read_text())
patch = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
for k, v in patch.items():
    if k in ("progress", "demo", "tests", "hardware", "self_score", "rubric", "sponsor_tech", "team") and isinstance(v, dict):
        doc.setdefault(k, {}).update(v)
    else:
        doc[k] = v
doc["progress"]["updated"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(doc, indent=2))
os.replace(tmp, path)
print("project.json updated:", doc["progress"]["percent"], doc["progress"]["phase"])
