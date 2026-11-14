"""Vexcel live adapter (Vexcel API v2).

WRITTEN FROM DOCUMENTATION, NOT EXERCISED WITHOUT A KEY. Every path and parameter below is
taken from the public OpenAPI 3.1 spec at https://api.vexcelgroup.com/v2/api-docs
(saved by the lead under data/reference/vexcel_openapi_v2.json). Authentication is the
`X-API-KEY` header (security scheme "Header" in the spec). The same key works for the
MCP server at https://api.vexcelgroup.com/v2/mcp.

Endpoints used:
  GET /ortho/collections          which ortho collections cover a point
  GET /ortho/extract              an ortho image for a point + bbox-dimensions (metres)
  GET /oriented/query             oblique/nadir frames covering a point (image names, capture dates)
  GET /oriented/extract           one oblique image by product-type (oblique-north|south|east|west)
  GET /oriented/transform-points  world <-> pixel in an oriented image
  GET /structure/extract          building attributes (footprint, roof material and condition, height)
  GET /dsm/elevation, /dtm/elevation   surface and terrain height at a point
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Callable

from .. import config

BASE = "https://api.vexcelgroup.com/v2"
SIDES = {"oblique_n": ("oblique-north", 0.0), "oblique_e": ("oblique-east", 90.0), "oblique_s": ("oblique-south", 180.0), "oblique_w": ("oblique-west", 270.0)}


class VexcelLive:
    mode = "live"

    def __init__(self, api_key: str, site: dict, transport: Callable | None = None, layer: str = "urban", out_dir: Path | None = None):
        self.key, self.site, self.layer = api_key, site, layer
        self.out_dir = out_dir or (config.DATA_DIR / "vexcel")
        self._transport = transport or self._http
        self._images: dict[str, dict] = {}

    @classmethod
    def from_env(cls, site: dict) -> "VexcelLive":
        key = os.environ.get("VEXCEL_API_KEY")
        if not key:
            from . import AdapterError
            raise AdapterError("VEXCEL_MODE=live but VEXCEL_API_KEY is not set (put it in .env or .secrets/vexcel.env)")
        return cls(key, site)

    # ---- transport (replaceable in tests)
    def _http(self, path: str, params: dict, binary: bool = False):
        import httpx
        r = httpx.get(BASE + path, params=params, headers={"X-API-KEY": self.key}, timeout=60.0)
        r.raise_for_status()
        return r.content if binary else r.json()

    @staticmethod
    def _wkt(lat: float, lon: float) -> str:
        return f"POINT({lon} {lat})"

    # ---- adapter interface
    def aerial(self) -> dict:
        prop = self.site["property"]
        if "ortho" not in self._images:
            self._fetch_ortho("ortho", prop["lat"], prop["lon"], 250.0, 1250)
            self._fetch_ortho("ortho_context", prop["lat"], prop["lon"], 760.0, 1267)
            for image_id in SIDES:
                try:
                    self._fetch_oblique(image_id, prop["lat"], prop["lon"])
                except Exception:                      # obliques are optional: not every collection has all four
                    pass
        return {"schema": "sandtable.aerial/1", "placeholder": False, "images": list(self._images.values())}

    def _fetch_ortho(self, image_id: str, lat: float, lon: float, size_m: float, px: int) -> None:
        raw = self._transport("/ortho/extract", {"layer": self.layer, "wkt": self._wkt(lat, lon), "srid": "4326", "bbox-dimensions": f"{size_m:g},{size_m:g}",
                                                 "destination-dimensions": f"{px},{px},fit", "image-format": "png"}, True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{image_id}.png"
        path.write_bytes(raw)
        dlat = size_m / 2 / 111320.0
        dlon = size_m / 2 / (111320.0 * math.cos(math.radians(lat)))
        meta = self._collection(lat, lon)
        self._images[image_id] = {"id": image_id, "kind": "ortho", "path": _rel(path), "url": f"/live/vexcel/{image_id}.png", "bbox": [lon - dlon, lat - dlat, lon + dlon, lat + dlat],
                                  "width": px, "height": px, "gsd_m": round(size_m / px, 3), "native_gsd_m": meta.get("min-gsd"),
                                  "capture_date": meta.get("last-capture-date"), "heading_deg": None, "source": f"Vexcel ortho ({self.layer}, collection {meta.get('collection')})", "synthetic": False}

    def _fetch_oblique(self, image_id: str, lat: float, lon: float) -> None:
        product, heading = SIDES[image_id]
        raw = self._transport("/oriented/extract", {"layer": self.layer, "wkt": self._wkt(lat, lon), "srid": "4326", "product-type": product,
                                                    "bbox-dimensions": "160,160", "destination-dimensions": "1200,1200,fit", "image-format": "png", "rotation": "north"}, True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{image_id}.png"
        path.write_bytes(raw)
        frames = self._transport("/oriented/query", {"layer": self.layer, "wkt": self._wkt(lat, lon), "srid": "4326", "product-type": product,
                                                     "include": "image-name,capture-date,collection", "total-records": 1}, False)
        first = _first(frames, "images")
        self._images[image_id] = {"id": image_id, "kind": "oblique", "path": _rel(path), "url": f"/live/vexcel/{image_id}.png", "bbox": None, "width": 1200, "height": 1200, "gsd_m": None,
                                  "capture_date": first.get("capture-date"), "heading_deg": heading, "image_name": first.get("image-name"),
                                  "source": f"Vexcel oblique ({product})", "synthetic": False}

    def _collection(self, lat: float, lon: float) -> dict:
        try:
            return _first(self._transport("/ortho/collections", {"wkt": self._wkt(lat, lon), "srid": 4326, "layer": self.layer,
                                                                 "include": "collection,last-capture-date,min-gsd", "total-records": 1}, False))
        except Exception:
            return {}

    def find_imagery(self, lat: float, lon: float) -> list[dict]:
        return self.aerial()["images"]

    def get_image(self, image_id: str) -> dict:
        self.aerial()
        if image_id not in self._images:
            raise ValueError(f"unknown aerial image {image_id!r}")
        im = dict(self._images[image_id])
        im["abs_path"] = str(self.out_dir / f"{image_id}.png")
        return im

    def get_structure(self, lat: float, lon: float) -> dict | None:
        data = self._transport("/structure/extract", {"wkt": self._wkt(lat, lon), "srid": "4326", "layer": self.layer, "primary-structure": "true",
                                                      "include": "structures,calculated,metadata", "unit-system": "metric", "buffer-meter": 15}, False)
        feats = (data or {}).get("features") or []
        if not feats:
            return None
        p = feats[0].get("properties") or {}
        roof = p.get("roof-characteristics") or {}
        return {"height_m": p.get("roof-height") or roof.get("roof-height"), "footprint_area_m2": p.get("footprint-area"),
                "roof_material": roof.get("roof-material") or p.get("roof-material"), "roof_condition": roof.get("roof-condition") or p.get("roof-condition"),
                "roof_objects": p.get("roof-objects"), "capture_date": (p.get("metadata") or {}).get("capture-date"),
                "source": "Vexcel structure attributes (/structure/extract)", "synthetic": False}

    def elevation(self, lat: float, lon: float) -> dict:
        dsm = self._transport("/dsm/elevation", {"wkt": self._wkt(lat, lon), "srid": "4326", "layer": self.layer, "unit-system": "metric"}, False)
        dtm = self._transport("/dtm/elevation", {"wkt": self._wkt(lat, lon), "srid": "4326", "unit-system": "metric"}, False)
        return {"surface": dsm, "terrain": dtm}

    def world_to_pixel(self, image_id: str, lat: float, lon: float) -> list[float]:
        im = self.get_image(image_id)
        if im["kind"] == "ortho":
            w, s, e, n = im["bbox"]
            return [(lon - w) / (e - w) * im["width"], (n - lat) / (n - s) * im["height"]]
        out = self._transport("/oriented/transform-points", {"operation": "world-2-pixel", "image-name": im["image_name"], "wkt": self._wkt(lat, lon), "srid": "4326", "metadata-format": "json"}, False)
        pt = _first(out, "points")
        return [pt.get("x"), pt.get("y")]

    def pixel_to_world(self, image_id: str, px: float, py: float) -> list[float]:
        im = self.get_image(image_id)
        if im["kind"] == "ortho":
            w, s, e, n = im["bbox"]
            return [n - py / im["height"] * (n - s), w + px / im["width"] * (e - w)]
        out = self._transport("/oriented/transform-points", {"operation": "pixel-2-world", "image-name": im["image_name"], "wkt": f"POINT({px} {py})", "metadata-format": "json"}, False)
        pt = _first(out, "points")
        return [pt.get("y"), pt.get("x")]


def _rel(path: Path) -> str:
    """Path relative to the project when the file lives inside it (it normally does: data/vexcel/)."""
    try:
        return str(path.resolve().relative_to(config.ROOT))
    except ValueError:
        return str(path)


def _first(payload, *prefer: str) -> dict:
    """The API returns lists under different keys depending on the service; take the first record defensively.
    `prefer` names the key to look under first (confirm the exact response keys against the live service)."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        for key in (*prefer, "collections", "images", "points", "features", "results", "data"):
            v = payload.get(key)
            if isinstance(v, list) and v:
                return v[0].get("properties", v[0]) if isinstance(v[0], dict) else {}
        return payload
    return {}
