"""Rebuild our SS-TWR firmware source inside a checkout of the public starter project, then copy in our build files.

    python hardware/twr_firmware/prepare.py --starter ../DWM3001C-starter-firmware            # clone if missing
    make -C ../DWM3001C-starter-firmware -f Makefile.twr ROLE=INITIATOR NRF5_SDK=/path/to/nRF5_SDK_17.1.0_ddde560
    make -C ../DWM3001C-starter-firmware -f Makefile.twr ROLE=RESPONDER NRF5_SDK=/path/to/nRF5_SDK_17.1.0_ddde560

Why a script and not the C file: our `app.c` derives from Qorvo/Decawave's DW3000 API examples ex_06a/ex_06b
("Copyright 2015 - 2021 (c) Decawave Ltd ... All rights reserved", no licence that clearly allows redistribution), as
found in the public project https://github.com/Uberi/DWM3001C-starter-firmware (no licence for that code either). So
this repository carries only OUR changes, as an ed-style script (`app.c.ed`, the output of `diff -e`) against the two
example files at a pinned commit; this script applies it and checks every hash. Nothing from the SDKs is copied here.

What it does (standard library only; needs `git` only when it has to clone):
 1. clone the starter project at the pinned commit (or use the checkout you name) and check the two example files' hashes;
 2. base = ss_twr_initiator.c + ss_twr_responder.c (concatenated); apply app.c.ed; check the result's hash;
 3. write Src/app.c, and copy Makefile.twr and dwm3001c.ld (ours) to the checkout's root.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STARTER_URL = "https://github.com/Uberi/DWM3001C-starter-firmware"
STARTER_COMMIT = "02c341b4f58dd8410239b5278b40aece675a3650"
UPSTREAM = [   # (path in the starter project, sha256 at STARTER_COMMIT)
    ("Src/examples/ex_06a_ss_twr_initiator/ss_twr_initiator.c", "6f42098a85d4442486faa4e1d1e902a24417bf5caf6c8569a74d017164ea98f3"),
    ("Src/examples/ex_06b_ss_twr_responder/ss_twr_responder.c", "85d94db594d5c72ae230f07df037b8f7cf78378ceadb31b51bb998ac62385397"),
]
APP_C_SHA256 = "e2e205e21f046bbdba2c4921bd95fff902857934c4459a947eebf2187b5be94a"
OURS = ["Makefile.twr", "dwm3001c.ld"]
_CMD = re.compile(r"^(\d+)(?:,(\d+))?([acd])$")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply_ed(base: list[bytes], script: list[bytes]) -> list[bytes]:
    """Apply a `diff -e` script (commands `N[,M]a|c|d`, text ended by a lone '.', bottom-up order) to a list of lines.
    Bytes throughout: the upstream files are not all UTF-8."""
    out = list(base)
    i = 0
    while i < len(script):
        m = _CMD.match(script[i].rstrip(b"\n").decode("ascii", "replace"))
        if not m:
            raise ValueError(f"line {i + 1} of the ed script is not a command: {script[i]!r}")
        a, b, op = int(m.group(1)), int(m.group(2) or m.group(1)), m.group(3)
        i += 1
        text: list[bytes] = []
        if op in "ac":
            while i < len(script) and script[i].rstrip(b"\n") != b".":
                text.append(script[i])
                i += 1
            if i == len(script):
                raise ValueError("the ed script ends inside a text block")
            i += 1
        if op == "a":
            out[a:a] = text
        elif op == "c":
            out[a - 1:b] = text
        else:
            del out[a - 1:b]
    return out


def build_app_c(starter: Path) -> bytes:
    parts = []
    for rel, want in UPSTREAM:
        data = (starter / rel).read_bytes()
        if sha256(data) != want:
            raise SystemExit(f"{rel} is not the file this script expects (sha256 differs): check out {STARTER_COMMIT}")
        parts.append(data)
    base = b"".join(parts).splitlines(keepends=True)
    script = (HERE / "app.c.ed").read_bytes().splitlines(keepends=True)
    app = b"".join(apply_ed(base, script))
    if sha256(app) != APP_C_SHA256:
        raise SystemExit("the rebuilt app.c does not match the expected hash: do not build it")
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--starter", required=True, help="checkout of DWM3001C-starter-firmware (cloned here if missing)")
    ap.add_argument("--print-only", action="store_true", help="write app.c to stdout; change nothing")
    args = ap.parse_args(argv)
    starter = Path(args.starter).resolve()
    if not starter.exists():
        print(f"cloning {STARTER_URL} into {starter}", file=sys.stderr)
        subprocess.run(["git", "clone", "-q", STARTER_URL, str(starter)], check=True)
        subprocess.run(["git", "-C", str(starter), "checkout", "-q", STARTER_COMMIT], check=True)
    app = build_app_c(starter)
    if args.print_only:
        sys.stdout.write(app.decode("utf-8"))
        return 0
    (starter / "Src" / "app.c").write_bytes(app)
    for name in OURS:
        shutil.copyfile(HERE / name, starter / name)
    print(f"Src/app.c rebuilt and checked (sha256 {APP_C_SHA256[:12]}...); copied {', '.join(OURS)}.\n"
          f"Next: make -C {starter} -f Makefile.twr ROLE=INITIATOR NRF5_SDK=<path to nRF5_SDK_17.1.0_ddde560>\n"
          f"      make -C {starter} -f Makefile.twr ROLE=RESPONDER NRF5_SDK=<same>", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
