"""Paths, ports, sources and thresholds for Beacon. Every module reads them from here.

Pattern copied from Sandtable's `sandtable/config.py` (sibling project, lane L01), with credit.
Nothing in this file prints or returns a secret value.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
BUILDING_DIR = FIXTURES / "building"
BEACON_FIXTURES = FIXTURES / "beacon"
SCENARIOS_DIR = BEACON_FIXTURES / "scenarios"
TWIN_JSON = BUILDING_DIR / "twin.json"
SITE_JSON = BUILDING_DIR / "site.json"
SAMPLE_JSON = BEACON_FIXTURES / "sample_beacons.json"
INGEST_EXAMPLE_JSON = BEACON_FIXTURES / "ingest_example.json"
WEB_DIR = ROOT / "web"
DIST_DIR = ROOT / "dist"
DATA_DIR = Path(os.environ.get("BEACON_DATA_DIR") or (ROOT / "data"))      # git-ignored: runtime state (live puck table, logs)
PUCKS_LIVE_JSON = DATA_DIR / "pucks_live.json"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- network -----------------------------------------------------------------
HOST = env("HOST", "127.0.0.1")
PORT = int(env("PORT", "1885"))
HUB_PORT = int(env("HUB_PORT", str(PORT + 100)))
UWBKIT_URL = env("UWBKIT_URL", f"ws://127.0.0.1:{HUB_PORT}/stream")

# --- source ------------------------------------------------------------------
# sim  = the simulated world (beacon.sim) feeding the real locator (default)
# fake = the hand-made sample (fixtures/beacon/sample_beacons.json) replayed in phases, marked SAMPLE
# live = real phones (the iOS app) posting observations to POST /api/ingest; nothing simulated
# kit  = real pucks through the uwb-kit hub (beacon.source_kit, when it exists)
SOURCES = ("sim", "fake", "live", "kit")
BEACON_SOURCE = env("BEACON_SOURCE", "sim")

# --- default scene -----------------------------------------------------------
DEFAULT_ROOM = env("BEACON_ROOM", "r310")         # the room the pucks are thrown into when a request names none
DEFAULT_SCENARIO = env("BEACON_SCENARIO", "suite-310")
SIM_SEED = int(env("BEACON_SEED", "310"))

# --- timing and thresholds ----------------------------------------------------------
STALE_S = env_float("BEACON_STALE_S", 20.0)        # a phone not heard for this long is "stale"
RETENTION_S = env_float("BEACON_RETENTION_S", 900.0)   # ... and dropped (with everything known about it) after this long
STEP_HZ = env_float("BEACON_STEP_HZ", 4.0)         # locator + tracker + Beacons object rate (also the stream's maximum rate)
SIM_HZ = env_float("BEACON_SIM_HZ", 10.0)          # simulated world step rate
COLD_WINDOW_S = env_float("BEACON_COLD_WINDOW_S", 30.0)   # cold scans older than this are not used for presence
STALE_SIGMA_GROWTH_MPS = env_float("BEACON_STALE_SIGMA_GROWTH_MPS", 0.25)   # sigma growth of a stale phone's geometry, m per s
ACK_LINK_S = 5.0                                   # a puck "hears" a phone when it had a link within this many seconds
RIG_SCALE = env_float("BEACON_RIG_SCALE", 1.0)     # building metres per metre of the physical demo area (live mode)


@lru_cache(maxsize=1)
def twin_raw() -> dict:
    return json.loads(TWIN_JSON.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def site_raw() -> dict:
    return json.loads(SITE_JSON.read_text(encoding="utf-8"))


def labels(site: dict | None = None, twin: dict | None = None) -> dict:
    """The fixture honesty labels (same texts as Sandtable's provider labels, sandtable/adapters/__init__.py)."""
    site = site if site is not None else site_raw()
    twin = twin if twin is not None else twin_raw()
    return {
        "costar": {"mode": "fixture", "real_sponsor_data": False,
                   "text": "Building record: public open data with named sources; CoStar-only fields are empty. Tenant names withheld. Not CoStar data."},
        "matterport": {"mode": "fixture", "real_sponsor_data": False,
                       "text": "Twin: synthetic interior fitted to the real building footprint. Not a Matterport scan."},
        "open_data": site.get("open_data", []),
        "twin_synthetic": bool((twin.get("source") or {}).get("synthetic")),
    }
