"""Shared configuration for Sandtable (lead-owned, frozen for round 1).

Every module reads paths, ports, modes and thresholds from here. Nothing in this
file prints or returns a secret value.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
BUILDING_DIR = FIXTURES / "building"          # site.json, twin.json
AERIAL_DIR = FIXTURES / "aerial"              # aerial.json + image files
LLM_CACHE_DIR = FIXTURES / "llm_cache"        # replay cache, one JSON file per prompt hash
DATA_DIR = Path(os.environ.get("SANDTABLE_DATA_DIR") or (ROOT / "data"))   # git-ignored: downloads, overlays, runtime output
RESULTS_DIR = ROOT / "results"
WEB_DIR = ROOT / "web"
HARDWARE_DIR = ROOT / "hardware"

SITE_JSON = BUILDING_DIR / "site.json"
TWIN_JSON = BUILDING_DIR / "twin.json"
AERIAL_JSON = AERIAL_DIR / "aerial.json"
TAGS_OVERLAY_JSON = DATA_DIR / "twin_tags_overlay.json"      # tags written back in fixture mode
CORRECTIONS_JSON = DATA_DIR / "record_corrections.json"      # record corrections filed in fixture mode


def _load_env_files() -> None:
    """Load KEY=VALUE files into os.environ without overriding what is already set.

    Looked up in this order: <project>/.env, then every *.env in a `.secrets/`
    directory beside the project (the bench drop zone). Values are never logged.
    """
    candidates: list[Path] = [ROOT / ".env"]
    secrets = ROOT.parent / ".secrets"
    if secrets.is_dir():
        candidates += sorted(secrets.glob("*.env"))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip().removeprefix("export ").strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_env_files()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- network -----------------------------------------------------------------
HOST = env("HOST", "127.0.0.1")
PORT = int(env("PORT", "1810"))
HUB_PORT = int(env("HUB_PORT", str(PORT + 100)))           # this project's own uwb-kit hub (1910)
UWBKIT_URL = env("UWBKIT_URL", f"ws://127.0.0.1:{HUB_PORT}/stream")


def hub_http_base() -> str:
    """http://host:port of the hub, derived from UWBKIT_URL."""
    url = UWBKIT_URL.replace("ws://", "http://").replace("wss://", "https://")
    return url.rsplit("/stream", 1)[0]


# --- sponsor data modes --------------------------------------------------------
# "fixture" (default) or "live". Per-layer overrides win over SANDTABLE_DATA_MODE.
DATA_MODE = env("SANDTABLE_DATA_MODE", "fixture")
COSTAR_MODE = env("COSTAR_MODE", DATA_MODE)
MATTERPORT_MODE = env("MATTERPORT_MODE", DATA_MODE)
VEXCEL_MODE = env("VEXCEL_MODE", DATA_MODE)

# Names of the environment variables the live adapters read (values never logged):
#   COSTAR_API_KEY, COSTAR_BASE_URL
#   MATTERPORT_TOKEN_ID, MATTERPORT_TOKEN_SECRET, MATTERPORT_MODEL_ID, MATTERPORT_SDK_KEY
#   VEXCEL_API_KEY
#   ANTHROPIC_API_KEY

# --- LLM -----------------------------------------------------------------------
LLM_BACKEND = env("LLM_BACKEND", "replay")                 # replay | claude-cli | anthropic
LLM_RECORD = env("LLM_RECORD", "0") == "1"                 # write real responses into the replay cache
LLM_MODEL_FAST = env("LLM_MODEL_FAST", "claude-haiku-5-5")
LLM_MODEL_MAIN = env("LLM_MODEL_MAIN", "claude-sonnet-5-5")
LLM_MODEL_HARD = env("LLM_MODEL_HARD", "claude-opus-5-5")
AGENT_MAX_STEPS = int(env("SANDTABLE_AGENT_MAX_STEPS", "10"))
REPLAY_PACE_S = env_float("SANDTABLE_REPLAY_PACE_S", 0.7)            # pause between replayed agent steps so people can read them
AGENT_FALLBACK_AFTER_S = env_float("SANDTABLE_AGENT_FALLBACK_AFTER_S", 20.0)  # reflex policy takes over if the LLM has not acted

# --- reflex thresholds (building frame, metres and seconds) -----------------------
# Real PASS devices pre-alert after about 20 s without motion and alarm at about 30 s.
# Exercise mode compresses this so a two-minute demo can show it; the UI states the value.
NO_MOTION_S = env_float("SANDTABLE_NO_MOTION_S", 8.0)
NO_MOTION_RADIUS_M = env_float("SANDTABLE_NO_MOTION_RADIUS_M", 2.0)   # stays inside this radius => "not moving"
OFF_ROUTE_M = env_float("SANDTABLE_OFF_ROUTE_M", 6.0)
CONTACT_M = env_float("SANDTABLE_CONTACT_M", 3.0)
WALK_SPEED_MPS = env_float("SANDTABLE_WALK_SPEED_MPS", 3.0)          # auto-followers (sim tags, virtual units), building frame; exercise pace
AIR_WORK_TIME_S = env_float("SANDTABLE_AIR_WORK_TIME_S", 1080.0)      # 18 min working time on a 30-min cylinder
STATE_HZ = env_float("SANDTABLE_STATE_HZ", 8.0)

# Exercise scale: metres of building per metre of rig. 1.0 means live, 1:1.
# When unset, the default registration fits the anchor rectangle to the floor outline.
EXERCISE_SCALE = env("SANDTABLE_SCALE", "")
