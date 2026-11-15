"""Beacon puck host: a small computer next to a puck (the Arduino UNO Q, a laptop) that tells the Beacon server the
puck is hosted and, experimentally, forwards what the puck itself prints.

    python -m beacon.host --server http://127.0.0.1:1885 --puck P1 --name uno-q
    python -m beacon.host --server http://127.0.0.1:1885 --puck P1 --baseline-to P2 --rtt 127.0.0.1:19021
    python -m beacon.host --help

The second form reads OUR board-to-board SS-TWR firmware (hardware/twr_firmware/, beacon/host/twr.py) on the initiator
board and posts the measured puck spacing to POST /api/pucks/baseline. Written and tested against sample lines in the
firmware's real format; not yet run against the boards with this host.

Pure Python 3.10+ standard library; optional `pyserial` (for `--serial`) and optional `bleak` (for `--cold-scan`).
Runs on the UNO Q's Debian aarch64, Windows, macOS and Linux. Written from documentation and forum reports; never
connected to a real board, a real J-Link or a real radio. Sources: docs/research/puck_hardware.md, hardware/UNO_Q.md.
"""
__version__ = "0.1"
