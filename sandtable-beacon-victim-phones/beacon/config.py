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
FIXTURE_TWIN_JSON = BUILDING_DIR / "twin.json"
FIXTURE_SITE_JSON = BUILDING_DIR / "site.json"

# --- the building bundle ------------------------------------------------------
# The twin and the building record default to this repository's labelled fixtures. A launcher that holds other data
# (a real twin and a real record, kept OUTSIDE this repository) points the module at them through the environment:
#   BEACON_TWIN_JSON=/path/twin.json  BEACON_SITE_JSON=/path/site.json      (either or both), or
#   BEACON_BUNDLE_DIR=/path/dir                                              (a directory holding twin.json and site.json)
# Same shapes as the fixtures (sandtable.twin/1, sandtable.site/1). Nothing read from an external bundle is ever written
# into this tree: with an external bundle every file the server writes goes under <data dir>/live (see DATA_DIR), and
# recorded model output goes there too (LLM_CACHE_DIR), never into fixtures/.
_BUNDLE_DIR = os.environ.get("BEACON_BUNDLE_DIR") or ""
TWIN_JSON = Path(os.environ.get("BEACON_TWIN_JSON") or (Path(_BUNDLE_DIR) / "twin.json" if _BUNDLE_DIR else FIXTURE_TWIN_JSON))
SITE_JSON = Path(os.environ.get("BEACON_SITE_JSON") or (Path(_BUNDLE_DIR) / "site.json" if _BUNDLE_DIR else FIXTURE_SITE_JSON))


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


EXTERNAL_BUNDLE = not (_same(TWIN_JSON, FIXTURE_TWIN_JSON) and _same(SITE_JSON, FIXTURE_SITE_JSON))

SAMPLE_JSON = BEACON_FIXTURES / "sample_beacons.json"
INGEST_EXAMPLE_JSON = BEACON_FIXTURES / "ingest_example.json"
WEB_DIR = ROOT / "web"
DIST_DIR = ROOT / "dist"
# git-ignored runtime state (live puck table, record checks, logs). With an external bundle it is a directory named
# "live" (under the data directory unless BEACON_DATA_DIR already names one), so that nothing derived from that data can
# be published by accident.
_DATA_ROOT = Path(os.environ.get("BEACON_DATA_DIR") or (ROOT / "data"))
DATA_DIR = (_DATA_ROOT if _DATA_ROOT.name == "live" or "live" in _DATA_ROOT.parts else _DATA_ROOT / "live") if EXTERNAL_BUNDLE else _DATA_ROOT
PUCKS_LIVE_JSON = DATA_DIR / "pucks_live.json"
# Recorded model output: the committed fixtures for the fixture bundle; a private directory for an external bundle
# (prompts built from a real record contain tenant names and must never land in fixtures/).
LLM_CACHE_DIR = Path(os.environ.get("BEACON_LLM_CACHE_DIR") or ((DATA_DIR / "llm_cache") if EXTERNAL_BUNDLE else (FIXTURES / "llm_cache")))


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
# "auto" builds a scene procedurally for whatever twin is loaded (beacon/autoscene.py); the named fixture scenes only fit
# the fixture twin, so an external bundle defaults to "auto".
DEFAULT_SCENARIO = env("BEACON_SCENARIO", "auto" if EXTERNAL_BUNDLE else "suite-310")
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


def default_room(twin) -> str | None:
    """The room pucks go into when a request names none: BEACON_ROOM when the loaded twin has it, otherwise the largest
    room of the exercise floor that has a door (suites first). `twin` is a beacon.twin.Twin."""
    ids = {r["id"] for r in twin.data.get("rooms", [])}
    if DEFAULT_ROOM in ids:
        return DEFAULT_ROOM
    floor = twin.exercise_floor
    doored = {rid for d in twin.data.get("doors", []) for rid in (d.get("rooms") or [])}
    rooms = [r for r in twin.data.get("rooms", []) if r.get("floor") == floor and r["id"] in doored] \
        or [r for r in twin.data.get("rooms", []) if r.get("floor") == floor] or list(twin.data.get("rooms", []))
    if not rooms:
        return None
    rooms.sort(key=lambda r: (0 if r.get("kind") == "suite" else 1, -float(r.get("area_m2") or 0.0), r["id"]))
    return rooms[0]["id"]


_FIXTURE_TEXT = {
    "costar": "Building record: public open data with named sources; CoStar-only fields are empty. Tenant names withheld. Not CoStar data.",
    "matterport": "Twin: synthetic interior fitted to the real building footprint. Not a Matterport scan.",
}
_LIVE_TEXT = {"costar": "Building record: CoStar (live).", "matterport": "Twin: Matterport Model API (live)."}
_EXTERNAL_TEXT = {"costar": "Building record: supplied by the launcher (BEACON_SITE_JSON); its provenance is not stated here.",
                  "matterport": "Twin: supplied by the launcher (BEACON_TWIN_JSON); its provenance is not stated here."}


def labels(site: dict | None = None, twin: dict | None = None) -> dict:
    """The honesty labels (same texts as Sandtable's provider labels, sandtable/adapters/__init__.py).

    Fixture bundle: the fixture texts. External bundle: the launcher states each layer's mode with BEACON_COSTAR_MODE /
    BEACON_MATTERPORT_MODE = live | fixture (Sandtable's COSTAR_MODE / MATTERPORT_MODE are honoured too); when it says
    nothing, the label says the provenance is not stated, rather than claiming either."""
    site = site if site is not None else site_raw()
    twin = twin if twin is not None else twin_raw()
    out: dict = {}
    for layer, external_key in (("costar", "BEACON_COSTAR_MODE"), ("matterport", "BEACON_MATTERPORT_MODE")):
        if not EXTERNAL_BUNDLE:
            out[layer] = {"mode": "fixture", "real_sponsor_data": False, "text": _FIXTURE_TEXT[layer]}
            continue
        mode = (os.environ.get(external_key) or os.environ.get(layer.upper() + "_MODE") or "").strip().lower()
        if mode == "live":
            out[layer] = {"mode": "live", "real_sponsor_data": True, "text": _LIVE_TEXT[layer]}
        elif mode == "fixture":
            out[layer] = {"mode": "fixture", "real_sponsor_data": False, "text": _FIXTURE_TEXT[layer]}
        else:
            out[layer] = {"mode": "external", "real_sponsor_data": False, "text": _EXTERNAL_TEXT[layer]}
    out["open_data"] = site.get("open_data", [])
    out["twin_synthetic"] = bool((twin.get("source") or {}).get("synthetic"))
    out["external_bundle"] = EXTERNAL_BUNDLE
    return out
