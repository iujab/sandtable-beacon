#!/usr/bin/env bash
# Refuses to call the tree publishable when a publishable file (tracked, or untracked and not ignored) contains:
#   1. a build-machine path, the local user name, the host name, a tailnet-range host address (a range base such as
#      100.64.0.0/10 is not an address) or something that looks like a key;
#   2. a key VALUE from the env files (.env, the bench .secrets/*.env): values are read at check time and never printed;
#   3. a term of the private building denylist (data/live/denylist_building.txt: one per line, case-insensitive, whole words) or a
#      Matterport model id from the CoStar kit's commercial-properties.csv (COSTAR_KIT_DIR or the bench drop zone);
# or when a publishable path is under a directory named `live` (sponsor data), or is imagery over 200 KB outside
# fixtures/aerial/, figures/ and vendor/beacon/figures/. Prints file names and the reason only. Exit code 1 if anything is found. Run before committing.
cd "$(dirname "$0")/.."
U=$(id -un); H=$(hostname)
OFF=$(mktemp); LIST=$(mktemp); trap 'rm -f "$OFF" "$LIST"' EXIT
{ git ls-files; git ls-files --others --exclude-standard; } | sort -u > "$LIST"
tr '\n' '\0' < "$LIST" |
  xargs -0 grep -I -l -E "$U|/home/[a-z]|/Users/[A-Za-z]|$H|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[1-9][0-9]*|sk-ant-|API_KEY=[A-Za-z0-9_-]{12,}" 2>/dev/null |
  grep -v "^scripts/check_publishable.sh$" | sed 's/$/  (machine path, user or host name, tailnet address or key pattern)/' >> "$OFF" || true
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python.exe; [ -x "$PY" ] || PY=python3
"$PY" - "$LIST" >> "$OFF" <<'PYEOF'
import csv, os, re, sys
from pathlib import Path
root = Path.cwd()
files = [l for l in Path(sys.argv[1]).read_text().splitlines() if l and Path(l).is_file()]
IMG = re.compile(r"\.(png|jpe?g|tiff?|webp|gif|bmp|jp2|heic|avif)$", re.I)
MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"RIFF", b"GIF8")
for f in files:
    parts = Path(f).parts
    if "live" in parts[:-1]:
        print(f"{f}  (under a 'live' directory: sponsor data must never be committed or published)")
    if os.path.getsize(f) > 200_000 and not f.startswith(("fixtures/aerial/", "figures/", "vendor/beacon/figures/")):   # the last: lane L16's committed fixture screenshots
        with open(f, "rb") as fh:
            head = fh.read(12)
        if IMG.search(f) or head.startswith(MAGIC):
            print(f"{f}  (imagery over 200 KB outside fixtures/aerial/, figures/ and vendor/beacon/figures/)")
# secrets: every value in the env files (the values stay in memory; only file names are printed)
env_files = [root / ".env"] + sorted((root.parent / ".secrets").glob("*.env"))
values = set()
for ef in env_files:
    if ef.is_file():
        for line in ef.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD|PROPERTY_ID|MODEL_ID", k.upper()) and len(v) >= 6:
                    values.add(v)
deny = set()
dl = root / "data" / "live" / "denylist_building.txt"
if dl.is_file():
    deny = {i: (l.strip().lower(), re.compile(r"(?<![a-z0-9])" + re.escape(l.strip().lower()) + r"(?![a-z0-9])"))
            for i, l in enumerate(dl.read_text(encoding="utf-8", errors="ignore").splitlines(), 1) if len(l.strip()) >= 4 and not l.startswith("#")}
models = set()
kit = Path(os.environ.get("COSTAR_KIT_DIR") or root.parent / ".secrets" / "costar-kit")
for csvf in (kit / "commercial-properties.csv", kit / "residential-listings.csv"):
    if csvf.is_file():
        with open(csvf, encoding="utf-8-sig", errors="ignore", newline="") as fh:
            models |= {(r.get("MatterportModelId") or "").strip() for r in csv.DictReader(fh)} - {""}
TOKEN = re.compile(r"[A-Za-z0-9]+")
for f in files:
    if f == "scripts/check_publishable.sh" or os.path.getsize(f) > 30_000_000:
        continue
    text = Path(f).read_bytes().decode("utf-8", errors="ignore")
    if any(v in text for v in values):
        print(f"{f}  (contains a key value from an env file)")
    if deny:
        low = text.lower()
        hits = [i for i, (term, rx) in deny.items() if term in low and rx.search(low)]
        if hits:
            print(f"{f}  (contains data/live/denylist_building.txt line(s) {', '.join(map(str, hits[:5]))}; the term is not printed)")
    if models and not models.isdisjoint(TOKEN.findall(text)):
        print(f"{f}  (contains a Matterport model id from the CoStar kit)")
print(f"#checked {len(files)} files, {len(values)} key values, {len(deny)} denylist terms, {len(models)} kit model ids", file=sys.stderr)
PYEOF
if [ -s "$OFF" ]; then echo "NOT publishable:"; sort -u "$OFF"; exit 1; fi
echo "publishable: no machine paths, user names, tailnet addresses, keys or key values, live paths, large imagery, building terms or kit model ids found"
