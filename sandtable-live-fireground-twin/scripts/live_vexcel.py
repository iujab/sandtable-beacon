"""Vexcel live helper. Everything it fetches stays under data/live/vexcel/ (git-ignored, never published).

    python scripts/live_vexcel.py coverage <lat> <lon>     collections and layers at a point, and their dates
    python scripts/live_vexcel.py fetch <lat> <lon>        ortho, context, roof crop, four obliques, structure,
                                                           parcel detections and elevation into data/live/vexcel/<site>/
    python scripts/live_vexcel.py fetch --demo             the same for the coordinates in data/live/demo_building.json
    python scripts/live_vexcel.py show                     what is cached, with sizes and capture dates
    python scripts/live_vexcel.py check <lat> <lon>        verify placement: API crosshair vs our world_to_pixel

The key is read from the environment (sandtable.config loads .env and the bench .secrets/), sent only in the
X-API-KEY header, and never printed. Every response is cached; a repeated command makes no network call.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandtable import config                                        # noqa: E402  (loads the env files)
from sandtable.adapters.vexcel_live import (LIVE_ROOT, OBLIQUES, VexcelLive, wkt_point, parse_xmp, bbox_of_wkt)  # noqa: E402


def _adapter(lat: float, lon: float) -> VexcelLive:
    import os
    key = os.environ.get("VEXCEL_API_KEY")
    if not key:
        sys.exit("VEXCEL_API_KEY is not set (see docs/TOOLKIT_SWAP.md)")
    return VexcelLive(key, {"property": {"lat": lat, "lon": lon}})


def _point(args) -> tuple[float, float]:
    if getattr(args, "demo", False):
        f = config.LIVE_DIR / "demo_building.json"
        if not f.exists():
            sys.exit(f"{f} does not exist yet")
        d = json.loads(f.read_text(encoding="utf-8"))
        # the provider keys the Vexcel site by the site record's coordinates: use them when the record exists,
        # so `fetch --demo` fills the same directory the server reads
        try:
            site = config.LIVE_DIR / "site" / config._slug(d["address"]) / "site.json"
            if site.is_file():
                prop = json.loads(site.read_text(encoding="utf-8"))["property"]
                return float(prop["lat"]), float(prop["lon"])
        except (AttributeError, KeyError, ValueError):
            pass
        return float(d["lat"]), float(d["lon"])
    if args.lat is None or args.lon is None:
        sys.exit("give <lat> <lon> or --demo")
    return args.lat, args.lon


def cmd_coverage(args) -> None:
    lat, lon = _point(args)
    v = _adapter(lat, lon)
    cov = v.coverage(lat, lon)
    print(f"ortho collections covering the point: {len(cov['ortho'])}")
    for c in cov["ortho"]:
        print(f"  {c['layer']:9s} {c['collection']:36s} {str(c['first_capture'])[:10]} .. {str(c['last_capture'])[:10]}  "
              f"gsd {c['min_gsd_m']}-{c['avg_gsd_m']} m  zoom<={c['max_zoom']}  {c['product_type']}")
    b = cov["ortho_best"]
    print(f"best blue-sky ortho: {b and b['collection']} ({b and b['layer']}, last capture {b and str(b['last_capture'])[:10]})")
    kinds: dict[str, int] = {}
    for c in cov["oriented"]:
        kinds[f"{c['layer']}/{c['product_type']}"] = kinds.get(f"{c['layer']}/{c['product_type']}", 0) + 1
    print(f"oriented collections: {len(cov['oriented'])}  by layer/product: {kinds}")
    newest = max(cov["oriented"], key=lambda c: c.get("last_capture") or "", default=None)
    if newest:
        print(f"  newest oriented: {newest['collection']} ({newest['layer']}, {str(newest['last_capture'])[:10]})")
    obl = v.find_obliques(lat, lon)
    for image_id, (product, _h, face) in OBLIQUES.items():
        m = obl.get(product)
        print(f"  {product:14s} " + (f"{m['collection']} {str(m['capture_date'])[:10]} (shows the {face} face)" if m else "none"))
    for layer, st in cov["layers"].items():
        print(f"layer {layer:10s} {st['status']}  {', '.join(c for c in st['collections'][:3] if c)}")
    print(f"requests: {v.client.stats}")


def cmd_fetch(args) -> None:
    lat, lon = _point(args)
    v = _adapter(lat, lon)
    b = v.fetch_site(lat, lon, obliques=not args.no_obliques, oblique_m=args.oblique_m)
    d = v.site_dir(lat, lon)
    print(f"site directory: {d.relative_to(config.ROOT)}")
    for im in b["images"]:
        f = config.ROOT / im["path"]
        print(f"  {im['id']:14s} {im['kind']:8s} {im['width']}x{im['height']} {im.get('gsd_m')} m/px (native {im.get('native_gsd_m')}) "
              f"{im.get('capture_date')} {f.stat().st_size // 1024} KB" + (f"  shows the {im['shows_face']} face" if im.get("shows_face") else "")
              + (f"  hd {im['hd_width']}x{im['hd_height']} {im['hd_gsd_m']} m/px" if im.get("hd_path") else ""))
    s = b.get("structure") or {}
    print(f"structure: height {s.get('height_m')} m, footprint {s.get('footprint_area_m2')} m2, roof {s.get('roof_material')} {s.get('roof_shape')} "
          f"condition {s.get('roof_condition')} ({s.get('roof_condition_score')}), type {s.get('building_type')}, objects {s.get('roof_objects')}, defects {s.get('roof_defects')}")
    e = b.get("elevation") or {}
    print(f"elevation: surface {e.get('surface_m')} m, terrain {e.get('terrain_m')} m, height {e.get('height_m')} m")
    p = b.get("property") or {}
    print(f"parcel: vehicles {p.get('vehicles')}, pool {p.get('pool')}, hardscape {p.get('hardscape_pct')}%, tree cover {p.get('tree_cover_pct')}%, inside parcel {p.get('inside_parcel')}")
    for n in b.get("notes") or []:
        print(f"note: {n}")
    print(f"requests: {v.client.stats}")


def cmd_show(_args) -> None:
    if not LIVE_ROOT.exists():
        print("nothing cached")
        return
    for man in sorted(LIVE_ROOT.glob("*/aerial.json")):
        b = json.loads(man.read_text(encoding="utf-8"))
        print(f"{man.parent.relative_to(config.ROOT)}  fetched {b.get('fetched_at')}")
        for im in b["images"]:
            f = config.ROOT / im["path"]
            size = f.stat().st_size // 1024 if f.exists() else "missing"
            hd = config.ROOT / im["hd_path"] if im.get("hd_path") else None
            print(f"  {im['id']:14s} {im['kind']:8s} {im['width']}x{im['height']} {im.get('gsd_m')} m/px  captured {im.get('capture_date')}  {size} KB"
                  + (f"  (+hd {hd.stat().st_size // 1024} KB)" if hd and hd.exists() else ""))
    cache = LIVE_ROOT / "cache"
    if cache.exists():
        print("response cache:")
        for d in sorted(p for p in cache.iterdir() if p.is_dir()):
            reqs = list(d.glob("*.req.json"))
            statuses: dict[int, int] = {}
            for r in reqs:
                st = json.loads(r.read_text(encoding="utf-8"))["status"]
                statuses[st] = statuses.get(st, 0) + 1
            size = sum(p.stat().st_size for p in d.glob("*.bin"))
            print(f"  {d.name:28s} {len(reqs):3d} responses {size // 1024:7d} KB  status {statuses}")


def cmd_check(args) -> None:
    """Ask the API to draw a crosshair at points around the building and compare with our own pixel mapping."""
    import io
    import numpy as np
    from PIL import Image
    lat, lon = _point(args)
    v = _adapter(lat, lon)
    man = v.site_dir(lat, lon) / "aerial.json"
    if not man.exists():
        sys.exit("fetch the site first")
    b = json.loads(man.read_text(encoding="utf-8"))
    v._bundles[man.parent.name] = b
    ortho = next(i for i in b["images"] if i["id"] == "ortho")
    clat = (ortho["bbox"][1] + ortho["bbox"][3]) / 2
    for de, dn in ((0, 0), (60, 40), (-80, -70)):
        tlat, tlon = lat + dn / 111320, lon + de / (111320 * math.cos(math.radians(clat)))
        params = {"layer": ortho["layer"], "collection": ortho["collection"], "wkt": wkt_point(lat, lon), "srid": "4326",
                  "bbox-dimensions": "250,250", "destination-dimensions": "500,500,fit", "image-format": "png",
                  "crosshair": wkt_point(tlat, tlon), "crosshair-color": "rgb(255,0,255)"}
        raw = v.client.get_image("/ortho/extract", params)
        w, s, e, n = bbox_of_wkt(parse_xmp(raw)["geometry"])
        a = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
        ys, xs = np.where(np.abs(a - np.array([255, 0, 255])).sum(2) < 150)
        seen = ((xs.min() + xs.max() + 1) / 2, (ys.min() + ys.max() + 1) / 2)
        ours = ((tlon - w) / (e - w) * 500, (n - tlat) / (n - s) * 500)
        board = v.world_to_pixel("ortho", tlat, tlon)
        k = ortho["width"] / 500
        print(f"ortho point {de:+4d} m E {dn:+4d} m N: crosshair at {seen[0]:.1f},{seen[1]:.1f}; ours {ours[0]:.1f},{ours[1]:.1f} "
              f"(error {(seen[0] - ours[0]) * 0.5:+.2f} m, {(seen[1] - ours[1]) * 0.5:+.2f} m); board image px {board[0]:.0f},{board[1]:.0f} = {board[0] / k:.1f},{board[1] / k:.1f} at 500 px")
    for im in b["images"]:
        if im["kind"] != "oblique":
            continue
        params = {"layer": im["layer"], "image-name": im["image_name"], "wkt": wkt_point(lat, lon), "srid": "4326", "bbox-dimensions": "100,100",
                  "downsample": 1, "crop": "bbox", "image-format": "png", "crosshair": wkt_point(lat, lon), "crosshair-color": "rgb(255,0,255)"}
        raw = v.client.get_image("/oriented/extract", params)
        xmp = parse_xmp(raw)
        a = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
        ys, xs = np.where(np.abs(a - np.array([255, 0, 255])).sum(2) < 150)
        if not len(xs):
            print(f"{im['id']}: no crosshair drawn")
            continue
        seen = ((xs.min() + xs.max() + 1) / 2, (ys.min() + ys.max() + 1) / 2)
        from sandtable.adapters.vexcel_live import oblique_crop, frame_to_crop
        crop = oblique_crop(xmp, a.shape[1], a.shape[0], 1)
        out = v.client.get_json("/oriented/transform-points", {"operation": "world-2-pixel", "image-name": im["image_name"],
                                                                "wkt": wkt_point(lat, lon), "srid": "4326", "metadata-format": "json"})
        ours = frame_to_crop(crop, out["points"][0]["x"], out["points"][0]["y"])
        print(f"{im['id']}: crosshair at {seen[0]:.1f},{seen[1]:.1f}; transform-points + crop gives {ours[0]:.1f},{ours[1]:.1f} "
              f"(error {seen[0] - ours[0]:+.1f}, {seen[1] - ours[1]:+.1f} px)")
    print(f"requests: {v.client.stats}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("coverage", "fetch", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("lat", type=float, nargs="?")
        sp.add_argument("lon", type=float, nargs="?")
        sp.add_argument("--demo", action="store_true", help="use data/live/demo_building.json")
        if name == "fetch":
            sp.add_argument("--no-obliques", action="store_true")
            sp.add_argument("--oblique-m", type=float, default=None, help="side of the oblique crop in metres (default: from the footprint and height)")
    sub.add_parser("show")
    args = ap.parse_args()
    {"coverage": cmd_coverage, "fetch": cmd_fetch, "show": cmd_show, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    main()
