#!/usr/bin/env python3
"""Build the FIXTURE site bundle from real, keyless open data.

Writes fixtures/building/site.json (the CoStar-shaped record + public context)
and fixtures/aerial/{aerial.json, ortho.png, ortho_context.png}.

Nothing here is CoStar, Matterport or Vexcel data. Every value carries its
source; values that only the sponsor would have are null. Business and person
names are deliberately withheld (suite, floor and NAICS category are kept)
because the bundle is used for a fictional emergency exercise.

Raw responses are cached under data/open/ so the script is re-runnable offline.
Usage: python scripts/build_site.py [--refresh]
"""
from __future__ import annotations

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

# The building: a real three-storey professional (medical office) building in Los Angeles.
HOUSE_NO, STREET_KEY, STREET_LABEL = "7325", "MEDICAL CENTER", "Medical Center Dr"

PARCELS = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
LARIAC = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Dynamic/LARIAC_Buildings_2020/MapServer/0/query"
BUSINESSES = "https://data.lacity.org/resource/6rrh-rzua.json"
TIGER_ROADS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/8/query"
LAFD = "https://maps.lacity.org/lahub/rest/services/Fire_Department/MapServer"
NAIP = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer"

SOURCES = {
    "assessor": {"name": "Los Angeles County Assessor parcel record", "what": "address, year built, building area, use, construction class", "url": PARCELS},
    "lariac": {"name": "LA County LARIAC building outlines (2020 service, 2008 capture)", "what": "footprint, roof height, ground elevation", "url": LARIAC},
    "businesses": {"name": "City of Los Angeles, Listing of Active Businesses", "what": "tenant suite, floor and NAICS category (names withheld)", "url": "https://data.lacity.org/d/6rrh-rzua"},
    "tiger": {"name": "US Census TIGERweb roads", "what": "street geometry for the address side", "url": TIGER_ROADS},
    "lafd": {"name": "City of Los Angeles GeoHub, Fire Department layers", "what": "hydrants (LADWP) and fire stations", "url": LAFD},
    "naip": {"name": "USDA NAIP orthoimagery via the USGS National Map", "what": "aerial imagery (public domain)", "url": NAIP},
}

# Rule table: NAICS prefix -> (life hazard, hazmat). Inferred from the category, not observed.
HAZARD_RULES = [
    ("6220", "hospital outpatient department: patients may be non-ambulatory or under treatment", "medical gases, sharps and biohazard waste likely"),
    ("62121", "dental patients may be sedated or mid-procedure", "medical oxygen and nitrous oxide cylinders likely"),
    ("62151", "few occupants; staff only", "laboratory reagents, biological samples, compressed gases likely"),
    ("62134", "therapy patients with limited mobility", None),
    ("62139", "patients with limited mobility possible", None),
    ("62132", "patients with dilated pupils or impaired vision possible", None),
    ("6219", "ambulatory care: patients may need assistance", "medical gases possible"),
    ("6211", "medical patients, some with limited mobility", "medical oxygen possible"),
    ("6244", "young children who need assisted evacuation", None),
    ("6233", "elderly residents who need assisted evacuation", None),
]


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


def main() -> None:
    unavailable: list[str] = []

    # 1. parcel record ---------------------------------------------------------
    parcel = get(PARCELS, {"where": f"SitusHouseNo='{HOUSE_NO}' AND SitusStreet LIKE '{STREET_KEY}%'", "outFields": "*", "outSR": "4326", "f": "geojson"}, "parcel.json")
    pf = parcel["features"][0]
    pa = pf["properties"]
    pring = pf["geometry"]["coordinates"][0] if pf["geometry"]["type"] == "Polygon" else pf["geometry"]["coordinates"][0][0]
    env = f"{min(p[0] for p in pring)},{min(p[1] for p in pring)},{max(p[0] for p in pring)},{max(p[1] for p in pring)}"
    pc = (sum(p[1] for p in pring) / len(pring), sum(p[0] for p in pring) / len(pring))

    # 2. building footprint and height ------------------------------------------
    blds = get(LARIAC, {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": "4326", "f": "geojson"}, "lariac_buildings.json")

    def centroid(feat):
        r = feat["geometry"]["coordinates"][0]
        return (sum(q[1] for q in r) / len(r), sum(q[0] for q in r) / len(r))

    bld = min(blds["features"], key=lambda f: dist_m(pc, centroid(f)))
    ring = bld["geometry"]["coordinates"][0]
    lat0, lon0 = centroid(bld)
    pts = [to_m(lat0, lon0, q[1], q[0]) for q in ring[:-1]]
    length, width, bearing, centre = min_area_rect(pts)
    height_ft = float(bld["properties"]["HEIGHT"])
    fp_area_m2 = poly_area(pts)

    # 3. tenants: suite, floor and category only ----------------------------------
    rows = get(BUSINESSES, {"$select": "street_address,naics,primary_naics_description,location_start_date,location_end_date",
                            "$where": f"street_address like '{HOUSE_NO} {STREET_KEY}%'", "$limit": "500"}, "businesses.json")
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
            if (f["properties"].get("NAME") or "").lower().startswith("medical center"):
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
    hyd = soft(lambda: get(f"{LAFD}/0/query", {"geometry": f"{lon0},{lat0}", "geometryType": "esriGeometryPoint", "inSR": "4326", "distance": "260", "units": "esriSRUnit_Meter",
                                               "outFields": "*", "outSR": "4326", "f": "geojson"}, "hydrants.json"), "LAFD hydrants", unavailable)
    hydrants = []
    for f in (hyd or {}).get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        props = f["properties"]
        hydrants.append({"id": f"h{len(hydrants) + 1}", "lat": round(lat, 6), "lon": round(lon, 6), "distance_m": round(dist_m((lat0, lon0), (lat, lon))),
                         "size": props.get("SIZE_CODE") or props.get("Size") or props.get("SIZE"), "source": SOURCES["lafd"]["name"], "synthetic": False})
    hydrants.sort(key=lambda h: h["distance_m"])
    for i, h in enumerate(hydrants):
        h["id"] = f"h{i + 1}"
    sta = soft(lambda: get(f"{LAFD}/1/query", {"geometry": f"{lon0},{lat0}", "geometryType": "esriGeometryPoint", "inSR": "4326", "distance": "6000", "units": "esriSRUnit_Meter",
                                               "outFields": "*", "outSR": "4326", "f": "geojson"}, "fire_stations.json"), "LAFD fire stations", unavailable)
    stations = []
    for f in (sta or {}).get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        props = f["properties"]
        label = next((str(props[k]) for k in ("FACILITY_N", "Name", "NAME", "STATION", "FS_CD", "FIRE_STATION") if props.get(k)), "Fire station")
        stations.append({"name": label if "station" in label.lower() else f"LAFD Station {label}", "lat": round(lat, 6), "lon": round(lon, 6),
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
        images.append({"id": image_id, "kind": "ortho", "path": f"fixtures/aerial/{image_id}.png", "bbox": bbox, "width": px, "height": px,
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


if __name__ == "__main__":
    main()
