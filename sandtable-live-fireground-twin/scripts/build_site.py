#!/usr/bin/env python3
"""Build a site bundle (sandtable.site/1 + NAIP aerials) from real, keyless open data.

Fixture (no arguments): writes fixtures/building/site.json (the CoStar-shaped record + public context)
and fixtures/aerial/{aerial.json, ortho.png, ortho_context.png}. Its output must not change.

Any Los Angeles or Houston address (the public context of a live building):
    python scripts/build_site.py --address "<street address>" --city la|houston --out data/live/site/<slug>/ [--lat LAT --lon LON]
    python scripts/build_site.py --from-demo        address, lat and lon from data/live/demo_building.json
writes <out>/site.json, <out>/aerial/ and caches raw answers in <out>/_open/. The output folder must be under
a directory named `live` (the address is the sponsor's demo building: private, never committed). With --lat/--lon
the parcel and the footprint are the ones at that point, which is more robust than matching the address text.
Houston sources: Harris County Appraisal District parcels, City of Houston building footprints (with height),
Houston Water hydrants, City of Houston fire stations; TIGER roads and NAIP for both cities. Houston has no keyless
open source for year built, building area or tenants by suite: those stay null (CoStar has them).

Nothing here is CoStar, Matterport or Vexcel data. Every value carries its
source; values that only the sponsor would have are null. Business and person
names are deliberately withheld (suite, floor and NAICS category are kept)
because the bundle is used for a fictional emergency exercise.

Raw responses are cached (data/open/ for the fixture) so the script is re-runnable offline.
Usage: python scripts/build_site.py [--refresh] [--address ... --city ... --out ...]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "open"
OUT_SITE = ROOT / "fixtures" / "building" / "site.json"
AERIAL_DIR = ROOT / "fixtures" / "aerial"
UA = {"User-Agent": "sandtable-fixture-builder/1.0 (open-data fetch)"}
REFRESH = "--refresh" in sys.argv
sys.path.insert(0, str(ROOT))
from sandtable.adapters.costar_extract import HAZARD_RULES as _RULES  # noqa: E402  (one rule table for fixture and live)
from sandtable.config import _slug as slug, demo_building, is_private  # noqa: E402

# The building: a real three-storey professional (medical office) building in Los Angeles.
HOUSE_NO, STREET_KEY, STREET_LABEL = "7325", "MEDICAL CENTER", "Medical Center Dr"
FIXTURE = True              # False for --address: a live building's public context
CITY = "la"
POINT: tuple[float, float] | None = None       # (lat, lon) of the building when known

PARCELS = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
LARIAC = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Dynamic/LARIAC_Buildings_2020/MapServer/0/query"
BUSINESSES = "https://data.lacity.org/resource/6rrh-rzua.json"
TIGER_ROADS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/8/query"
LAFD = "https://maps.lacity.org/lahub/rest/services/Fire_Department/MapServer"
NAIP = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer"
HCAD = "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0/query"
COH_FOOTPRINTS = "https://geogimsms.houstontx.gov/arcgis/rest/services/EMC/BuildingFootprints_Inspections/MapServer/0/query"
COH_HYDRANTS = "https://geogimsms.houstontx.gov/arcgis/rest/services/HW/OutsideWater_gx/MapServer/32/query"
COH_STATIONS = "https://geogimsms.houstontx.gov/arcgis/rest/services/DIR/healthandPublicSafety_gx/MapServer/9/query"

SOURCES = {
    "assessor": {"name": "Los Angeles County Assessor parcel record", "what": "address, year built, building area, use, construction class", "url": PARCELS},
    "lariac": {"name": "LA County LARIAC building outlines (2020 service, 2008 capture)", "what": "footprint, roof height, ground elevation", "url": LARIAC},
    "businesses": {"name": "City of Los Angeles, Listing of Active Businesses", "what": "tenant suite, floor and NAICS category (names withheld)", "url": "https://data.lacity.org/d/6rrh-rzua"},
    "tiger": {"name": "US Census TIGERweb roads", "what": "street geometry for the address side", "url": TIGER_ROADS},
    "lafd": {"name": "City of Los Angeles GeoHub, Fire Department layers", "what": "hydrants (LADWP) and fire stations", "url": LAFD},
    "naip": {"name": "USDA NAIP orthoimagery via the USGS National Map", "what": "aerial imagery (public domain)", "url": NAIP},
}

HOUSTON_SOURCES = {
    "assessor": {"name": "Harris County Appraisal District parcels (Harris County GIS)", "what": "parcel outline, account, land use", "url": HCAD},
    "lariac": {"name": "City of Houston building footprints (EMC inspections layer)", "what": "footprint, building height, base elevation", "url": COH_FOOTPRINTS},
    "tiger": SOURCES["tiger"],
    "lafd": {"name": "City of Houston GIS: Houston Water hydrants and HFD fire stations", "what": "hydrants and fire stations", "url": COH_HYDRANTS},
    "naip": SOURCES["naip"],
}

# Rule table: NAICS prefix -> (life hazard, hazmat). Inferred from the category, not observed.
# Shared with the live CoStar adapter (sandtable/adapters/costar_extract.py), which also matches industry text.
HAZARD_RULES = [(pre, lh, hz) for pre, _words, lh, hz in _RULES if pre]


def get(url: str, params: dict | None, name: str, binary: bool = False):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if path.exists() and not REFRESH:
        return path.read_bytes() if binary else json.loads(path.read_text())
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(full, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if not binary:
        json.loads(raw)                      # fail loudly on a non-JSON answer before caching
    path.write_bytes(raw)
    return raw if binary else json.loads(raw)


def soft(fn, what: str, unavailable: list[str]):
    try:
        return fn()
    except Exception as exc:                 # a source being down must not stop the build
        unavailable.append(f"{what}: {type(exc).__name__}: {exc}")
        return None


def to_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    return ((lon - lon0) * 111320.0 * math.cos(math.radians(lat0)), (lat - lat0) * 111320.0)


def from_m(lat0: float, lon0: float, e: float, n: float) -> tuple[float, float]:
    return (lat0 + n / 111320.0, lon0 + e / (111320.0 * math.cos(math.radians(lat0))))


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    e, n = to_m(a[0], a[1], b[0], b[1])
    return math.hypot(e, n)


def poly_area(pts: list[tuple[float, float]]) -> float:
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))) / 2


def min_area_rect(pts: list[tuple[float, float]]):
    """Minimum-area bounding rectangle. Returns (length, width, bearing_deg of the long axis, centre e/n)."""
    best = None
    for i in range(len(pts)):
        dx, dy = pts[(i + 1) % len(pts)][0] - pts[i][0], pts[(i + 1) % len(pts)][1] - pts[i][1]
        seg = math.hypot(dx, dy)
        if seg < 0.5:
            continue
        ux, uy = dx / seg, dy / seg
        us = [p[0] * ux + p[1] * uy for p in pts]
        vs = [-p[0] * uy + p[1] * ux for p in pts]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * h < best[0]:
            cu, cv = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
            centre = (cu * ux - cv * uy, cu * uy + cv * ux)
            if w >= h:
                best = (w * h, w, h, math.degrees(math.atan2(ux, uy)) % 180.0, centre)
            else:
                best = (w * h, h, w, math.degrees(math.atan2(-uy, ux)) % 180.0, centre)
    return best[1], best[2], best[3], best[4]


def nearest_on_polyline(p, line):
    best = None
    for a, b in zip(line, line[1:]):
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
        q = (ax + t * dx, ay + t * dy)
        d = math.hypot(p[0] - q[0], p[1] - q[1])
        if best is None or d < best[0]:
            best = (d, q)
    return best


def _ring(geom: dict) -> list:
    return geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]


def _parcel_query(where: str | None) -> dict:
    """The parcel by address (fixture) or, for a live building with a known point, the parcel at that point."""
    if CITY == "houston":
        q = {"outFields": "*", "outSR": "4326", "f": "geojson"}
        if POINT:
            q.update({"geometry": f"{POINT[1]},{POINT[0]}", "geometryType": "esriGeometryPoint", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects"})
        else:
            q["where"] = where
        return get(HCAD, q, "parcel.json")
    if POINT:
        return get(PARCELS, {"geometry": f"{POINT[1]},{POINT[0]}", "geometryType": "esriGeometryPoint", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                             "outFields": "*", "outSR": "4326", "f": "geojson"}, "parcel.json")
    return get(PARCELS, {"where": where, "outFields": "*", "outSR": "4326", "f": "geojson"}, "parcel.json")


def main() -> None:
    unavailable: list[str] = []

    # 1. parcel record ---------------------------------------------------------
    if CITY == "houston":
        parcel = _parcel_query(f"site_str_num={int(HOUSE_NO)} AND UPPER(site_str_name) LIKE '%{STREET_KEY}%'")
    else:
        parcel = _parcel_query(f"SitusHouseNo='{HOUSE_NO}' AND SitusStreet LIKE '{STREET_KEY}%'" if FIXTURE else f"SitusHouseNo='{HOUSE_NO}' AND SitusStreet LIKE '%{STREET_KEY}%'")
    if not parcel.get("features"):
        raise SystemExit(f"no parcel found for the address in the {CITY} parcel layer (try --lat/--lon)")
    pf = parcel["features"][0]
    pa = pf["properties"]
    pring = pf["geometry"]["coordinates"][0] if pf["geometry"]["type"] == "Polygon" else pf["geometry"]["coordinates"][0][0]
    env = f"{min(p[0] for p in pring)},{min(p[1] for p in pring)},{max(p[0] for p in pring)},{max(p[1] for p in pring)}"
    pc = (sum(p[1] for p in pring) / len(pring), sum(p[0] for p in pring) / len(pring))
    if POINT:
        pc = POINT

    # 2. building footprint and height ------------------------------------------
    fp_url = COH_FOOTPRINTS if CITY == "houston" else LARIAC
    blds = get(fp_url, {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": "4326", "f": "geojson"}, "lariac_buildings.json")

    def centroid(feat):
        r = _ring(feat["geometry"]) if not FIXTURE else feat["geometry"]["coordinates"][0]
        return (sum(q[1] for q in r) / len(r), sum(q[0] for q in r) / len(r))

    if not blds.get("features"):
        raise SystemExit("no building footprint found on the parcel")
    bld = min(blds["features"], key=lambda f: dist_m(pc, centroid(f)))
    ring = _ring(bld["geometry"]) if not FIXTURE else bld["geometry"]["coordinates"][0]
    lat0, lon0 = centroid(bld)
    pts = [to_m(lat0, lon0, q[1], q[0]) for q in ring[:-1]]
    length, width, bearing, centre = min_area_rect(pts)
    if CITY == "houston":                                   # City of Houston heights are in metres
        h_m = bld["properties"].get("BLDGHEIGHT")
        height_ft = float(h_m) / 0.3048 if h_m else float("nan")
    else:
        height_ft = float(bld["properties"]["HEIGHT"])
    fp_area_m2 = poly_area(pts)

    # 3. tenants: suite, floor and category only ----------------------------------
    if CITY == "houston":
        rows = []                                           # no keyless Houston business listing with suites: CoStar has the tenants
        unavailable.append("tenants: no keyless Houston open dataset lists businesses by suite (the CoStar record has them)")
    else:
        rows = get(BUSINESSES, {"$select": "street_address,naics,primary_naics_description,location_start_date,location_end_date",
                                "$where": f"street_address like '{HOUSE_NO} {STREET_KEY}%'" if FIXTURE else f"street_address like '{HOUSE_NO} %{STREET_KEY}%'",
                                "$limit": "500"}, "businesses.json")
    tenants = []
    for r in sorted(rows, key=lambda r: (r.get("street_address", ""), r.get("location_start_date", ""))):
        if r.get("location_end_date"):
            continue
        m = re.search(r"#\s*([A-Z0-9-]+)", r.get("street_address", ""))
        if not m:
            continue
        suite = m.group(1)
        naics = r.get("naics") or ""
        cat = (r.get("primary_naics_description") or "Business").strip()
        hazard = next(((lh, hz) for pre, lh, hz in HAZARD_RULES if naics.startswith(pre)), (None, None))
        short = re.sub(r"\s*\(.*?\)", "", cat)
        short = short.replace("Offices of ", "").strip()
        short = short[:1].upper() + short[1:]
        tenants.append({
            "tenant_id": f"reg-{suite}-{len([t for t in tenants if t['suite'] == suite]) + 1}",
            "name": f"{short} (name withheld)", "suite": suite, "floor": int(suite[0]) if suite[:1].isdigit() else None,
            "sf": None, "industry": cat, "naics": naics, "registered_since": (r.get("location_start_date") or "")[:4] or None,
            "lease_expiration": None, "life_hazard": hazard[0], "hazmat": hazard[1],
            "hazard_basis": "inferred from the NAICS category by a rule table, not observed" if hazard[0] or hazard[1] else None,
            "source": SOURCES["businesses"]["name"], "synthetic": False,
        })
    by_suite: dict[str, int] = {}
    for t in tenants:
        by_suite[t["suite"]] = by_suite.get(t["suite"], 0) + 1
    multi = sorted(s for s, n in by_suite.items() if n > 1)

    # 4. address side from the street geometry -----------------------------------
    roads = soft(lambda: get(TIGER_ROADS, {"geometry": f"{lon0 - 0.002},{lat0 - 0.0018},{lon0 + 0.002},{lat0 + 0.0018}", "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                                           "outFields": "NAME,MTFCC", "outSR": "4326", "f": "geojson"}, "tiger_roads.json"), "TIGER roads", unavailable)
    street_pt = None
    if roads:
        for f in roads["features"]:
            if (f["properties"].get("NAME") or "").lower().startswith(STREET_KEY.lower()) if FIXTURE else STREET_KEY.lower() in (f["properties"].get("NAME") or "").lower():
                geoms = f["geometry"]["coordinates"] if f["geometry"]["type"] == "MultiLineString" else [f["geometry"]["coordinates"]]
                for line in geoms:
                    cand = nearest_on_polyline((0.0, 0.0), [to_m(lat0, lon0, q[1], q[0]) for q in line])
                    if cand and (street_pt is None or cand[0] < street_pt[0]):
                        street_pt = cand
    # facade normals of the fitted rectangle: +long axis, -long axis, +short axis, -short axis
    b = math.radians(bearing)
    normals = {"long+": bearing, "long-": (bearing + 180) % 360, "short+": (bearing - 90) % 360, "short-": (bearing + 90) % 360}
    if street_pt:
        street_bearing = math.degrees(math.atan2(street_pt[1][0] - centre[0], street_pt[1][1] - centre[1])) % 360
    else:
        street_bearing = 90.0
    side_a = min(normals.values(), key=lambda nb: min(abs(nb - street_bearing), 360 - abs(nb - street_bearing)))
    sides = {}
    for i, letter in enumerate("ABCD"):
        sides[letter] = {"bearing_deg": round((side_a + 90 * i) % 360, 1), "street": STREET_LABEL if letter == "A" else None,
                         "note": "address side (nearest street centreline in TIGER)" if letter == "A" else None}

    def offset_from_centre(bearing_deg: float, metres: float) -> tuple[float, float]:
        r = math.radians(bearing_deg)
        return from_m(lat0, lon0, centre[0] + metres * math.sin(r), centre[1] + metres * math.cos(r))

    half_a = (length / 2) if side_a in (normals["long+"], normals["long-"]) else (width / 2)
    entrance = offset_from_centre(side_a, half_a)
    if street_pt:
        engine = from_m(lat0, lon0, street_pt[1][0], street_pt[1][1])
    else:
        engine = offset_from_centre(side_a, half_a + 18.0)

    # 5. hydrants and fire stations ----------------------------------------------
    hyd_url, sta_url = (COH_HYDRANTS, COH_STATIONS) if CITY == "houston" else (f"{LAFD}/0/query", f"{LAFD}/1/query")
    hyd = soft(lambda: get(hyd_url, {"geometry": f"{lon0},{lat0}", "geometryType": "esriGeometryPoint", "inSR": "4326", "distance": "260", "units": "esriSRUnit_Meter",
                                               "outFields": "*", "outSR": "4326", "f": "geojson"}, "hydrants.json"), "LAFD hydrants", unavailable)
    hydrants = []
    for f in (hyd or {}).get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        props = f["properties"]
        hydrants.append({"id": f"h{len(hydrants) + 1}", "lat": round(lat, 6), "lon": round(lon, 6), "distance_m": round(dist_m((lat0, lon0), (lat, lon))),
                         "size": props.get("SIZE_CODE") or props.get("Size") or props.get("SIZE") or props.get("BARRELDIAMETER"), "source": SOURCES["lafd"]["name"], "synthetic": False})
    hydrants.sort(key=lambda h: h["distance_m"])
    for i, h in enumerate(hydrants):
        h["id"] = f"h{i + 1}"
    sta = soft(lambda: get(sta_url, {"geometry": f"{lon0},{lat0}", "geometryType": "esriGeometryPoint", "inSR": "4326", "distance": "6000", "units": "esriSRUnit_Meter",
                                               "outFields": "*", "outSR": "4326", "f": "geojson"}, "fire_stations.json"), "LAFD fire stations", unavailable)
    stations = []
    for f in (sta or {}).get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        props = f["properties"]
        label = next((str(props[k]) for k in ("FACILITY_N", "Name", "NAME", "STATION", "FS_CD", "FIRE_STATION") if props.get(k)), "Fire station")
        if CITY == "houston":
            label = next((str(props[k]) for k in ("TEXT_", "LABEL", "NAME") if props.get(k)), "Fire station")
        stations.append({"name": label if "station" in label.lower() else f"{'HFD' if CITY == 'houston' else 'LAFD'} Station {label}", "lat": round(lat, 6), "lon": round(lon, 6),
                         "distance_m": round(dist_m((lat0, lon0), (lat, lon))), "address": props.get("ADDRESS") or props.get("Address"),
                         "source": SOURCES["lafd"]["name"], "synthetic": False})
    stations.sort(key=lambda s: s["distance_m"])
    stations = stations[:2]

    # 6. aerial imagery (real NAIP) ------------------------------------------------
    AERIAL_DIR.mkdir(parents=True, exist_ok=True)
    images = []
    for image_id, half_m, px in (("ortho", 125.0, 1250), ("ortho_context", 380.0, 1267)):
        dlat = half_m / 111320.0
        dlon = half_m / (111320.0 * math.cos(math.radians(lat0)))
        bbox = [round(lon0 - dlon, 7), round(lat0 - dlat, 7), round(lon0 + dlon, 7), round(lat0 + dlat, 7)]
        raw = soft(lambda: get(f"{NAIP}/exportImage", {"bbox": ",".join(map(str, bbox)), "bboxSR": "4326", "imageSR": "4326", "size": f"{px},{px}", "format": "png", "f": "image"},
                               f"naip_{image_id}.png", binary=True), f"NAIP {image_id}", unavailable)
        if not raw or raw[:4] != b"\x89PNG":
            unavailable.append(f"NAIP {image_id}: no PNG returned")
            continue
        (AERIAL_DIR / f"{image_id}.png").write_bytes(raw)
        images.append({"id": image_id, "kind": "ortho", "path": f"{AERIAL_DIR.relative_to(ROOT).as_posix()}/{image_id}.png", "bbox": bbox, "width": px, "height": px,
                       "gsd_m": round(2 * half_m / px, 3), "native_gsd_m": 0.6, "capture_date": None, "heading_deg": None,
                       "source": SOURCES["naip"]["name"], "synthetic": False,
                       "note": "Resampled by the USGS image service from 0.6 m NAIP. Stand-in for Vexcel ortho in fixture mode."})
    meta = soft(lambda: get(f"{NAIP}/query", {"geometry": f"{lon0},{lat0}", "geometryType": "esriGeometryPoint", "inSR": "4326", "outFields": "*", "returnGeometry": "false", "f": "json"},
                            "naip_meta.json"), "NAIP metadata", unavailable)
    if meta and meta.get("features"):
        years = []
        for f in meta["features"]:
            a = f["attributes"]
            for k, v in a.items():
                if k.lower() in ("year", "acquisitiondate", "src_date", "srcimgdate", "acq_date") and v:
                    years.append(str(v))
        if years:
            for im in images:
                im["capture_date"] = sorted(years)[-1][:10]
    (AERIAL_DIR / "aerial.json").write_text(json.dumps({"schema": "sandtable.aerial/1", "placeholder": False, "images": images}, indent=1))

    # 7. the record ---------------------------------------------------------------
    if not FIXTURE:
        return write_live(locals())
    qclass = (pa.get("QualityClass1") or "").strip()
    class_text = {"A": "Class A: fire-resistive, protected structural steel frame", "B": "Class B: reinforced concrete frame",
                  "C": "Class C: masonry or concrete walls, wood or steel floors and roof", "D": "Class D: wood frame", "S": "Class S: metal frame and walls"}.get(qclass[:1])
    stories = max(int(t["floor"]) for t in tenants if t["floor"]) if tenants else None
    rba = int(pa.get("SQFTmain1") or 0) or None
    fp_sf = round(fp_area_m2 * 10.7639)
    assessor, lariac = SOURCES["assessor"]["name"], SOURCES["lariac"]["name"]
    prop = {
        "property_id": f"APN {pa.get('APN')}", "name": f"Professional building, {HOUSE_NO} {STREET_LABEL}",
        "address": f"{HOUSE_NO} {STREET_LABEL}", "city": "West Hills, Los Angeles", "state": "CA", "zip": (pa.get("SitusZIP") or "")[:5],
        "lat": round(lat0, 6), "lon": round(lon0, 6),
        "property_type": f"Office / medical ({pa.get('UseDescription')})", "building_class": None,
        "rba_sf": rba, "typical_floor_sf": fp_sf, "stories": stories, "year_built": int(pa.get("YearBuilt1") or 0) or None, "year_renovated": None,
        "construction": class_text, "sprinklers": None, "elevators": None, "parking_spaces": None, "parking_ratio": None, "percent_leased": None,
        "height_m": round(height_ft * 0.3048, 1), "ground_elevation_m": round(float(bld["properties"].get("ELEV") or 0) * 0.3048, 1),
        "owner": None, "property_manager": None,
    }
    only = {"source": "CoStar-only field: null in fixture mode", "synthetic": False}
    field_sources = {
        "property_id": {"source": assessor, "synthetic": False}, "address": {"source": assessor, "synthetic": False},
        "property_type": {"source": assessor + " (use description)", "synthetic": False},
        "rba_sf": {"source": assessor + " (main building area; not CoStar RBA)", "synthetic": False},
        "typical_floor_sf": {"source": lariac + " (footprint area)", "synthetic": False},
        "stories": {"source": "derived: highest suite floor in " + SOURCES["businesses"]["name"] + f", consistent with roof height {height_ft:.0f} ft", "synthetic": False},
        "year_built": {"source": assessor, "synthetic": False},
        "construction": {"source": assessor + f" (quality class '{qclass}', class letter decoded with the standard A/B/C/D/S definitions)", "synthetic": False},
        "height_m": {"source": lariac, "synthetic": False}, "ground_elevation_m": {"source": lariac, "synthetic": False},
        **{k: only for k in ("building_class", "sprinklers", "elevators", "parking_spaces", "parking_ratio", "percent_leased", "owner", "property_manager", "year_renovated")},
    }
    checks = []
    if rba and stories:
        gross = fp_sf * stories
        checks.append({"id": "area", "layers": ["costar", "vexcel"],
                       "text": f"Record area {rba:,} sq ft versus footprint {fp_sf:,} sq ft x {stories} floors = {gross:,} sq ft ({(gross - rba) / rba * 100:+.0f}%). Confirm rentable versus gross area, or an atrium or void.",
                       "record_sf": rba, "footprint_sf": fp_sf, "stories": stories})
    for s in multi:
        cats = [f"{t['industry']} (since {t['registered_since']})" for t in tenants if t["suite"] == s]
        checks.append({"id": f"suite-{s}", "layers": ["costar"], "suite": s, "text": f"Suite {s} has {len(cats)} active registrations: " + "; ".join(cats) + ". Confirm the current occupant."})

    site = {
        "schema": "sandtable.site/1", "placeholder": False,
        "source_note": ("FIXTURE built from public open data by scripts/build_site.py. Not CoStar data. Chosen because it is a real multi-tenant medical office "
                        "building in Los Angeles with a compact footprint, numbered suites on three floors and surface parking visible from the air. "
                        "Business and person names are withheld; suite, floor and NAICS category are real. CoStar-only fields are null."),
        "property": prop, "field_sources": field_sources, "tenants": tenants, "available_spaces": [],
        "footprint": {"type": "Polygon", "coordinates": [[[round(q[0], 7), round(q[1], 7)] for q in ring]]},
        "frame": {"centre": {"lat": round(from_m(lat0, lon0, *centre)[0], 7), "lon": round(from_m(lat0, lon0, *centre)[1], 7)},
                  "length_m": round(length, 2), "width_m": round(width, 2), "bearing_deg": round(bearing, 2),
                  "note": "minimum-area bounding rectangle of the footprint; bearing is the compass bearing of the long axis"},
        "sides": sides,
        "context": {
            "hydrants": hydrants[:8], "fire_stations": stations,
            "apparatus_positions": [{"id": "E1", "label": "Engine 1", "lat": round(engine[0], 6), "lon": round(engine[1], 6), "side": "A",
                                     "source": "inferred: nearest point of the address street to the building", "synthetic": True}],
            "entrances": [{"id": "main", "label": "Main entrance (assumed mid-facade, side A)", "lat": round(entrance[0], 6), "lon": round(entrance[1], 6), "side": "A",
                           "leads_to": "stairA", "source": "inferred from the footprint; not surveyed", "synthetic": True}],
            "permits": [],
        },
        "cross_checks": checks,
        "open_data": list(SOURCES.values()),
        "unavailable": unavailable,
    }
    OUT_SITE.parent.mkdir(parents=True, exist_ok=True)
    OUT_SITE.write_text(json.dumps(site, indent=1))
    print(f"site: {prop['address']} | {stories} floors | {rba} sq ft | built {prop['year_built']} | {prop['construction']}")
    print(f"footprint {length:.1f} x {width:.1f} m, bearing {bearing:.1f}, side A faces {side_a:.0f} deg; height {prop['height_m']} m")
    print(f"tenants: {len(tenants)} registrations in {len(by_suite)} suites; multi-registration suites: {multi}")
    print(f"hydrants: {len(hydrants)}; stations: {[(s['name'], s['distance_m']) for s in stations]}; images: {[i['id'] for i in images]}")
    if unavailable:
        print("UNAVAILABLE:", *unavailable, sep="\n  ")


def write_live(v: dict) -> None:
    """The public-context bundle of a live building: same shape as the fixture, CoStar fields null, private output."""
    pa, tenants, unavailable, srcs = v["pa"], v["tenants"], v["unavailable"], SOURCES
    lat0, lon0, centre, length, width, bearing = v["lat0"], v["lon0"], v["centre"], v["length"], v["width"], v["bearing"]
    height_ft, fp_sf = v["height_ft"], round(v["fp_area_m2"] * 10.7639)
    stories = max(int(t["floor"]) for t in tenants if t["floor"]) if tenants else None
    if CITY == "houston":
        apn, use, zip5, year, rba, city = pa.get("acct_num") or pa.get("HCAD_NUM"), pa.get("land_use") or pa.get("state_class"), str(pa.get("site_zip") or "")[:5], None, None, (pa.get("site_city") or "Houston").title()
    else:
        apn, use, zip5, year, rba, city = pa.get("APN"), pa.get("UseDescription"), (pa.get("SitusZIP") or "")[:5], int(pa.get("YearBuilt1") or 0) or None, int(pa.get("SQFTmain1") or 0) or None, (pa.get("SitusCity") or "Los Angeles").title()
    assessor, footprints = srcs["assessor"]["name"], srcs["lariac"]["name"]
    ok_height = height_ft == height_ft
    prop = {"property_id": f"APN {apn}" if apn else None, "name": None, "address": ADDRESS, "city": city, "state": "TX" if CITY == "houston" else "CA", "zip": zip5 or None,
            "lat": round(lat0, 6), "lon": round(lon0, 6), "property_type": use, "building_class": None, "rba_sf": rba, "typical_floor_sf": fp_sf, "stories": stories,
            "year_built": year, "year_renovated": None, "construction": None, "sprinklers": None, "elevators": None, "parking_spaces": None, "parking_ratio": None,
            "percent_leased": None, "height_m": round(height_ft * 0.3048, 1) if ok_height else None, "owner": None, "property_manager": None}
    only = {"source": "CoStar-only field: null in the open-data context", "synthetic": False}
    field_sources = {"property_id": {"source": assessor, "synthetic": False}, "address": {"source": "the demo building's address", "synthetic": False},
                     "property_type": {"source": assessor, "synthetic": False}, "typical_floor_sf": {"source": footprints + " (footprint area)", "synthetic": False},
                     "height_m": {"source": footprints, "synthetic": False}, "rba_sf": {"source": assessor, "synthetic": False} if rba else only,
                     "year_built": {"source": assessor, "synthetic": False} if year else only,
                     **{k: only for k in ("building_class", "construction", "sprinklers", "elevators", "parking_spaces", "parking_ratio", "percent_leased", "owner", "property_manager", "year_renovated")}}
    site = {"schema": "sandtable.site/1", "placeholder": False,
            "source_note": "Public context of the live demo building, built from open data by scripts/build_site.py. Not CoStar data. Private: lives under data/live/, never committed.",
            "property": prop, "field_sources": field_sources, "tenants": tenants, "available_spaces": [],
            "footprint": {"type": "Polygon", "coordinates": [[[round(q[0], 7), round(q[1], 7)] for q in v["ring"]]]},
            "frame": {"centre": {"lat": round(from_m(lat0, lon0, *centre)[0], 7), "lon": round(from_m(lat0, lon0, *centre)[1], 7)},
                      "length_m": round(length, 2), "width_m": round(width, 2), "bearing_deg": round(bearing, 2),
                      "note": "minimum-area bounding rectangle of the footprint; bearing is the compass bearing of the long axis"},
            "sides": v["sides"],
            "context": {"hydrants": v["hydrants"][:8], "fire_stations": v["stations"],
                        "apparatus_positions": [{"id": "E1", "label": "Engine 1", "lat": round(v["engine"][0], 6), "lon": round(v["engine"][1], 6), "side": "A",
                                                 "source": "inferred: nearest point of the address street to the building", "synthetic": True}],
                        "entrances": [{"id": "main", "label": "Main entrance (assumed mid-facade, side A)", "lat": round(v["entrance"][0], 6), "lon": round(v["entrance"][1], 6),
                                       "side": "A", "leads_to": "stairA", "source": "inferred from the footprint; not surveyed", "synthetic": True}],
                        "permits": []},
            "cross_checks": [], "open_data": list(srcs.values()), "unavailable": unavailable}
    OUT_SITE.parent.mkdir(parents=True, exist_ok=True)
    OUT_SITE.write_text(json.dumps(site, indent=1))
    print(f"public context written to {OUT_SITE.parent.relative_to(ROOT) if OUT_SITE.is_relative_to(ROOT) else OUT_SITE.parent}: footprint {length:.0f} x {width:.0f} m, "
          f"{len(v['hydrants'])} hydrants, {len(v['stations'])} stations, {len(v['images'])} images, {len(tenants)} open-data tenant rows")
    if unavailable:
        print("UNAVAILABLE:", *[u.split(":")[0] for u in unavailable], sep="\n  ")


def configure(argv: list[str]) -> None:
    """--address/--city/--out (or --from-demo): a live building's public context, written under a `live` directory."""
    global FIXTURE, CITY, POINT, HOUSE_NO, STREET_KEY, STREET_LABEL, ADDRESS, CACHE, OUT_SITE, AERIAL_DIR, SOURCES
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--address")
    ap.add_argument("--city", choices=["la", "houston"])
    ap.add_argument("--out")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--from-demo", action="store_true", help="read address, lat and lon from data/live/demo_building.json")
    a = ap.parse_args(argv)
    if a.from_demo:
        b = demo_building() or {}
        if not b.get("address"):
            raise SystemExit("data/live/demo_building.json is missing or has no address")
        a.address, a.lat, a.lon = a.address or b["address"], a.lat or b.get("lat"), a.lon or b.get("lon")
        a.city = a.city or ("houston" if (a.lat or 34) < 31.5 else "la")
        a.out = a.out or str(ROOT / "data" / "live" / "site" / slug(a.address))
    if not a.address:
        return
    if not a.city or not a.out:
        raise SystemExit("--address needs --city la|houston and --out data/live/site/<slug>/")
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    if not is_private(out / "site.json"):
        raise SystemExit("refusing: --out must be under a directory named 'live' (e.g. data/live/site/<slug>/): the address is the sponsor's building")
    m = re.match(r"\s*(\d+)[A-Za-z]?\s+(.+)", a.address.split(",")[0])
    if not m:
        raise SystemExit("the address must start with a house number")
    street = re.sub(r"\b(N|S|E|W|NORTH|SOUTH|EAST|WEST)\b\.?\s+", "", m.group(2).upper(), count=1)
    street = re.sub(r"\s+(ST|STREET|AVE|AVENUE|AV|DR|DRIVE|BLVD|BOULEVARD|RD|ROAD|LN|LANE|PL|PLACE|CT|COURT|PKWY|PARKWAY|WAY|HWY|FWY|CIR|TER|SQ)\.?(\s.*)?$", "", street)
    FIXTURE, CITY, ADDRESS = False, a.city, a.address.split(",")[0].strip()
    HOUSE_NO, STREET_KEY, STREET_LABEL = m.group(1), street.strip(), m.group(2).strip()
    POINT = (a.lat, a.lon) if a.lat is not None and a.lon is not None else None
    CACHE, OUT_SITE, AERIAL_DIR = out / "_open", out / "site.json", out / "aerial"
    if CITY == "houston":
        SOURCES = HOUSTON_SOURCES


if __name__ == "__main__":
    configure([x for x in sys.argv[1:]])
    main()
