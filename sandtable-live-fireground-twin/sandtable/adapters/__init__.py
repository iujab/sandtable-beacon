"""Typed adapters for the three sponsor layers, each with a `fixture` and a `live` mode.

    from sandtable.adapters import get_provider
    p = get_provider()
    p.site()      # CoStar-shaped building record + public context   (sandtable.site/1)
    p.twin_raw()  # Matterport-shaped twin in the building frame      (sandtable.twin/1)
    p.aerial()    # Vexcel-shaped aerial image index                  (sandtable.aerial/1)
    p.costar / p.matterport / p.vexcel   # the per-layer adapters with their tool-level methods

Fixture mode reads the bundle under fixtures/ (real open data + a synthetic interior, all labelled).
Live mode reads the sponsor services (Matterport, Vexcel) with keys from the environment and the CoStar
Data Extract from a local folder (see docs/TOOLKIT_SWAP.md).
A layer set to `live` with a missing key raises at start-up: it never silently falls back.

Privacy: building a provider with any live layer switches the whole process to the private root
(config.enter_live): local writes, caches and model recordings then go under data/live/ only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config

__all__ = ["get_provider", "Provider", "AdapterError"]


class AdapterError(RuntimeError):
    pass


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _append(path: Path, item: dict, id_prefix: str) -> None:
    """Append with a deterministic id (prefix + running number), so recorded agent runs replay exactly."""
    config.guard_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    item["id"] = f"{id_prefix}-{len(items) + 1}"
    items.append(item)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
    tmp.replace(path)


# --- CoStar ------------------------------------------------------------------------------

class CoStarFixture:
    mode = "fixture"

    def __init__(self, site_path: Path | None = None, corrections_path: Path | None = None):
        self._site = _read(site_path or config.site_context_json())
        self._corrections_arg = corrections_path

    @property
    def _corrections(self) -> Path:                     # resolved at write time: private once the process is live
        return self._corrections_arg or config.CORRECTIONS_JSON

    def site(self) -> dict:
        return self._site

    def get_property(self) -> dict:
        return {**self._site["property"], "field_sources": self._site.get("field_sources", {})}

    def list_tenants(self, floor: int | None = None) -> list[dict]:
        return [t for t in self._site.get("tenants", []) if floor is None or t.get("floor") == floor]

    def list_available_space(self, floor: int | None = None) -> list[dict]:
        return [a for a in self._site.get("available_spaces", []) if floor is None or a.get("floor") == floor]

    def file_correction(self, field: str, current: Any, proposed: Any, evidence: dict) -> dict:
        where = "data/live/record_corrections.json" if config.live_active() else "data/record_corrections.json"
        item = {"id": None, "field": field, "current": current, "proposed": proposed, "evidence": evidence,
                "status": f"filed locally (fixture mode: written to {where}, not sent to CoStar)"}
        _append(self._corrections, item, "corr")
        return item

    def clear_corrections(self) -> None:
        if self._corrections.exists():
            self._corrections.unlink()


# --- Matterport ----------------------------------------------------------------------------

class MatterportFixture:
    mode = "fixture"

    def __init__(self, twin_path: Path = config.TWIN_JSON, overlay_path: Path | None = None):
        self._twin = _read(twin_path)
        self._overlay_arg = overlay_path

    @property
    def _overlay(self) -> Path:                         # resolved at write time: private once the process is live
        return self._overlay_arg or config.TAGS_OVERLAY_JSON

    def _overlay_tags(self) -> list[dict]:
        return json.loads(self._overlay.read_text(encoding="utf-8")) if self._overlay.exists() else []

    def get_model(self) -> dict:
        twin = dict(self._twin)
        twin["tags"] = list(self._twin.get("tags", [])) + self._overlay_tags()
        return twin

    def list_tags(self) -> list[dict]:
        return self.get_model()["tags"]

    def add_tag(self, floor: str, position: list[float], label: str, description: str = "", kind: str = "observation") -> dict:
        where = "data/live/twin_tags_overlay.json" if config.live_active() else "data/twin_tags_overlay.json"
        tag = {"id": None, "kind": kind, "label": label, "floor": floor,
               "position": [round(float(v), 2) for v in position], "description": description,
               "status": f"written locally (fixture mode: {where}, not sent to Matterport)"}
        _append(self._overlay, tag, "obs")
        return tag

    def clear_overlay(self) -> None:
        if self._overlay.exists():
            self._overlay.unlink()


# --- Vexcel --------------------------------------------------------------------------------

class VexcelFixture:
    mode = "fixture"

    def __init__(self, aerial_path: Path = config.AERIAL_JSON, site: dict | None = None):
        self._aerial = _read(aerial_path)
        self._site = site or {}

    def aerial(self) -> dict:
        return self._aerial

    def _image(self, image_id: str) -> dict:
        for im in self._aerial["images"]:
            if im["id"] == image_id:
                return im
        raise ValueError(f"unknown aerial image {image_id!r}")

    def find_imagery(self, lat: float, lon: float) -> list[dict]:
        out = []
        for im in self._aerial["images"]:
            w, s, e, n = im["bbox"]
            if w <= lon <= e and s <= lat <= n:
                out.append(im)
        return out

    def get_image(self, image_id: str) -> dict:
        im = dict(self._image(image_id))
        im["abs_path"] = str(config.ROOT / im["path"])
        return im

    def get_structure(self, lat: float, lon: float) -> dict | None:
        prop = self._site.get("property") or {}
        if prop.get("height_m") is None:
            return None
        src = (self._site.get("field_sources") or {}).get("height_m", {})
        return {"height_m": prop["height_m"], "footprint_area_m2": round((prop.get("typical_floor_sf") or 0) / 10.7639) or None,
                "roof_material": None, "roof_condition": None,
                "source": src.get("source", "open data") + " (stand-in for Vexcel structure attributes; roof attributes are null in fixture mode)",
                "synthetic": False}

    def world_to_pixel(self, image_id: str, lat: float, lon: float) -> list[float]:
        im = self._image(image_id)
        w, s, e, n = im["bbox"]
        return [(lon - w) / (e - w) * im["width"], (n - lat) / (n - s) * im["height"]]

    def pixel_to_world(self, image_id: str, px: float, py: float) -> list[float]:
        im = self._image(image_id)
        w, s, e, n = im["bbox"]
        return [n - py / im["height"] * (n - s), w + px / im["width"] * (e - w)]


# --- provider --------------------------------------------------------------------------------

class Provider:
    def __init__(self, costar, matterport, vexcel):
        self.costar, self.matterport, self.vexcel = costar, matterport, vexcel
        self.modes = {"costar": costar.mode, "matterport": matterport.mode, "vexcel": vexcel.mode}
        live = [layer for layer, mode in self.modes.items() if mode == "live"]
        if live:                                          # the privacy guard: cannot be forgotten by a caller
            config.enter_live(live)

    def site(self) -> dict:
        return self.costar.site()

    def twin_raw(self) -> dict:
        return self.matterport.get_model()

    def aerial(self) -> dict:
        return self.vexcel.aerial()

    def labels(self) -> dict:
        site, twin, aerial = self.site(), self.twin_raw(), self.aerial()
        img = (aerial.get("images") or [{}])[0]
        fixture_text = {
            "costar": "Building record: public open data with named sources; CoStar-only fields are empty. Tenant names withheld. Not CoStar data.",
            "matterport": "Twin: synthetic interior fitted to the real building footprint. Not a Matterport scan.",
            "vexcel": f"Aerial: {img.get('source', 'public imagery')}. Not Vexcel imagery.",
        }
        live_text = {"costar": "Building record: CoStar (live).", "matterport": "Twin: Matterport Model API (live).", "vexcel": "Aerial: Vexcel API (live)."}
        out = {}
        for layer, mode in self.modes.items():
            out[layer] = {"mode": mode, "real_sponsor_data": mode == "live", "text": live_text[layer] if mode == "live" else fixture_text[layer]}
        out["open_data"] = site.get("open_data", [])
        out["twin_synthetic"] = bool(twin.get("source", {}).get("synthetic"))
        return out


_provider: Provider | None = None


def build_provider(costar_mode: str | None = None, matterport_mode: str | None = None, vexcel_mode: str | None = None) -> Provider:
    cm, mm, vm = costar_mode or config.COSTAR_MODE, matterport_mode or config.MATTERPORT_MODE, vexcel_mode or config.VEXCEL_MODE
    live = [layer for layer, mode in (("costar", cm), ("matterport", mm), ("vexcel", vm)) if mode == "live"]
    before = config.privacy_state()
    if live:                      # switch to the private root BEFORE any live adapter fetches or caches anything
        config.enter_live(live)
    try:
        if cm == "live":
            from .costar_live import CoStarLive
            costar = CoStarLive.from_env()
        else:
            costar = CoStarFixture()
        if mm == "live":
            from .matterport_live import MatterportLive
            matterport = MatterportLive.from_env()
        else:
            matterport = MatterportFixture()
        if vm == "live":
            from .vexcel_live import VexcelLive
            vexcel = VexcelLive.from_env(site=costar.site())
        else:
            vexcel = VexcelFixture(site=costar.site())
    except Exception:
        config.restore_privacy_state(before)       # nothing live was built (a missing key): leave the process as it was
        raise
    return Provider(costar, matterport, vexcel)


def get_provider() -> Provider:
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider
