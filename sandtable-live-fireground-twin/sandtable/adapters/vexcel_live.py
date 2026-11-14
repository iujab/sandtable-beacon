"""Vexcel live adapter (Vexcel API v2), checked against the live service with a sponsor key.

Authentication is the `X-API-KEY` request header. The key is never put in a URL, a cache file name,
a log line or an error message. Every response is cached on disk under `data/live/vexcel/cache/`
(git-ignored; the publisher skips any path with a `live` directory), so a second run makes no network
call and the demo works offline once a site has been fetched. Nothing fetched from Vexcel may leave
`data/live/`: tests use invented payloads.

Endpoints used (all GET, base https://api.vexcelgroup.com/v2), with the response shapes seen live:
  /ortho/collections      GeoJSON FeatureCollection; properties: layer, collection, first/last-capture-date,
                          min/avg-gsd, max-zoom, product-type, camera-technology
  /ortho/extract          image bytes; XMP packet in the file: Vexcel:geometry (the exact extent returned,
                          POLYGON in EPSG:4326), Vexcel:pixel-bbox (x0,y0,w,h in the Web Mercator tile-pixel
                          grid of the zoom used), capture and publish dates, collection, min-gsd
  /oriented/query         FeatureCollection; properties: image-name, product-type, capture-date, collection, ...
  /oriented/extract       image bytes; XMP: Vexcel:image-name, capture-date, product-type, raster size and
                          Vexcel:pixel-bbox = the crop inside the source frame, in pixels of the downsample level
  /oriented/transform-points  {"points": [{"x", "y"(, "z")}], "dem": [...], "collection": [...]};
                          pixel coordinates are in the full-resolution source frame
  /structure/extract      FeatureCollection; flat dotted property keys ("roof-characteristics.material.class",
                          "roof-objects.ac-unit.count", "footprint.wkt", "roof-characteristics.height", ...)
  /property/extract       FeatureCollection; parcel keys ("vehicles.count", "pool.detected", "hardscapes.percent",
                          ...) plus "structures": [...]
  /dsm/elevation, /dtm/elevation   {"wkt": "POINT Z(lon lat z)" or "MULTIPOINT Z(...)", "collection": [...]}
  /{structure,property,dsm,3d,embedding,damage}/collections   coverage of the other layers at the point
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Callable

from .. import config

BASE = "https://api.vexcelgroup.com/v2"
LIVE_ROOT = config.LIVE_DIR / "vexcel"               # every byte from Vexcel lives under here (data/live/vexcel; never moves)
URL_PREFIX = "/live/vexcel"                           # where the server serves LIVE_ROOT
VISION_MAX_PX = 1536                                  # board and vision copies; the model downsizes larger images anyway

# image id -> (product-type, look direction in degrees, the building side that faces the camera)
OBLIQUES = {"oblique_n": ("oblique-north", 0.0, "south"), "oblique_e": ("oblique-east", 90.0, "west"),
            "oblique_s": ("oblique-south", 180.0, "north"), "oblique_w": ("oblique-west", 270.0, "east")}
ORTHO_LAYERS = "urban,urban-r,wide-area"              # blue-sky layers, preferred in this order
COLLECTION_FIELDS = "layer,collection,first-capture-date,last-capture-date,min-gsd,avg-gsd,max-zoom,product-type,camera-technology"
ROOF_CONDITION = {1: "severe", 2: "poor", 3: "fair", 4: "good", 5: "excellent"}

Transport = Callable[[str, dict], "tuple[int, str, bytes]"]


class VexcelError(RuntimeError):
    def __init__(self, path: str, status: int, detail: str = ""):
        super().__init__(f"Vexcel {path} answered HTTP {status}{': ' + detail if detail else ''}")
        self.path, self.status = path, status


class VexcelNotLicensed(VexcelError):
    pass


# ---- the HTTP client with its disk cache -------------------------------------------------------------

class VexcelClient:
    """GET with the key in the X-API-KEY header and every response cached on disk.

    The cache file name is a hash of the endpoint and parameters (which never contain the key); next to the
    body, a `.req.json` keeps the endpoint, parameters, status, content type and fetch time for `show`.
    Negative answers (204, 401, 403, 404) are cached too, so a missing layer is asked once.
    """

    def __init__(self, api_key: str, cache_dir: Path | None = None, transport: Transport | None = None, offline: bool | None = None):
        self._key = api_key
        self.cache_dir = cache_dir or (LIVE_ROOT / "cache")
        self._transport = transport or self._http
        self.offline = (os.environ.get("VEXCEL_OFFLINE") == "1") if offline is None else offline
        self.stats = {"network": 0, "cache": 0}

    def __repr__(self) -> str:                         # never show the key
        return f"VexcelClient(cache_dir={self.cache_dir!s}, offline={self.offline})"

    def _http(self, path: str, params: dict) -> tuple[int, str, bytes]:
        import httpx
        for v in params.values():
            if self._key and self._key in str(v):
                raise ValueError("refusing to send the API key as a query parameter")
        r = httpx.get(BASE + path, params=params, headers={"X-API-KEY": self._key}, timeout=120.0)
        return r.status_code, r.headers.get("content-type", ""), r.content

    @staticmethod
    def cache_key(path: str, params: dict) -> str:
        canon = json.dumps({"path": path, "params": {k: str(v) for k, v in params.items()}}, sort_keys=True)
        return hashlib.sha256(canon.encode()).hexdigest()[:24]

    def _files(self, path: str, params: dict) -> tuple[Path, Path]:
        d = self.cache_dir / path.strip("/").replace("/", "-")
        k = self.cache_key(path, params)
        return d / f"{k}.req.json", d / f"{k}.bin"

    def request(self, path: str, params: dict, refresh: bool = False) -> tuple[int, str, bytes]:
        meta_p, body_p = self._files(path, params)
        if meta_p.exists() and not refresh:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            self.stats["cache"] += 1
            return meta["status"], meta.get("content_type", ""), body_p.read_bytes() if body_p.exists() else b""
        if self.offline:
            raise VexcelError(path, 0, "not in the cache and VEXCEL_OFFLINE=1")
        t0 = time.time()
        status, ctype, body = self._transport(path, dict(params))
        self.stats["network"] += 1
        if status >= 500 or status == 429:                # transient: do not cache
            raise VexcelError(path, status, _short(body, self._key))
        meta_p.parent.mkdir(parents=True, exist_ok=True)
        body_p.write_bytes(body)
        meta_p.write_text(json.dumps({"path": path, "params": params, "status": status, "content_type": ctype, "bytes": len(body),
                                      "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "elapsed_s": round(time.time() - t0, 2)}, indent=1), encoding="utf-8")
        return status, ctype, body

    def get_json(self, path: str, params: dict) -> dict | None:
        """Parsed JSON, or None when the service has nothing at this place (204, or 404 'No results')."""
        status, _ctype, body = self.request(path, params)
        if status in (204, 404):
            return None
        if status in (401, 403):
            raise VexcelNotLicensed(path, status, _short(body, self._key))
        if status != 200:
            raise VexcelError(path, status, _short(body, self._key))
        return json.loads(body) if body else None

    def get_image(self, path: str, params: dict) -> bytes | None:
        status, ctype, body = self.request(path, params)
        if status in (204, 404):
            return None
        if status in (401, 403):
            raise VexcelNotLicensed(path, status, _short(body, self._key))
        if status != 200 or not ctype.startswith("image/"):
            raise VexcelError(path, status, _short(body, self._key))
        return body


def _short(body: bytes, key: str = "") -> str:
    text = body[:200].decode("utf-8", errors="replace").replace("\n", " ")
    return text.replace(key, "[key]") if key else text


# ---- parsing helpers (pure functions; tested with invented payloads) ------------------------------------

def wkt_point(lat: float, lon: float) -> str:
    return f"POINT({lon:.7f} {lat:.7f})"


def parse_xmp(data: bytes) -> dict:
    """Vexcel:<name> tags of the XMP packet embedded in an extracted image."""
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.S)
    if not m:
        return {}
    return {k.decode(): v.decode("utf-8", errors="replace") for k, v in re.findall(rb"<Vexcel:([\w-]+)>(.*?)</Vexcel:\1>", m.group(0), re.S)}


def wkt_rings(wkt: str) -> list[list[tuple[float, float]]]:
    """Outer rings of a POLYGON or MULTIPOLYGON as [(x, y), ...] lists (holes are dropped)."""
    body = wkt[wkt.index("("):]
    polys = re.findall(r"\(\(([^()]*)\)", body) if wkt.strip().upper().startswith("MULTI") else re.findall(r"\(\(([^()]*)\)", body)[:1]
    return [[tuple(float(v) for v in p.split()[:2]) for p in poly.split(",")] for poly in polys]


def wkt_points(wkt: str) -> list[tuple[float, ...]]:
    """Coordinates of a POINT [Z] / MULTIPOINT [Z] / LINESTRING as tuples."""
    nums = re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?(?:\s+-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)+", wkt[wkt.index("("):])
    return [tuple(float(v) for v in n.split()) for n in nums]


def bbox_of_wkt(wkt: str) -> list[float]:
    pts = [p for ring in wkt_rings(wkt) for p in ring]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def mercator_bbox(pixel_bbox: str, zoom: int) -> list[float]:
    """[west, south, east, north] of a Web Mercator tile-pixel box "x0,y0,w,h" at `zoom`."""
    x0, y0, w, h = (int(v) for v in pixel_bbox.split(","))
    world = 256 * 2 ** zoom
    lon = lambda x: x / world * 360.0 - 180.0
    lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world))))
    return [lon(x0), lat(y0 + h), lon(x0 + w), lat(y0)]


def point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def ground_m(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    dy = (lat1 - lat0) * 111320.0
    dx = (lon1 - lon0) * 111320.0 * math.cos(math.radians((lat0 + lat1) / 2))
    return math.hypot(dx, dy)


def parse_collections(fc: dict | None) -> list[dict]:
    out = []
    for f in (fc or {}).get("features") or []:
        p = f.get("properties") or {}
        out.append({"layer": p.get("layer"), "collection": p.get("collection"), "first_capture": p.get("first-capture-date"),
                    "last_capture": p.get("last-capture-date"), "min_gsd_m": _round(p.get("min-gsd"), 4), "avg_gsd_m": _round(p.get("avg-gsd"), 4),
                    "max_zoom": p.get("max-zoom"), "product_type": p.get("product-type"), "camera": p.get("camera-technology")})
    return out


def best_collection(cols: list[dict], layers: str = ORTHO_LAYERS) -> dict | None:
    """Newest blue-sky collection, preferring the layer order given (urban before urban-r before wide-area)."""
    order = layers.split(",")
    usable = [c for c in cols if c.get("layer") in order and c.get("product_type") in ("final-ortho", "dxm-ortho", "quick-ortho", None)]
    if not usable:
        return None
    newest = max(c.get("last_capture") or "" for c in usable)
    # the newest capture year wins; within it, the preferred layer
    year = newest[:4]
    same_year = [c for c in usable if (c.get("last_capture") or "")[:4] == year]
    return sorted(same_year, key=lambda c: (order.index(c["layer"]), -(_ts(c.get("last_capture")))))[0]


def _ts(s: str | None) -> float:
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) if s else 0.0
    except ValueError:
        return 0.0


def _round(v, n):
    return round(v, n) if isinstance(v, (int, float)) else v


def pick_structure(fc: dict | None, lat: float, lon: float) -> dict | None:
    """The structure whose footprint contains the point, else the one with the nearest roof centroid."""
    feats = [f.get("properties") or {} for f in (fc or {}).get("features") or []]
    if not feats:
        return None
    for p in feats:
        wkt = p.get("footprint.wkt")
        if wkt and any(point_in_ring(lon, lat, r) for r in wkt_rings(wkt)):
            return p

    def dist(p):
        c = p.get("roof-characteristics.centroid.wkt")
        if not c:
            return 1e9
        x, y = wkt_points(c)[0][:2]
        return ground_m(lat, lon, y, x)
    return min(feats, key=dist)


def map_structure(p: dict) -> dict:
    """Vexcel's flat dotted structure keys -> the adapter contract (+ what a fire officer reads off a roof)."""
    cond = p.get("roof-characteristics.condition")
    objects = {name: p.get(f"roof-objects.{key}.count") for name, key in
               (("ac_units", "ac-unit"), ("roof_vents", "roof-vent"), ("skylights", "skylight"), ("satellite_dishes", "satellite-dish"),
                ("chimneys", "chimney"), ("solar_pv_panels", "solar-panel-photovoltaic"), ("solar_thermal_panels", "solar-panel-thermal"))}
    objects["solar"] = p.get("roof-objects.solar.value")
    objects["solar_pv_area_m2"] = p.get("roof-objects.solar-panel-photovoltaic.area")
    defects = sorted({k.split(".")[-2] for k, v in p.items() if k.startswith("roof-defects.") and k.endswith(".detected") and v is True})
    footprint = None
    if p.get("footprint.wkt"):
        rings = wkt_rings(p["footprint.wkt"])
        footprint = {"type": "Polygon", "coordinates": [[list(pt) for pt in rings[0]]]} if len(rings) == 1 else \
                    {"type": "MultiPolygon", "coordinates": [[[list(pt) for pt in r]] for r in rings]}
    centroid = wkt_points(p["roof-characteristics.centroid.wkt"])[0][:2] if p.get("roof-characteristics.centroid.wkt") else None
    return {"height_m": _round(p.get("roof-characteristics.height"), 1),
            "height_basis": "Vexcel calculated roof height (surface model minus terrain at the roof centroid)" if p.get("roof-characteristics.height") is not None else None,
            "footprint_area_m2": _round(p.get("footprint.area"), 0),
            "roof_material": p.get("roof-characteristics.material.class"),
            "roof_shape": p.get("roof-characteristics.shape.value"),
            "roof_condition": ROOF_CONDITION.get(int(cond)) if isinstance(cond, (int, float)) else None,
            "roof_condition_score": p.get("roof-characteristics.condition.precise-value") if p.get("roof-characteristics.condition.precise-value") is not None else cond,
            "roof_condition_scale": "1 severe .. 5 excellent",
            "roof_objects": objects, "roof_defects": defects,
            "roof_tree_cover_pct": p.get("roof-characteristics.tree-cover.percent"),
            "building_type": p.get("building-type.class"),
            "ground_elevation_m": _round(p.get("ground-elevation.value"), 1), "roof_base_elevation_m": _round(p.get("roof-characteristics.elevation"), 1),
            "defensible_space_m": {k.split(".")[-1]: v for k, v in p.items() if k.startswith("defensible-space.")} or None,
            "roof_centroid": [centroid[1], centroid[0]] if centroid else None,
            "footprint": footprint,
            "capture_date": (p.get("metadata.ortho-date-estimate") or "")[:10] or None, "collection": p.get("metadata.collection"),
            "gsd_m": p.get("metadata.gsd"),
            "source": "Vexcel structure attributes (/structure/extract)", "synthetic": False}


def map_property(p: dict) -> dict:
    """Parcel-level detections (/property/extract) that matter on a fireground."""
    g = lambda k: p.get(k)
    return {"vehicles": g("vehicles.count") or 0 if g("vehicles.detected") else 0, "vehicles_detected": g("vehicles.detected"),
            "boats": g("vehicles.types.boat.count"), "pool": g("pool.detected"), "pool_area_m2": g("pool.total-area"),
            "enclosure": g("enclosure.detected"), "sport_court": g("sport-court.detected"), "deck": g("deck.detected"),
            "playground": g("playground.detected"), "hardscape_pct": g("hardscapes.percent"), "tree_cover_pct": g("parcel-tree-cover.percent"),
            "structures_on_parcel": g("structure.count"), "inside_parcel": g("request-information.inside-parcel"),
            "parcel_source": g("parcel-source"), "parcel": g("parcel-wkt"),
            "capture_date": (g("metadata.ortho-date-estimate") or "")[:10] or None, "collection": g("metadata.collection"),
            "source": "Vexcel property attributes (/property/extract)", "synthetic": False}


def oblique_crop(xmp: dict, width: int, height: int, downsample: int) -> dict:
    """How a crop maps to its source frame: source_px = (crop_px * w0/width + x0) * 2**downsample."""
    x0, y0, w0, h0 = (float(v) for v in xmp["pixel-bbox"].split(","))
    return {"x0": x0, "y0": y0, "w": w0, "h": h0, "downsample": downsample, "sx": width / w0, "sy": height / h0}


def frame_to_crop(crop: dict, x: float, y: float) -> list[float]:
    f = 2 ** crop["downsample"]
    return [(x / f - crop["x0"]) * crop["sx"], (y / f - crop["y0"]) * crop["sy"]]


def crop_to_frame(crop: dict, px: float, py: float) -> list[float]:
    f = 2 ** crop["downsample"]
    return [(px / crop["sx"] + crop["x0"]) * f, (py / crop["sy"] + crop["y0"]) * f]


def site_slug(lat: float, lon: float) -> str:
    return f"site_{lat:.5f}_{lon:.5f}".replace("-", "m")


# ---- the adapter -----------------------------------------------------------------------------------------

class VexcelLive:
    """The contract of docs/CONTRACTS.md 4.1 plus coverage, elevation and parcel detections."""
    mode = "live"

    def __init__(self, api_key: str, site: dict, transport: Transport | None = None, out_root: Path | None = None,
                 client: VexcelClient | None = None, ortho_m: float = 250.0, context_m: float = 760.0):
        self.site = site
        self.root = out_root or LIVE_ROOT
        self.client = client or VexcelClient(api_key, cache_dir=self.root / "cache", transport=transport)
        self.ortho_m, self.context_m = ortho_m, context_m
        self._bundles: dict[str, dict] = {}

    def __repr__(self) -> str:
        return f"VexcelLive(root={self.root!s})"

    @classmethod
    def from_env(cls, site: dict) -> "VexcelLive":
        key = os.environ.get("VEXCEL_API_KEY")
        if not key:
            from . import AdapterError
            raise AdapterError("VEXCEL_MODE=live but VEXCEL_API_KEY is not set (put it in .env or .secrets/vexcel.env)")
        return cls(key, site)

    # ---- where things go
    def site_dir(self, lat: float, lon: float) -> Path:
        return self.root / site_slug(lat, lon)

    def _rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(config.ROOT))
        except ValueError:
            return str(path)

    def _url(self, path: Path) -> str:
        return URL_PREFIX + "/" + str(path.resolve().relative_to(self.root.resolve())).replace(os.sep, "/")

    # ---- coverage
    def coverage(self, lat: float, lon: float) -> dict:
        """Which collections and layers cover the point, how fresh they are, and which layers the key may not use."""
        pt = wkt_point(lat, lon)
        out: dict = {"point": [lat, lon], "layers": {}}
        ortho = parse_collections(self.client.get_json("/ortho/collections", {"wkt": pt, "srid": 4326, "include": COLLECTION_FIELDS}))
        out["ortho"] = ortho
        out["ortho_best"] = best_collection(ortho)
        orient = parse_collections(self.client.get_json("/oriented/collections", {"wkt": pt, "srid": 4326, "include": COLLECTION_FIELDS}))
        out["oriented"] = orient
        for layer in ("structure", "property", "dsm", "dtm", "3d", "embedding", "damage"):
            try:
                fc = self.client.get_json(f"/{layer}/collections", {"wkt": pt, "srid": 4326})
                cols = [(f.get("properties") or {}).get("collection") for f in (fc or {}).get("features") or []]
                out["layers"][layer] = {"status": "available" if cols else "no coverage here", "collections": cols[:6]}
            except VexcelNotLicensed as exc:
                out["layers"][layer] = {"status": f"not licensed (HTTP {exc.status})", "collections": []}
            except VexcelError as exc:
                out["layers"][layer] = {"status": f"error (HTTP {exc.status})", "collections": []}
        return out

    # ---- orthos
    def fetch_ortho(self, image_id: str, lat: float, lon: float, size_m: float, zoom: int, dest: Path, collection: dict | None = None) -> dict:
        """A square ortho around the point at a fixed tile zoom (native at the collection's max zoom).
        The extent is read from the image's own XMP (the API may snap it), never assumed from the request."""
        params = {"layer": (collection or {}).get("layer") or "urban", "wkt": wkt_point(lat, lon), "srid": "4326",
                  "bbox-dimensions": f"{size_m:g},{size_m:g}", "zoom": zoom, "image-format": "jpeg"}
        if collection and collection.get("collection"):
            params["collection"] = collection["collection"]
        raw = self.client.get_image("/ortho/extract", params)
        if raw is None:
            raise VexcelError("/ortho/extract", 204, "no ortho at this point")
        xmp = parse_xmp(raw)
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        if "geometry" in xmp:
            bbox, basis = bbox_of_wkt(xmp["geometry"]), "XMP Vexcel:geometry of the returned image"
        elif "pixel-bbox" in xmp:
            bbox, basis = mercator_bbox(xmp["pixel-bbox"], zoom), "XMP Vexcel:pixel-bbox at the tile zoom"
        else:
            raise VexcelError("/ortho/extract", 200, "the image carries no extent metadata; refusing to guess its bounds")
        dest.parent.mkdir(parents=True, exist_ok=True)
        hd = dest.with_name(dest.stem + "_hd.jpg")
        hd.write_bytes(raw)
        scale = min(1.0, VISION_MAX_PX / max(w, h))
        bw, bh = (round(w * scale), round(h * scale))
        img.convert("RGB").resize((bw, bh), Image.LANCZOS).save(dest, quality=92)
        m_per_deg_lon = 111320.0 * math.cos(math.radians((bbox[1] + bbox[3]) / 2))
        span_m = (bbox[2] - bbox[0]) * m_per_deg_lon
        cap = xmp.get("last-capture-date") or (collection or {}).get("last_capture")
        return {"id": image_id, "kind": "ortho", "path": self._rel(dest), "url": self._url(dest), "bbox": [round(v, 9) for v in bbox], "bbox_basis": basis,
                "width": bw, "height": bh, "gsd_m": round(span_m / bw, 3),
                "hd_path": self._rel(hd), "hd_url": self._url(hd), "hd_width": w, "hd_height": h, "hd_gsd_m": round(span_m / w, 3),
                "native_gsd_m": _round(float(xmp["min-gsd"]) if xmp.get("min-gsd") else (collection or {}).get("min_gsd_m"), 3),
                "capture_date": _date(cap), "capture_window": [_date(xmp.get("first-capture-date")), _date(xmp.get("last-capture-date"))],
                "collection": xmp.get("collection") or (collection or {}).get("collection"), "layer": xmp.get("layer") or params["layer"],
                "zoom": zoom, "heading_deg": None, "source": f"Vexcel ortho ({xmp.get('layer') or params['layer']}, {xmp.get('collection') or 'collection unknown'})",
                "synthetic": False}

    def roof_crop(self, ortho: dict, footprint: dict | None, dest: Path, margin_m: float = 12.0) -> dict | None:
        """A native-resolution crop of the roof cut locally from the high-resolution ortho (no extra API call)."""
        if not footprint:
            return None
        from PIL import Image
        ring = footprint["coordinates"][0] if footprint["type"] == "Polygon" else [p for poly in footprint["coordinates"] for p in poly[0]]
        w, s, e, n = ortho["bbox"]
        W, H = ortho["hd_width"], ortho["hd_height"]
        lat0 = (s + n) / 2
        mlon, mlat = margin_m / (111320.0 * math.cos(math.radians(lat0))), margin_m / 111320.0
        lons, lats = [p[0] for p in ring], [p[1] for p in ring]
        x0 = max(0, math.floor((min(lons) - mlon - w) / (e - w) * W))
        x1 = min(W, math.ceil((max(lons) + mlon - w) / (e - w) * W))
        y0 = max(0, math.floor((n - (max(lats) + mlat)) / (n - s) * H))
        y1 = min(H, math.ceil((n - (min(lats) - mlat)) / (n - s) * H))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        img = Image.open(config.ROOT / ortho["hd_path"]).crop((x0, y0, x1, y1))
        scale = min(1.0, VISION_MAX_PX / max(img.size))        # the annotated PNG must stay under the API's 5 MB image limit
        if scale < 1.0:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        img.convert("RGB").save(dest, quality=94)
        bbox = [w + x0 / W * (e - w), n - y1 / H * (n - s), w + x1 / W * (e - w), n - y0 / H * (n - s)]
        return {**{k: ortho[k] for k in ("native_gsd_m", "capture_date", "capture_window", "collection", "layer", "source", "synthetic")},
                "id": "roof", "kind": "detail", "path": self._rel(dest), "url": self._url(dest), "bbox": [round(v, 9) for v in bbox],
                "bbox_basis": "pixel crop of the ortho high-resolution image", "width": img.width, "height": img.height,
                "gsd_m": round(ortho["hd_gsd_m"] / scale, 3), "heading_deg": None, "note": "roof and its immediate surroundings at native resolution"}

    # ---- obliques
    def find_obliques(self, lat: float, lon: float, layer: str = "urban,urban-r") -> dict[str, dict]:
        """Newest image of each look direction that covers the point, nearest to the image centre."""
        fc = self.client.get_json("/oriented/query", {"wkt": wkt_point(lat, lon), "srid": 4326, "layer": layer, "product-type": "oblique",
                                                       "include": "layer,image-name,product-type,capture-date,collection,avg-gsd,raster-size-width,raster-size-height",
                                                       "order-by": "collection-last-capture-date-desc,image-center-distance-asc", "total-records": 40})
        best: dict[str, dict] = {}
        for f in (fc or {}).get("features") or []:
            p = f.get("properties") or {}
            pt = p.get("product-type")
            if pt and pt not in best:
                best[pt] = {"image_name": p.get("image-name"), "layer": p.get("layer"), "capture_date": p.get("capture-date"),
                            "collection": p.get("collection"), "avg_gsd_m": p.get("avg-gsd")}
        return best

    def fetch_oblique(self, image_id: str, lat: float, lon: float, size_m: float, dest: Path, image_name: str | None = None,
                      layer: str = "urban", downsample: int | None = None) -> dict | None:
        product, heading, face = OBLIQUES[image_id]
        ds = downsample if downsample is not None else (0 if size_m <= 110 else 1)
        params = {"layer": layer, "wkt": wkt_point(lat, lon), "srid": "4326", "product-type": product, "bbox-dimensions": f"{size_m:g},{size_m:g}",
                  "downsample": ds, "crop": "bbox", "image-format": "jpeg", "order-by": "collection-last-capture-date-desc,image-center-distance-asc"}
        if image_name:
            params["image-name"] = image_name
        raw = self.client.get_image("/oriented/extract", params)
        if raw is None:
            return None
        xmp = parse_xmp(raw)
        if "pixel-bbox" not in xmp or "image-name" not in xmp:
            raise VexcelError("/oriented/extract", 200, "the oblique carries no crop metadata; world_to_pixel would be wrong")
        from PIL import Image, ImageOps
        import io
        img = Image.open(io.BytesIO(raw))
        if (img.getexif() or {}).get(0x0112, 1) not in (1, None):
            raise VexcelError("/oriented/extract", 200, "rotated JPEG (EXIF orientation); pixel mapping not supported")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        w, h = img.size
        crop = oblique_crop(xmp, w, h, ds)
        gsd = float(xmp.get("min-gsd") or 0) or None
        return {"id": image_id, "kind": "oblique", "path": self._rel(dest), "url": self._url(dest), "bbox": None, "width": w, "height": h,
                "gsd_m": round(gsd * 2 ** ds / crop["sx"], 3) if gsd else None, "native_gsd_m": _round(gsd, 3),
                "capture_date": _date(xmp.get("capture-date")), "heading_deg": heading, "look": product.split("-")[1], "shows_face": face,
                "image_name": xmp["image-name"], "collection": xmp.get("collection"), "layer": xmp.get("layer") or layer, "crop": crop,
                "source": f"Vexcel oblique ({product}, {xmp.get('collection') or 'collection unknown'})", "synthetic": False}

    # ---- structure, parcel, elevation
    def structure_raw(self, lat: float, lon: float) -> dict | None:
        return self.client.get_json("/structure/extract", {"wkt": wkt_point(lat, lon), "srid": "4326", "primary-structure": "true",
                                                            "include": "precomputed,calculated", "unit-system": "metric", "buffer-meter": 30})

    def get_structure(self, lat: float, lon: float) -> dict | None:
        p = pick_structure(self.structure_raw(lat, lon), lat, lon)
        return map_structure(p) if p else None

    def get_property(self, lat: float, lon: float) -> dict | None:
        fc = self.client.get_json("/property/extract", {"wkt": wkt_point(lat, lon), "srid": "4326", "primary-structure": "true",
                                                         "unit-system": "metric", "buffer-meter": 30})
        feats = (fc or {}).get("features") or []
        return map_property(feats[0].get("properties") or {}) if feats else None

    def elevation(self, lat: float, lon: float, roof_point: list[float] | None = None) -> dict:
        """Surface (DSM) and terrain (DTM) height at the roof point; their difference is the building height there."""
        rlat, rlon = roof_point or (lat, lon)
        pt = wkt_point(rlat, rlon)
        dsm = self.client.get_json("/dsm/elevation", {"wkt": pt, "srid": "4326", "layer": "urban", "unit-system": "metric"})
        dtm = self.client.get_json("/dtm/elevation", {"wkt": pt, "srid": "4326", "unit-system": "metric"})
        z = lambda d: wkt_points(d["wkt"])[0][2] if d and d.get("wkt") and len(wkt_points(d["wkt"])[0]) > 2 else None
        s, t = z(dsm), z(dtm)
        return {"at": [rlat, rlon], "surface_m": _round(s, 2), "terrain_m": _round(t, 2),
                "height_m": round(s - t, 1) if s is not None and t is not None else None,
                "surface_collection": ((dsm or {}).get("collection") or [None])[0], "terrain_collection": ((dtm or {}).get("collection") or [None])[0],
                "source": "Vexcel /dsm/elevation minus /dtm/elevation", "synthetic": False}

    # ---- the bundle (sandtable.aerial/1)
    def fetch_site(self, lat: float, lon: float, obliques: bool = True, oblique_m: float | None = None) -> dict:
        """Fetch (or reuse from the cache) everything for one site and write <site>/aerial.json and friends."""
        d = self.site_dir(lat, lon)
        d.mkdir(parents=True, exist_ok=True)
        cov = self.coverage(lat, lon)
        best = cov.get("ortho_best")
        if not best:
            raise VexcelError("/ortho/collections", 204, "no blue-sky ortho covers this point")
        zoom = int(best.get("max_zoom") or 21)
        images = [self.fetch_ortho("ortho", lat, lon, self.ortho_m, zoom, d / "ortho.jpg", best),
                  self.fetch_ortho("ortho_context", lat, lon, self.context_m, zoom - 2, d / "ortho_context.jpg", best)]
        notes: list[str] = []
        structure = prop = None
        try:
            structure = self.get_structure(lat, lon)
        except VexcelNotLicensed as exc:
            notes.append(f"structure attributes not licensed (HTTP {exc.status})")
        roof = self.roof_crop(images[0], (structure or {}).get("footprint"), d / "roof.jpg")
        if roof:
            images.append(roof)
        elev = self.elevation(lat, lon, (structure or {}).get("roof_centroid"))
        try:
            prop = self.get_property(lat, lon)
        except VexcelNotLicensed as exc:
            notes.append(f"property attributes not licensed (HTTP {exc.status})")
        found = {}
        if obliques:
            found = self.find_obliques(lat, lon)
            if oblique_m is None:
                ext = _extent_m((structure or {}).get("footprint"))
                oblique_m = max(80.0, min(200.0, ext + 2 * max(20.0, (structure or {}).get("height_m") or 20.0)))
            # centre the obliques on the roof, not on the street address point
            olat, olon = (structure or {}).get("roof_centroid") or (lat, lon)
            for image_id, (product, _h, _f) in OBLIQUES.items():
                meta = found.get(product)
                if not meta:
                    notes.append(f"no {product} image covers the building")
                    continue
                im = self.fetch_oblique(image_id, olat, olon, oblique_m, d / f"{image_id}.jpg", meta["image_name"], layer=meta.get("layer") or "urban")
                if im:
                    images.append(im)
        bundle = {"schema": "sandtable.aerial/1", "placeholder": False, "site": {"lat": lat, "lon": lon, "slug": d.name},
                  "images": images, "structure": structure, "elevation": elev, "property": prop,
                  "coverage": {"ortho_best": best, "ortho_collections": len(cov["ortho"]), "layers": cov["layers"]},
                  "notes": notes, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "source_note": "Vexcel API v2, live with the sponsor key. Kept under data/live/ only; never committed or published."}
        for name, obj in (("aerial.json", bundle), ("coverage.json", cov)):
            tmp = d / (name + ".tmp")
            tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
            tmp.replace(d / name)
        self._bundles[d.name] = bundle
        return bundle

    def _bundle(self) -> dict:
        prop = self.site["property"]
        d = self.site_dir(prop["lat"], prop["lon"])
        if d.name not in self._bundles:
            manifest = d / "aerial.json"
            self._bundles[d.name] = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else self.fetch_site(prop["lat"], prop["lon"])
        return self._bundles[d.name]

    # ---- adapter interface (docs/CONTRACTS.md 4.1)
    def aerial(self) -> dict:
        return self._bundle()

    def _image(self, image_id: str) -> dict:
        for im in self._bundle()["images"]:
            if im["id"] == image_id:
                return im
        raise ValueError(f"unknown aerial image {image_id!r}")

    def find_imagery(self, lat: float, lon: float) -> list[dict]:
        out = []
        for im in self._bundle()["images"]:
            if im.get("bbox"):
                w, s, e, n = im["bbox"]
                if w <= lon <= e and s <= lat <= n:
                    out.append(im)
            elif im["kind"] == "oblique":
                px, py = self.world_to_pixel(im["id"], lat, lon)
                if 0 <= px < im["width"] and 0 <= py < im["height"]:
                    out.append(im)
        return out

    def get_image(self, image_id: str) -> dict:
        im = dict(self._image(image_id))
        im["abs_path"] = str(config.ROOT / im["path"])
        return im

    def world_to_pixel(self, image_id: str, lat: float, lon: float) -> list[float]:
        im = self._image(image_id)
        if im.get("bbox"):
            w, s, e, n = im["bbox"]
            return [(lon - w) / (e - w) * im["width"], (n - lat) / (n - s) * im["height"]]
        out = self.client.get_json("/oriented/transform-points", {"operation": "world-2-pixel", "image-name": im["image_name"],
                                                                   "wkt": wkt_point(lat, lon), "srid": "4326", "metadata-format": "json"})
        pts = (out or {}).get("points") or []
        if not pts:
            raise ValueError(f"the point does not project into {image_id}")
        return frame_to_crop(im["crop"], pts[0]["x"], pts[0]["y"])

    def world_to_pixel_many(self, image_id: str, points: list[list[float]]) -> list[list[float]]:
        """[[lat, lon], ...] -> [[px, py], ...]; for an oblique, one transform-points call for all the points."""
        im = self._image(image_id)
        if im.get("bbox") or not points:
            return [self.world_to_pixel(image_id, lat, lon) for lat, lon in points]
        if len(points) == 1:
            return [self.world_to_pixel(image_id, *points[0])]
        wkt = "LINESTRING(" + ", ".join(f"{lon:.7f} {lat:.7f}" for lat, lon in points) + ")"
        out = self.client.get_json("/oriented/transform-points", {"operation": "world-2-pixel", "image-name": im["image_name"],
                                                                   "wkt": wkt, "srid": "4326", "metadata-format": "json"})
        pts = (out or {}).get("points") or []
        if len(pts) != len(points):
            raise ValueError(f"transform-points returned {len(pts)} points for {len(points)}")
        return [frame_to_crop(im["crop"], q["x"], q["y"]) for q in pts]

    def pixel_to_world(self, image_id: str, px: float, py: float) -> list[float]:
        im = self._image(image_id)
        if im.get("bbox"):
            w, s, e, n = im["bbox"]
            return [n - py / im["height"] * (n - s), w + px / im["width"] * (e - w)]
        x, y = crop_to_frame(im["crop"], px, py)
        out = self.client.get_json("/oriented/transform-points", {"operation": "pixel-2-world", "image-name": im["image_name"],
                                                                   "wkt": f"POINT({x:.2f} {y:.2f})", "srid": "4326", "metadata-format": "json"})
        pts = (out or {}).get("points") or []
        if not pts:
            raise ValueError(f"pixel ({px}, {py}) does not project from {image_id}")
        return [pts[0]["y"], pts[0]["x"]]


def _date(s: str | None) -> str | None:
    """'2025-12-31T16:30:11', '2025-12-31 17:01:14.0' or 'Fri Jan 02 18:43:55 UTC 2026' -> '2025-12-31'."""
    if not s:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        return time.strftime("%Y-%m-%d", time.strptime(s.replace("UTC ", ""), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return s


def _extent_m(footprint: dict | None) -> float:
    if not footprint:
        return 40.0
    ring = footprint["coordinates"][0] if footprint["type"] == "Polygon" else [p for poly in footprint["coordinates"] for p in poly[0]]
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    return max(ground_m(min(lats), min(lons), min(lats), max(lons)), ground_m(min(lats), min(lons), max(lats), min(lons)))
