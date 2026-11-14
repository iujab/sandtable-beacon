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


# Switches decide WHAT a process does (which data, which model backend). They come from the command's environment or from the
# project's own .env, never from the bench drop zone: that directory holds credentials and ids for several machines (one of its
# files is the .env prepared for the laptop), and a switch found there must not silently turn every process on this machine live.
SWITCHES = frozenset({"SANDTABLE_DATA_MODE", "COSTAR_MODE", "MATTERPORT_MODE", "VEXCEL_MODE", "LLM_BACKEND"})


def _load_env_files(project_env: Path | None = None, drop_zone: Path | None = None, environ=None) -> None:
    """Load KEY=VALUE files into os.environ without overriding what is already set.

    Looked up in this order: <project>/.env, then every *.env in a `.secrets/`
    directory beside the project (the bench drop zone), from which the SWITCHES
    and empty values are not taken. Values are never logged.
    """
    environ = os.environ if environ is None else environ
    candidates: list[tuple[Path, bool]] = [(project_env or ROOT / ".env", True)]
    secrets = drop_zone or ROOT.parent / ".secrets"
    if secrets.is_dir():
        candidates += [(p, False) for p in sorted(secrets.glob("*.env"))]
    for path, own in candidates:
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
                if not own and (key in SWITCHES or not value):
                    continue
                if key and key not in environ:
                    environ[key] = value
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

# Names of the environment variables the live adapters read (values never logged): see .env.example.
#   COSTAR_KIT_DIR, COSTAR_PROPERTY_ID, COSTAR_PROPERTY_ADDRESS, COSTAR_KIT_ZIP_PASSWORD
#   MATTERPORT_TOKEN_ID, MATTERPORT_TOKEN_SECRET, MATTERPORT_MODEL_ID, MATTERPORT_SDK_KEY
#   VEXCEL_API_KEY
#   ANTHROPIC_API_KEY

# --- privacy guard: sponsor data never leaves data/live/ ------------------------------------
# The owner's rule is "don't push the data at all". Every byte of sponsor data and everything derived
# from it (caches, normalised bundles, annotated images, screenshots, model recordings made against live
# data, results) lives under LIVE_DIR: a directory named `live`, inside the git-ignored data directory,
# which the publisher also skips. As soon as any layer is live in this process (from the environment at
# import, or when the adapters build a provider with a live layer), the process switches for good:
#   * DATA_DIR, TAGS_OVERLAY_JSON and CORRECTIONS_JSON point under LIVE_DIR, so every runtime writer that
#     goes through config (tag overlay, record corrections, annotated aerials, Vexcel downloads) is private;
#   * the model replay cache becomes LIVE_DIR/llm_cache; fixtures/llm_cache/ is read-only (guard_write);
#   * guard_write(path) refuses any write outside a `live` directory.
# Fixture mode is untouched: none of this changes a path or a default while every layer is `fixture`.
DATA_ROOT = DATA_DIR                          # the git-ignored root, whatever the mode
LIVE_DIR = DATA_ROOT / "live"                 # the private root
_LIVE_LAYERS: set[str] = set()                # layers that went live in this process (never cleared outside tests)


class PrivacyError(PermissionError):
    """A write that would put sponsor-derived bytes outside the private root."""


def live_active() -> bool:
    """True once any sponsor layer is live in this process."""
    return bool(_LIVE_LAYERS)


def live_layers() -> list[str]:
    return sorted(_LIVE_LAYERS)


# Several boards may run from one checkout at once (the standing demo, a private live instance, a recording run, a builder's
# test board). The files a board writes at run time (tags written back, corrections filed, the dry-run log of pins) feed the
# agent's prompts, so they must not be shared: one board's incident, or its reset, would change another board's prompts and a
# recorded run would stop replaying. The standard port keeps the plain file names; any other port gets its own.
INSTANCE = ""


def set_instance(port) -> None:
    """Name this process's runtime files after its board port (called by the launcher and the server at start)."""
    global INSTANCE, TAGS_OVERLAY_JSON, CORRECTIONS_JSON
    port = str(port or "")
    INSTANCE = "" if port in ("", "1810") else f".{port}"
    TAGS_OVERLAY_JSON = DATA_DIR / f"twin_tags_overlay{INSTANCE}.json"
    CORRECTIONS_JSON = DATA_DIR / f"record_corrections{INSTANCE}.json"


def enter_live(layers) -> None:
    """Switch this process's runtime outputs to the private root (idempotent)."""
    global DATA_DIR, TAGS_OVERLAY_JSON, CORRECTIONS_JSON
    _LIVE_LAYERS.update(layers)
    if _LIVE_LAYERS:
        DATA_DIR = LIVE_DIR
        TAGS_OVERLAY_JSON = LIVE_DIR / f"twin_tags_overlay{INSTANCE}.json"
        CORRECTIONS_JSON = LIVE_DIR / f"record_corrections{INSTANCE}.json"


def privacy_state() -> tuple:
    """Snapshot for undoing a failed switch (adapters.build_provider) and for tests."""
    return (set(_LIVE_LAYERS), DATA_DIR, TAGS_OVERLAY_JSON, CORRECTIONS_JSON)


def restore_privacy_state(state: tuple) -> None:
    global DATA_DIR, TAGS_OVERLAY_JSON, CORRECTIONS_JSON
    layers, DATA_DIR, TAGS_OVERLAY_JSON, CORRECTIONS_JSON = state
    _LIVE_LAYERS.clear()
    _LIVE_LAYERS.update(layers)


def is_private(path) -> bool:
    """True when the path lies under a directory named `live` (judged inside the data root or the project)."""
    p = Path(path).resolve()
    for base in (Path(LIVE_DIR).resolve().parent, ROOT):
        try:
            return "live" in p.relative_to(base).parts[:-1]
        except ValueError:
            continue
    return "live" in p.parts[:-1]


def guard_write(path) -> Path:
    """Call before writing a file at runtime. In live mode, refuses any path outside a `live` directory."""
    if live_active() and not is_private(path):
        raise PrivacyError(f"live mode ({', '.join(live_layers())}): refusing to write {path} outside a 'live' directory "
                           f"(sponsor-derived output goes under {LIVE_DIR})")
    return Path(path)


def private_path(*parts: str) -> Path:
    """A path under the private root, with its parent directory created."""
    p = LIVE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def llm_cache_dir() -> Path:
    """The model replay cache: fixtures/llm_cache/ in fixture mode, LIVE_DIR/llm_cache once any layer is live."""
    return LIVE_DIR / "llm_cache" if live_active() else LLM_CACHE_DIR


def output_dir(kind: str) -> Path:
    """Where a results or screenshot writer puts its files: <project>/<kind> in fixture mode, LIVE_DIR/<kind> in live mode."""
    return LIVE_DIR / kind if live_active() else ROOT / kind


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "building"


def demo_building() -> dict | None:
    """The live demo building chosen for the sponsor layers (LIVE_DIR/demo_building.json), or None."""
    import json
    path = LIVE_DIR / "demo_building.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def site_context_json() -> Path:
    """The public-context bundle (sandtable.site/1) the record is merged with.

    SANDTABLE_SITE (a site.json or its folder) wins. Otherwise, in live mode, the open-data context built by
    scripts/build_site.py for the demo building (LIVE_DIR/site/<slug>/site.json) when it exists; else the fixture.
    """
    explicit = env("SANDTABLE_SITE")
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else ROOT / p
        return p / "site.json" if p.is_dir() else p
    if live_active():
        b = demo_building()
        if b and b.get("address"):
            cand = LIVE_DIR / "site" / _slug(b["address"]) / "site.json"
            if cand.is_file():
                return cand
    return SITE_JSON


if any(m == "live" for m in (COSTAR_MODE, MATTERPORT_MODE, VEXCEL_MODE)):
    enter_live([name for name, m in (("costar", COSTAR_MODE), ("matterport", MATTERPORT_MODE), ("vexcel", VEXCEL_MODE)) if m == "live"])

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
