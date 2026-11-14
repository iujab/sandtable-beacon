"""Vendor the Beacon module (sibling lane L16, ../sandtable-beacon-victim-phones) into vendor/beacon/ so this repository
stands alone and one command (python -m sandtable.demo) runs the whole product.

    python scripts/vendor_beacon.py                   copy the sibling's current COMMIT (vendor/beacon is replaced each time)
    python scripts/vendor_beacon.py --check           report what would be copied, change nothing
    python scripts/vendor_beacon.py --update-sample   also replace our fixtures/beacon/sample_beacons.json (re-record after!)

What is copied: the files the sibling has COMMITTED at HEAD (`git archive`, so uncommitted work and everything its
.gitignore ignores stay out) under `beacon/`, `fixtures/`, `web/`, `apps/`, `hardware/`, `docs/`, `results/`, `scripts/`,
`tests/`, `figures/` (when its README shows them), plus README.md, requirements.txt, run_demo.sh, run_tests.sh, LICENSE.
Skipped: `data/`, virtualenvs, caches, build outputs, anything private (a `live` directory, `.env*`, keys, signing material).
Without git (a plain folder), the working tree is copied with the same rules.

Writes vendor/beacon/VENDORED.md from the copy itself (commit, every file and its size, what was skipped and why),
docs/owner_questions_beacon.json (the sibling's `owner_questions`), and merges its requirements into ours without
duplicates. Our fixtures/beacon/sample_beacons.json is replaced only with --update-sample, because recorded agent runs
replay against it. The sibling directory is only read, never written.
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "sandtable-beacon-victim-phones"
DST = ROOT / "vendor" / "beacon"
INCLUDE_DIRS = ["beacon", "fixtures", "web", "apps", "hardware", "docs", "results", "scripts", "tests"]
OPTIONAL_DIRS = {"figures": "figures/"}          # copied when the README refers to them
INCLUDE_FILES = ["README.md", "requirements.txt", "run_demo.sh", "run_tests.sh", "LICENSE"]
ALWAYS_SKIP = [".git/", ".venv/", "venv/", "env/", "node_modules/", "data/", "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
               "*.pyc", "*.pyo", ".DS_Store", "*.pid", ".env", ".env*", "xcuserdata/", "DerivedData/", "build/", "dist/", ".gradle/",
               "*.pem", "*.key", "*.p12", "*.cer", "*.mobileprovision", ".secrets/", "live/"]


def git(*args: str) -> bytes | None:
    try:
        r = subprocess.run(["git", "-C", str(SRC), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def ignore_patterns() -> list[str]:
    pats = list(ALWAYS_SKIP)
    gi = SRC / ".gitignore"
    if gi.is_file():
        for line in gi.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                pats.append(line)
    return pats


def skip_reason(rel: PurePosixPath, pats: list[str]) -> str | None:
    """Why a file is not vendored, or None. Directory patterns apply to any parent directory."""
    parts = rel.parts
    if "live" in parts[:-1]:
        return "under a 'live' directory (private)"
    for pat in pats:
        dir_only = pat.endswith("/")
        p = pat.rstrip("/").lstrip("/")
        if dir_only:
            if any(fnmatch.fnmatch(part, p) for part in parts[:-1]):
                return f"ignored ({pat})"
            continue
        if "/" in p:
            if fnmatch.fnmatch(rel.as_posix(), p) or fnmatch.fnmatch(rel.as_posix(), p + "/*"):
                return f"ignored ({pat})"
        elif any(fnmatch.fnmatch(part, p) for part in parts):
            return f"ignored ({pat})"
    return None


def wanted_roots() -> list[str]:
    roots = list(INCLUDE_DIRS)
    readme = SRC / "README.md"
    text = readme.read_text(encoding="utf-8", errors="ignore") if readme.is_file() else ""
    for d, needle in OPTIONAL_DIRS.items():
        if needle in text:
            roots.append(d)
    return roots


def collect() -> tuple[dict[str, bytes], list[tuple[str, str]], dict]:
    """(files: relative posix path -> bytes, skipped: (path, reason), source info)."""
    pats, roots = ignore_patterns(), wanted_roots()
    files: dict[str, bytes] = {}
    skipped: list[tuple[str, str]] = []
    commit = (git("rev-parse", "HEAD") or b"").decode().strip() or None
    info = {"commit": commit, "method": "git archive HEAD" if commit else "working tree", "roots": roots}
    if commit:
        info["commit_date"] = (git("show", "-s", "--format=%cI", "HEAD") or b"").decode().strip()
        info["commit_subject"] = (git("show", "-s", "--format=%s", "HEAD") or b"").decode().strip()
        dirty = (git("status", "--porcelain") or b"").decode().splitlines()
        info["uncommitted_in_source"] = len(dirty)
        raw = git("archive", "--format=tar", "HEAD", "--", *[r for r in roots + INCLUDE_FILES if git("ls-files", "--", r)])
        if raw is None:
            raise SystemExit("git archive failed")
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    continue
                rel = PurePosixPath(m.name)
                why = skip_reason(rel, pats)
                if why:
                    skipped.append((m.name, why))
                else:
                    files[m.name] = tar.extractfile(m).read()
    else:
        for name in INCLUDE_FILES:
            if (SRC / name).is_file():
                files[name] = (SRC / name).read_bytes()
        for d in roots:
            for path in sorted((SRC / d).rglob("*")) if (SRC / d).is_dir() else []:
                if not path.is_file() or path.is_symlink():
                    continue
                rel = PurePosixPath(path.relative_to(SRC).as_posix())
                why = skip_reason(rel, pats)
                if why:
                    skipped.append((str(rel), why))
                else:
                    files[str(rel)] = path.read_bytes()
    return files, skipped, info


def _req_name(line: str) -> str | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    if line.startswith((".", "/")):
        return line                                     # a local path requirement: compare verbatim
    m = re.match(r"([A-Za-z0-9_.\-]+)", line)
    return m.group(1).lower().replace("_", "-") if m else None


def merge_requirements(src_text: str | None, check: bool) -> list[str]:
    if not src_text:
        return []
    ours_path = ROOT / "requirements.txt"
    ours = ours_path.read_text(encoding="utf-8")
    have = {n for n in (_req_name(x) for x in ours.splitlines()) if n}
    add = []
    for line in src_text.splitlines():
        n = _req_name(line)
        if n and n not in have and not n.startswith((".", "/")):
            add.append(line.split("#", 1)[0].strip())
            have.add(n)
    if add and not check:
        marker = "# from vendor/beacon (lane L16's Beacon module)"
        text = ours.rstrip("\n") + "\n" + (marker + "\n" if marker not in ours else "") + "\n".join(add) + "\n"
        ours_path.write_text(text, encoding="utf-8")
    return add


def vendored_md(files: dict[str, bytes], skipped: list[tuple[str, str]], info: dict, pj: dict, stamp: str) -> str:
    by_root: dict[str, list[int]] = {}
    for rel, data in files.items():
        root = rel.split("/", 1)[0] if "/" in rel else "(top level)"
        by_root.setdefault(root, [0, 0])
        by_root[root][0] += 1
        by_root[root][1] += len(data)
    reasons: dict[str, int] = {}
    for _p, why in skipped:
        reasons[why] = reasons.get(why, 0) + 1
    commit = info.get("commit")
    lines = [
        "# Vendored: the Beacon module (lane L16)",
        "",
        f"A copy of `../sandtable-beacon-victim-phones/` (sibling lane **L16**, \"{pj.get('title', 'Sandtable Beacon')}\"), made by "
        f"`scripts/vendor_beacon.py` on {stamp}.",
        "",
        (f"Source: commit `{commit}` ({info.get('commit_date', '')}): \"{info.get('commit_subject', '')}\". Copied with `git archive`, so only "
         f"committed files are here" + (f"; {info['uncommitted_in_source']} uncommitted change(s) in the source tree were not copied." if info.get("uncommitted_in_source") else ".")
         if commit else "Source: the sibling's working tree (no git history was available)."),
        "",
        "It is that team's work, credited to them; it is copied (not linked) so this repository stands alone. Do not edit it here: change the "
        "sibling project and re-run `python scripts/vendor_beacon.py`. This file is generated from the copy.",
        "",
        "## What was copied",
        "",
        "| Directory | Files | Bytes |",
        "|---|---|---|",
        *[f"| `{r}/` | {n} | {b:,} |" if r != "(top level)" else f"| top level | {n} | {b:,} |" for r, (n, b) in sorted(by_root.items())],
        f"| **total** | **{len(files)}** | **{sum(len(d) for d in files.values()):,}** |",
        "",
        "Skipped: " + ("; ".join(f"{n} file(s) {why}" for why, n in sorted(reasons.items())) if reasons else "nothing among the committed files of the copied directories")
        + ". Never copied: `data/`, virtualenvs, caches, build outputs, `.env*`, keys and signing material, any `live` directory.",
        "",
        "## How it runs here",
        "",
        "`python -m sandtable.demo` starts it by itself on port 1885 when nothing answers there (and stops it on exit), then points the board at it "
        "(`BEACON_URL`). By hand: `cd vendor/beacon && python -m beacon.demo --port 1885` (simulated world). Its feed contract is "
        "`docs/BEACON_FEED.md`; its own tests run with `cd vendor/beacon && python -m pytest -q tests`. If it is absent or fails to start, the "
        "Sandtable board relays the hand-made sample (`fixtures/beacon/sample_beacons.json`), labelled SAMPLE on screen.",
        "",
        "## Every file",
        "",
        "| File | Bytes |",
        "|---|---|",
        *[f"| `{rel}` | {len(files[rel]):,} |" for rel in sorted(files)],
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update-sample", action="store_true", help="also replace fixtures/beacon/sample_beacons.json (recorded beacon runs must then be re-recorded)")
    args = ap.parse_args(argv)
    if not (SRC / "beacon").is_dir():
        print(f"[vendor_beacon] nothing to vendor: {SRC.name}/beacon does not exist", file=sys.stderr)
        return 1
    files, skipped, info = collect()
    if "beacon/__init__.py" not in files and not any(f.startswith("beacon/") for f in files):
        raise SystemExit("[vendor_beacon] the copy has no beacon/ package: refusing to replace vendor/beacon")
    size = sum(len(d) for d in files.values())
    print(f"[vendor_beacon] {len(files)} files, {size / 1e6:.1f} MB from ../{SRC.name} ({info['method']}"
          + (f" {info['commit'][:12]}" if info.get("commit") else "") + f"); skipped {len(skipped)}")
    pj = {}
    try:
        raw_pj = git("show", "HEAD:project.json") if info.get("commit") else (SRC / "project.json").read_bytes()
        pj = json.loads(raw_pj or b"{}")
    except (OSError, ValueError):
        pass
    req_text = files.get("requirements.txt", b"").decode("utf-8", errors="ignore") or None
    if args.check:
        print("[vendor_beacon] requirements to add:", merge_requirements(req_text, check=True) or "none")
        return 0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp = DST.with_name("beacon.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    for rel, data in files.items():
        out = tmp / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    (tmp / "VENDORED.md").write_text(vendored_md(files, skipped, info, pj, stamp), encoding="utf-8")
    if DST.exists():
        shutil.rmtree(DST)
    tmp.rename(DST)
    oq = pj.get("owner_questions")
    if oq is not None:
        (ROOT / "docs" / "owner_questions_beacon.json").write_text(json.dumps({"source": "lane L16's project.json (the Beacon module, vendored in vendor/beacon/)",
                                                                              "vendored": stamp, "commit": info.get("commit"), "owner_questions": oq},
                                                                             indent=1) + "\n", encoding="utf-8")
    sample_rel = "fixtures/beacon/sample_beacons.json"
    ours = ROOT / sample_rel
    if sample_rel in files and (not ours.is_file() or ours.read_bytes() != files[sample_rel]):
        if args.update_sample:
            ours.parent.mkdir(parents=True, exist_ok=True)
            ours.write_bytes(files[sample_rel])
            print("[vendor_beacon] replaced our fixtures/beacon/sample_beacons.json: re-record the beacon runs (scripts/record_all.sh)")
        else:
            print("[vendor_beacon] their sample_beacons.json differs from ours; ours is kept (recorded runs replay against it). "
                  "Use --update-sample to replace it, then re-record.")
    add = merge_requirements(req_text, check=False)
    print(f"[vendor_beacon] copied into vendor/beacon/; owner questions: {len(oq) if oq else 0}; requirements added: {add or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
