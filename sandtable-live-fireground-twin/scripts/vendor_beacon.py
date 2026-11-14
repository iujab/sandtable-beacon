"""Vendor the Beacon module (sibling lane L16, ../sandtable-beacon-victim-phones) into vendor/beacon/ so this repository
stands alone and one command (python -m sandtable.demo) runs the whole product.

    python scripts/vendor_beacon.py            copy (idempotent: vendor/beacon is replaced by a fresh copy each time)
    python scripts/vendor_beacon.py --check    report what would be copied, change nothing

Copies the `beacon/` package, `fixtures/`, `web/`, `apps/`, `hardware/`, `docs/`, the README and the requirements; skips
`data/`, virtualenvs, caches and anything the sibling's .gitignore ignores. Writes vendor/beacon/VENDORED.md (source,
time, credit, how to run), docs/owner_questions_beacon.json (the sibling's `owner_questions`), and merges its
requirements into ours without duplicates. The sibling directory is only read, never written.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "sandtable-beacon-victim-phones"
DST = ROOT / "vendor" / "beacon"
INCLUDE_DIRS = ["beacon", "fixtures", "web", "apps", "hardware", "docs"]
INCLUDE_FILES = ["README.md", "requirements.txt", "run_demo.sh", "LICENSE"]
ALWAYS_SKIP = [".git/", ".venv/", "venv/", "env/", "node_modules/", "data/", "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
               "*.pyc", "*.pyo", ".DS_Store", "*.pid", ".env", "xcuserdata/", "DerivedData/", "build/", "dist/"]


def ignore_patterns() -> list[str]:
    pats = list(ALWAYS_SKIP)
    gi = SRC / ".gitignore"
    if gi.is_file():
        for line in gi.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                pats.append(line)
    return pats


def ignored(rel: Path, is_dir: bool, pats: list[str]) -> bool:
    parts = rel.parts
    for pat in pats:
        dir_only = pat.endswith("/")
        p = pat.rstrip("/").lstrip("/")
        if dir_only and not is_dir:
            if any(fnmatch.fnmatch(part, p) for part in parts[:-1]):
                return True
            continue
        if "/" in p:
            if fnmatch.fnmatch(rel.as_posix(), p) or fnmatch.fnmatch(rel.as_posix(), p + "/*"):
                return True
        elif any(fnmatch.fnmatch(part, p) for part in parts):
            return True
    return False


def plan() -> list[Path]:
    pats = ignore_patterns()
    files: list[Path] = []
    for name in INCLUDE_FILES:
        if (SRC / name).is_file():
            files.append(Path(name))
    for d in INCLUDE_DIRS:
        base = SRC / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(SRC)
            if path.is_dir() or ignored(rel, False, pats) or any(ignored(Path(*rel.parts[:i]), True, pats) for i in range(1, len(rel.parts))):
                continue
            if path.is_symlink():
                continue
            files.append(rel)
    return files


def _req_name(line: str) -> str | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    if line.startswith((".", "/")):
        return line                                     # a local path requirement: compare verbatim
    m = re.match(r"([A-Za-z0-9_.\-]+)", line)
    return m.group(1).lower().replace("_", "-") if m else None


def merge_requirements(check: bool) -> list[str]:
    src = SRC / "requirements.txt"
    if not src.is_file():
        return []
    ours_path = ROOT / "requirements.txt"
    ours = ours_path.read_text(encoding="utf-8")
    have = {n for n in (_req_name(x) for x in ours.splitlines()) if n}
    add = []
    for line in src.read_text(encoding="utf-8").splitlines():
        n = _req_name(line)
        if n and n not in have and not n.startswith((".", "/")):
            add.append(line.split("#", 1)[0].strip())
            have.add(n)
    if add and not check:
        marker = "# from vendor/beacon (lane L16's Beacon module)"
        text = ours.rstrip("\n") + "\n" + (marker + "\n" if marker not in ours else "") + "\n".join(add) + "\n"
        ours_path.write_text(text, encoding="utf-8")
    return add


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if not (SRC / "beacon").is_dir():
        print(f"[vendor_beacon] nothing to vendor: {SRC.name}/beacon does not exist", file=sys.stderr)
        return 1
    files = plan()
    size = sum((SRC / f).stat().st_size for f in files)
    print(f"[vendor_beacon] {len(files)} files, {size / 1e6:.1f} MB from ../{SRC.name}")
    if args.check:
        add = merge_requirements(check=True)
        print("[vendor_beacon] requirements to add:", add or "none")
        return 0
    tmp = DST.with_name("beacon.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    for rel in files:
        out = tmp / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, out)
    commit = None
    head = SRC / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        commit = (SRC / ".git" / ref[5:]).read_text().strip() if ref.startswith("ref: ") else ref
    except OSError:
        pass
    pj = {}
    try:
        pj = json.loads((SRC / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (tmp / "VENDORED.md").write_text(f"""# Vendored: the Beacon module (lane L16)

This directory is a copy of `../sandtable-beacon-victim-phones/` (sibling lane **L16**, "{pj.get('title', 'Sandtable Beacon')}"),
made by `scripts/vendor_beacon.py` on {stamp}{f' from commit `{commit[:12]}`' if commit else ''}. It is that team's work, credited
to them; it is copied (not linked) so this repository stands alone. Do not edit it here: change the sibling project and
re-run `python scripts/vendor_beacon.py`.

Copied: `beacon/` (the server, locator, tracker, simulator), `fixtures/`, `web/`, `apps/`, `hardware/`, `docs/`, the README and
requirements. Skipped: `data/`, virtualenvs, caches and everything the sibling's `.gitignore` ignores.

## How it runs here

`python -m sandtable.demo` starts it by itself on port 1885 when nothing answers there (and stops it on exit), then points
the board at it (`BEACON_URL`). By hand:

```bash
cd vendor/beacon && python -m beacon.demo --port 1885      # simulated world, http://127.0.0.1:1885
```

Its feed contract is `docs/BEACON_FEED.md`. If it is absent or fails to start, the Sandtable board relays the
hand-made sample (`fixtures/beacon/sample_beacons.json`), labelled SAMPLE on screen.
""", encoding="utf-8")
    if DST.exists():
        shutil.rmtree(DST)
    tmp.rename(DST)
    oq = pj.get("owner_questions")
    if oq is not None:
        (ROOT / "docs" / "owner_questions_beacon.json").write_text(json.dumps({"source": "../sandtable-beacon-victim-phones/project.json (lane L16)",
                                                                              "vendored": stamp, "owner_questions": oq}, indent=1) + "\n", encoding="utf-8")
    sample = SRC / "fixtures" / "beacon" / "sample_beacons.json"          # our copy of its sample changes only here
    if sample.is_file():
        (ROOT / "fixtures" / "beacon").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, ROOT / "fixtures" / "beacon" / "sample_beacons.json")
    add = merge_requirements(check=False)
    print(f"[vendor_beacon] copied into vendor/beacon/; owner questions: {len(oq) if oq else 0}; requirements added: {add or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
