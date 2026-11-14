"""Beacon puck host: a small computer next to a puck (the Arduino UNO Q, a laptop) that tells the Beacon server the
puck is hosted and, experimentally, forwards what the puck itself prints.

    python -m beacon.host --server http://127.0.0.1:1885 --puck P1 --name uno-q
    python -m beacon.host --help

Pure Python 3.10+ standard library; optional `pyserial` (for `--serial`) and optional `bleak` (for `--cold-scan`).
Runs on the UNO Q's Debian aarch64, Windows, macOS and Linux. Written from documentation and forum reports; never
connected to a real board, a real J-Link or a real radio. Sources: docs/research/puck_hardware.md, hardware/UNO_Q.md.
"""
__version__ = "0.1"
