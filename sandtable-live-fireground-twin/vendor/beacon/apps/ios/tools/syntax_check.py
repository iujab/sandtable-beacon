"""Syntax-only check of the iOS app's Swift sources with tree-sitter.

THIS IS A SYNTAX PARSE, NOT A COMPILE. It finds broken brackets, stray tokens and
malformed statements. It does NOT check types, API names, method signatures,
optionals, availability or actor isolation: only the Swift compiler in Xcode can.

Run from anywhere, in a throwaway environment (do not add these packages to the
project's requirements):

    uv run --with tree-sitter --with tree-sitter-swift python apps/ios/tools/syntax_check.py

Exit code 0 when no file has an ERROR or MISSING node, 1 otherwise. The output is
also what apps/ios/tools/syntax_check.log contains.
"""

from __future__ import annotations

import sys
from pathlib import Path

import tree_sitter
import tree_sitter_swift

HERE = Path(__file__).resolve().parent
SOURCES = HERE.parent / "SandtableBeacon"


def _version(dist: str) -> str:
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:  # noqa: BLE001 - informational only
        return "?"


def make_parser() -> tree_sitter.Parser:
    language = tree_sitter.Language(tree_sitter_swift.language())
    try:
        return tree_sitter.Parser(language)
    except TypeError:  # older py-tree-sitter API
        parser = tree_sitter.Parser()
        parser.set_language(language)
        return parser


def problems(node: tree_sitter.Node, source: bytes) -> list[str]:
    found: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "ERROR" or n.is_missing:
            row, col = n.start_point
            snippet = source[n.start_byte:min(n.end_byte, n.start_byte + 70)]
            text = snippet.decode("utf-8", "replace").replace("\n", "\\n")
            kind = "MISSING " + n.type if n.is_missing else "ERROR"
            found.append(f"  line {row + 1}, col {col + 1}: {kind}: {text}")
            if n.type == "ERROR":
                continue  # do not report every child of one broken region
        stack.extend(reversed(n.children))
    return found


def main() -> int:
    parser = make_parser()
    files = sorted(SOURCES.glob("*.swift"))
    if not files:
        print(f"no Swift files under {SOURCES}")
        return 1
    print("Swift syntax check with tree-sitter (a syntax parse, NOT a compile:")
    print("types, API names and signatures are unchecked).")
    print(f"tree-sitter {_version('tree-sitter')}, tree-sitter-swift {_version('tree-sitter-swift')}")
    total = 0
    for path in files:
        source = path.read_bytes()
        tree = parser.parse(source)
        found = problems(tree.root_node, source)
        lines = source.count(b"\n")
        status = "OK" if not found else f"{len(found)} problem(s)"
        print(f"{path.name:28s} {lines:5d} lines  {status}")
        for line in found:
            print(line)
        total += len(found)
    print(f"{len(files)} files, {total} syntax problem(s)")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
