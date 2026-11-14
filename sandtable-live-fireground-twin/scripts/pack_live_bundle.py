#!/usr/bin/env python3
"""Private laptop bundle: everything a second machine needs to reproduce this live run without re-deriving it.

    python scripts/pack_live_bundle.py pack [--out data/live/bundle/sandtable-live-bundle.zip]
    python scripts/pack_live_bundle.py unpack <archive> [--into data/live]
    python scripts/pack_live_bundle.py show <archive>                 the manifest's parts and sizes, nothing else

THE ARCHIVE CONTAINS SPONSOR DATA (CoStar, Matterport and Vexcel, and what was derived from them). It must never be
committed, uploaded, attached or shared: carry it by hand (USB stick, a private share you control). `pack` refuses an
output path that git does not ignore and that is not under a directory named `live`; `unpack` restores only into a
directory named `live`. Key values are never packed: every file is scanned for the key values of the environment
(.env and the bench drop zone) before the archive is written, and `pack` refuses if one is found.

Pure Python standard library (zipfile, hashlib, json); runs on Windows, macOS and Linux.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandtable import config  # noqa: E402  (loads .env and the bench drop zone into the environment; prints nothing)

SCHEMA = "sandtable.live_bundle/1"
WARNING = ("This archive contains sponsor data (CoStar, Matterport, Vexcel) and data derived from it. Never commit it, upload it, attach it "
           "or share it. Unpack it only into data/live/ of a Sandtable checkout. It holds no key values.")
DEFAULT_OUT = config.LIVE_DIR / "bundle" / "sandtable-live-bundle.zip"   # under a `live` directory, like everything private; no PARTS pattern matches bundle/

# What goes in: (part name, glob patterns relative to the live root, what it is, how the laptop could rebuild it instead)
PARTS = [
    ("building", ["demo_building.json", "candidates.json"], "the chosen demo building (property id, model id, address, storey, suite) and the shortlist",
     None),
    ("denylist", ["denylist_building.txt", "denylist_dropped.txt"], "terms scripts/check_publishable.sh refuses in publishable files",
     "python -m sandtable.adapters.costar_extract denylist"),
    ("site", ["site/**/*"], "public open-data context of the building (footprint, sides, hydrants, stations, NAIP aerials, raw answers)",
     "python scripts/build_site.py --from-demo"),
    ("costar", ["costar/_index/**/*"], "the CoStar data kit indexed for the building (per-property cache)",
     "python -m sandtable.adapters.costar_extract record   (with COSTAR_KIT_DIR set)"),
    ("matterport", ["matterport/cache/**/*", "matterport/twin_*.json", "matterport/validation_*.json", "matterport/tags_dryrun_*.json"],
     "Model API responses (cache) and the normalised twin of the scan", "python scripts/live_matterport.py normalize"),
    ("vexcel", ["vexcel/cache/**/*", "vexcel/{site}/*"], "Vexcel API responses (cache) and the images of the building",
     "python scripts/live_vexcel.py fetch --demo"),
    ("llm_cache", ["llm_cache/**/*"], "private recordings of real model runs made against the live data (replay with LLM_BACKEND=replay)",
     None),
    ("beacon_story", ["beacon/story_*.json"], "the recorded simulated Beacon run on the live twin that the hands-free exercise replays (the private agent "
     "recordings only match this exact file)", None),
    ("beacon_bundle", ["beacon_bundle/**/*"], "the Beacon module copy the launcher assembles on the live twin (re-assembled at every live start)",
     "python -m sandtable.demo (live mode assembles it)"),
]
EXCLUDE = re.compile(r"(\.log$|\.tmp$|\.pid$|__pycache__|/_review/|^vexcel/vision_probe|^matterport/sdk_test/|^matterport/render_|^figures/|^annotated_|\.pyc$)")
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def key_values() -> dict[str, bytes]:
    """Variable name -> value for every secret-looking variable of the environment and the env files (values stay in memory)."""
    out: dict[str, bytes] = {}
    files = [ROOT / ".env"] + sorted((ROOT.parent / ".secrets").glob("*.env"))
    for ef in files:
        if ef.is_file():
            for line in ef.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'")
                    if SECRET_NAME.search(k.upper()) and len(v) >= 6:
                        out[k] = v.encode()
    for k, v in os.environ.items():
        if SECRET_NAME.search(k.upper()) and len(v) >= 6 and k not in out:
            out[k] = v.encode()
    return out


def is_ignored_or_private(path: Path) -> bool:
    if "live" in path.resolve().parts[:-1]:
        return True
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


def collect(live: Path) -> list[tuple[str, str, Path]]:
    """(part, relative posix path, absolute path) of every file that goes into the bundle."""
    b = _demo(live)
    site = None
    if b and b.get("lat") is not None:
        try:
            from sandtable.adapters.vexcel_live import site_slug
            site = site_slug(float(b["lat"]), float(b["lon"]))
        except Exception:
            site = None
    out, seen = [], set()
    for part, patterns, _what, _rebuild in PARTS:
        for pat in patterns:
            if "{site}" in pat:
                if not site:
                    continue
                pat = pat.replace("{site}", site)
            for p in sorted(live.glob(pat)):
                if not p.is_file():
                    continue
                rel = p.relative_to(live).as_posix()
                if rel in seen or EXCLUDE.search(rel):
                    continue
                seen.add(rel)
                out.append((part, rel, p))
    return out


def _demo(live: Path) -> dict | None:
    try:
        return json.loads((live / "demo_building.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pack(live: Path, out: Path) -> dict:
    out = out.resolve()
    if not is_ignored_or_private(out):
        raise SystemExit(f"refusing: {out.name} would not be git-ignored. Write the archive under data/ (git-ignored) or a 'live' directory.")
    files = collect(live)
    if not files:
        raise SystemExit(f"nothing to pack under {live}")
    secrets = key_values()
    leaks = []
    for part, rel, p in files:
        data = p.read_bytes()
        for name, value in secrets.items():
            if value in data:
                leaks.append((rel, name))
    if leaks:
        for rel, name in leaks:
            print(f"  {rel} contains the value of {name}")
        raise SystemExit("refusing to pack: a key value was found in the files above (delete or regenerate them first)")
    manifest = {"schema": SCHEMA, "warning": WARNING, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "contains_sponsor_data": True, "contains_key_values": False,
                "parts": {part: {"what": what, "rebuild": rebuild or "only from this bundle", "files": 0, "bytes": 0}
                          for part, _pats, what, rebuild in PARTS},
                "files": []}
    for part, rel, p in files:
        size = p.stat().st_size
        manifest["files"].append({"path": rel, "part": part, "size": size, "sha256": sha256(p)})
        manifest["parts"][part]["files"] += 1
        manifest["parts"][part]["bytes"] += size
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=1))
        z.writestr("README_PRIVATE.txt", WARNING + "\n\nRestore with: python scripts/pack_live_bundle.py unpack <this archive>\n")
        for _part, rel, p in files:
            z.write(p, "live/" + rel)
    os.replace(tmp, out)
    return manifest


def read_manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as z:
        m = json.loads(z.read("MANIFEST.json"))
    if m.get("schema") != SCHEMA:
        raise SystemExit("not a Sandtable live bundle")
    return m


def unpack(archive: Path, into: Path) -> dict:
    into = into.resolve()
    if "live" not in into.parts:
        raise SystemExit("refusing: unpack only into a directory named 'live' (data/live/)")
    m = read_manifest(archive)
    want = {f["path"]: f for f in m["files"]}
    bad = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if not info.filename.startswith("live/") or info.is_dir():
                continue
            rel = info.filename[len("live/"):]
            target = (into / PurePosixPath(rel)).resolve()
            if not target.is_relative_to(into) or rel not in want:
                bad.append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".unpack")
            tmp.write_bytes(z.read(info))
            os.replace(tmp, target)
    mismatch = [rel for rel, f in want.items() if not (into / PurePosixPath(rel)).is_file() or sha256(into / PurePosixPath(rel)) != f["sha256"]]
    if bad or mismatch:
        raise SystemExit(f"unpack incomplete: {len(bad)} unexpected entries skipped, {len(mismatch)} files missing or with a wrong hash")
    return m


def missing_on_this_machine(into: Path) -> list[str]:
    """What the archive cannot carry: keys, the CoStar kit, the selections. Names only."""
    todo = []
    for name in ("VEXCEL_API_KEY", "MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET", "MATTERPORT_SDK_KEY"):
        if not os.environ.get(name):
            todo.append(f"{name} is not set (put it in .env)")
    if not (os.environ.get("MATTERPORT_MODEL_ID") or (_demo(into) or {}).get("model_id")):
        todo.append("MATTERPORT_MODEL_ID (or data/live/demo_building.json with model_id)")
    from sandtable.adapters.costar_extract import default_kit_dir, is_kit
    kit = default_kit_dir()
    if not is_kit(kit):
        todo.append("the CoStar data kit: unzip it OUTSIDE the repository and set COSTAR_KIT_DIR to that folder "
                    "(the bundle carries the building's indexed record, enough for the demo building; other buildings need the kit)")
    if not (os.environ.get("COSTAR_PROPERTY_ID") or (_demo(into) or {}).get("property_id")):
        todo.append("COSTAR_PROPERTY_ID (or data/live/demo_building.json with property_id)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        todo.append("ANTHROPIC_API_KEY is not set: live model decisions need it (LLM_BACKEND=anthropic); otherwise the private recordings replay")
    return todo


def _summary(m: dict) -> None:
    for part, info in m["parts"].items():
        if info["files"]:
            print(f"  {part:13s} {info['files']:5d} files {info['bytes'] / 1e6:8.1f} MB   {info['what']}")
            print(f"  {'':13s} rebuild: {info['rebuild']}")
    empty = [p for p, i in m["parts"].items() if not i["files"]]
    if empty:
        print("  not in this bundle: " + ", ".join(empty))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--from", dest="src", default=str(config.LIVE_DIR), help="the live root to pack (default: data/live)")
    u = sub.add_parser("unpack")
    u.add_argument("archive")
    u.add_argument("--into", default=str(config.LIVE_DIR))
    s = sub.add_parser("show")
    s.add_argument("archive")
    a = ap.parse_args(argv)
    print(WARNING)
    if a.cmd == "pack":
        out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
        m = pack(Path(a.src), out)
        print(f"packed {len(m['files'])} files into {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} ({out.stat().st_size / 1e6:.1f} MB):")
        _summary(m)
    elif a.cmd == "unpack":
        into = Path(a.into) if Path(a.into).is_absolute() else ROOT / a.into
        m = unpack(Path(a.archive), into)
        print(f"restored {len(m['files'])} files into {into.relative_to(ROOT) if into.is_relative_to(ROOT) else into}; every hash matches the manifest:")
        _summary(m)
        todo = missing_on_this_machine(into)
        print("still needed on this machine:" if todo else "nothing else is missing: run python -m sandtable.adapters.check --live")
        for t in todo:
            print(f"  - {t}")
    else:
        m = read_manifest(Path(a.archive))
        print(f"created {m['created']}, {len(m['files'])} files")
        _summary(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
