"""Sponsor-layer adapters: fixture bundle shapes and honesty labels, coordinate round trips, live adapters against a mocked transport."""
import json

import pytest

from sandtable import config
from sandtable.adapters import AdapterError, build_provider
from sandtable.adapters.costar_live import COSTAR_FIELD_MAP, CoStarLive
from sandtable.adapters.matterport_live import MatterportLive, normalize_model
from sandtable.adapters.vexcel_live import VexcelLive


@pytest.fixture(scope="module")
def p():
    return build_provider()


def test_fixture_bundle_has_the_contract_shapes(p):
    site, twin, aerial = p.site(), p.twin_raw(), p.aerial()
    assert site["schema"] == "sandtable.site/1" and twin["schema"] == "sandtable.twin/1" and aerial["schema"] == "sandtable.aerial/1"
    assert p.modes == {"costar": "fixture", "matterport": "fixture", "vexcel": "fixture"}
    for key in ("property", "field_sources", "tenants", "footprint", "sides", "context"):
        assert key in site
    for key in ("floors", "rooms", "walls", "doors", "stairs", "sweeps", "tags", "graph", "georef", "exercise_floor"):
        assert key in twin
    assert any(i["id"] == "ortho" for i in aerial["images"])
    for im in aerial["images"]:
        assert (config.ROOT / im["path"]).is_file()


def test_fixture_is_labelled_honestly(p):
    site, twin, aerial, labels = p.site(), p.twin_raw(), p.aerial(), p.labels()
    assert site["placeholder"] is False and "Not CoStar" in site["source_note"]
    assert twin["source"]["synthetic"] is True and "not a scan" in twin["source"]["note"]
    assert all(not i["synthetic"] and "NAIP" in i["source"] for i in aerial["images"])        # real public imagery, named
    for layer in ("costar", "matterport", "vexcel"):
        assert labels[layer]["mode"] == "fixture" and labels[layer]["real_sponsor_data"] is False and "Not " in labels[layer]["text"]
    prop = site["property"]
    for field in ("owner", "property_manager", "sprinklers", "percent_leased"):                # CoStar-only fields are empty, never invented
        assert prop[field] is None
    for field, value in prop.items():
        if value is not None and field in site["field_sources"]:
            assert site["field_sources"][field]["source"], field


def test_tenant_names_are_withheld_and_hazards_marked_as_inferred(p):
    tenants = p.costar.list_tenants()
    assert len(tenants) >= 8
    for t in tenants:
        assert t["name"].endswith("(name withheld)") and t["synthetic"] is False and t["naics"]
        if t["life_hazard"] or t["hazmat"]:
            assert "inferred" in t["hazard_basis"]
    floor3 = p.costar.list_tenants(3)
    assert floor3 and all(t["floor"] == 3 for t in floor3)


def test_record_joins_to_the_twin_by_suite_number(p):
    twin = p.twin_raw()
    floor_no = int(next(f["label"] for f in twin["floors"] if f["id"] == twin["exercise_floor"]))
    suites = {r["suite"] for r in twin["rooms"] if r["kind"] == "suite"}
    assert {t["suite"] for t in p.costar.list_tenants(floor_no)} <= suites


def test_world_pixel_round_trip_and_building_is_inside_the_image(p):
    prop = p.site()["property"]
    for im in p.aerial()["images"]:
        px, py = p.vexcel.world_to_pixel(im["id"], prop["lat"], prop["lon"])
        assert 0.3 * im["width"] < px < 0.7 * im["width"] and 0.3 * im["height"] < py < 0.7 * im["height"]
        lat, lon = p.vexcel.pixel_to_world(im["id"], px, py)
        assert abs(lat - prop["lat"]) < 1e-9 and abs(lon - prop["lon"]) < 1e-9
    assert p.vexcel.find_imagery(prop["lat"], prop["lon"]) and p.vexcel.find_imagery(0.0, 0.0) == []
    assert p.vexcel.get_structure(prop["lat"], prop["lon"])["height_m"] == prop["height_m"]


def test_fixture_writes_go_to_local_files_with_deterministic_ids(p):
    p.matterport.clear_overlay()
    p.costar.clear_corrections()
    t1 = p.matterport.add_tag("f3", [1.0, 2.0, 1.2], "note one")
    t2 = p.matterport.add_tag("f3", [1.0, 2.0, 1.2], "note two")
    assert (t1["id"], t2["id"]) == ("obs-1", "obs-2") and "not sent to Matterport" in t1["status"]
    assert [t["id"] for t in p.matterport.list_tags()][-2:] == ["obs-1", "obs-2"]
    c = p.costar.file_correction("area", 1, 2, {"why": "test"})
    assert c["id"] == "corr-1" and "not sent to CoStar" in c["status"]
    p.matterport.clear_overlay()
    p.costar.clear_corrections()
    assert not any(str(t["id"]).startswith("obs-") for t in p.matterport.list_tags())


def test_live_mode_without_keys_fails_loudly(monkeypatch):
    for k in ("VEXCEL_API_KEY", "MATTERPORT_TOKEN_ID", "MATTERPORT_TOKEN_SECRET", "MATTERPORT_MODEL_ID", "COSTAR_API_KEY", "COSTAR_BASE_URL", "COSTAR_PROPERTY_ID"):
        monkeypatch.delenv(k, raising=False)
    for kwargs, name in (({"vexcel_mode": "live"}, "VEXCEL_API_KEY"), ({"matterport_mode": "live"}, "MATTERPORT_TOKEN_ID"), ({"costar_mode": "live"}, "COSTAR_API_KEY")):
        with pytest.raises(AdapterError) as err:
            build_provider(**kwargs)
        assert name in str(err.value)


# --- Vexcel live adapter, against an invented service whose payloads have the shapes the live API returns ---
# Every value below is made up (place, collection names, image names, attributes). No live response is stored in the repository.

VX_LAT, VX_LON = 40.0, -100.0
VX_FOOT = [(VX_LON - 0.0002, VX_LAT - 0.00015), (VX_LON + 0.0002, VX_LAT - 0.00015), (VX_LON + 0.0002, VX_LAT + 0.00015),
           (VX_LON - 0.0002, VX_LAT + 0.00015), (VX_LON - 0.0002, VX_LAT - 0.00015)]
VX_OTHER = [(VX_LON + 0.001, VX_LAT + 0.001), (VX_LON + 0.0012, VX_LAT + 0.001), (VX_LON + 0.0012, VX_LAT + 0.0012), (VX_LON + 0.001, VX_LAT + 0.001)]
VX_KEY = "invented-test-key-0123456789"


def _vx_poly(ring):
    return "POLYGON ((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"


def _vx_image(w, h, xmp: dict) -> bytes:
    import io
    from PIL import Image
    packet = "<x:xmpmeta><rdf:RDF><rdf:Description>" + "".join(f"<Vexcel:{k}>{v}</Vexcel:{k}>" for k, v in xmp.items()) + "</rdf:Description></rdf:RDF></x:xmpmeta>"
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 90)).save(buf, "JPEG", xmp=packet.encode())
    return buf.getvalue()


def _vx_structure(ring, height, material, condition, centroid):
    return {"footprint.wkt": _vx_poly(ring), "footprint.area": 1234.4, "roof-characteristics.material.class": material, "roof-characteristics.shape.value": "flat",
            "roof-characteristics.condition": float(condition), "roof-characteristics.condition.precise-value": condition + 0.2,
            "roof-characteristics.centroid.wkt": f"POINT ({centroid[0]} {centroid[1]})", "roof-characteristics.height": height, "roof-characteristics.elevation": 100.0,
            "roof-objects.ac-unit.count": 3, "roof-objects.roof-vent.count": 7, "roof-objects.skylight.count": 0, "roof-objects.solar.value": True,
            "roof-defects.tarp.detected": False, "roof-defects.discoloration.detected": True, "building-type.class": "commercial",
            "ground-elevation.value": 99.5, "metadata.ortho-date-estimate": "2030-01-02T00:00:00", "metadata.collection": "zz-test-2030", "metadata.gsd": 0.07}


class FakeVexcel:
    """Answers like the live service (status, content type, body) with invented values; records every request."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        J = lambda obj: (200, "application/json", json.dumps(obj).encode())
        fc = lambda props: J({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": q} for q in props]})
        if path == "/ortho/collections":
            return fc([{"layer": "wide-area", "collection": "zz-wide-2030", "last-capture-date": "2030-03-01T10:00:00", "min-gsd": 0.12, "avg-gsd": 0.15, "max-zoom": 20, "product-type": "final-ortho"},
                       {"layer": "urban-r", "collection": "zz-test_rf01-2030", "last-capture-date": "2030-06-01T10:00:00", "min-gsd": 0.05, "avg-gsd": 0.065, "max-zoom": 21, "product-type": "final-ortho"},
                       {"layer": "urban", "collection": "zz-test-2030", "last-capture-date": "2030-01-05T10:00:00", "min-gsd": 0.07, "avg-gsd": 0.075, "max-zoom": 21, "product-type": "final-ortho"},
                       {"layer": "graysky", "collection": "zz-event", "last-capture-date": "2031-01-01T10:00:00", "min-gsd": 0.1, "max-zoom": 20, "product-type": "quick-ortho"}])
        if path == "/oriented/collections":
            return fc([{"layer": "urban", "collection": "zz-test-2030", "product-type": "oblique-north", "last-capture-date": "2030-01-05T10:00:00"}])
        if path == "/damage/collections":
            return (404, "application/json", b'{"status":404,"message":"No results available"}')
        if path == "/3d/collections":
            return (403, "application/json", b'{"message":"forbidden"}')
        if path.endswith("/collections"):
            return fc([{"collection": "zz-test-2030", "product-type": path.split("/")[1]}])
        if path == "/ortho/extract":
            half = float(params["bbox-dimensions"].split(",")[0]) / 2
            dlat, dlon = half / 111320.0, half / (111320.0 * 0.766)
            # the service snaps the extent: it is NOT the one asked for (shifted east and south)
            w, s, e, n = VX_LON - dlon + 5e-6, VX_LAT - dlat - 3e-6, VX_LON + dlon + 5e-6, VX_LAT + dlat - 3e-6
            ring = [(w, s), (w, n), (e, n), (e, s), (w, s)]
            return (200, "image/jpeg", _vx_image(200, 180, {"geometry": _vx_poly(ring), "layer": params["layer"], "collection": params.get("collection", "?"),
                                                             "min-gsd": "0.05", "first-capture-date": "Sat Jun 01 10:00:00 UTC 2030",
                                                             "last-capture-date": "Sun Jun 02 10:00:00 UTC 2030", "pixel-bbox": "1000,2000,200,180"}))
        if path == "/structure/extract":       # the first feature is NOT the one containing the point
            return fc([_vx_structure(VX_OTHER, 30.0, "metal", 2, (VX_LON + 0.0011, VX_LAT + 0.0011)), _vx_structure(VX_FOOT, 21.3, "membrane", 4, (VX_LON, VX_LAT))])
        if path == "/property/extract":
            return fc([{"vehicles.detected": True, "vehicles.count": 12, "pool.detected": False, "hardscapes.percent": 40.0, "parcel-tree-cover.percent": 5.0,
                        "structure.count": 1, "request-information.inside-parcel": True, "parcel-source": "invented", "structures": []}])
        if path in ("/dsm/elevation", "/dtm/elevation"):
            z = 121.3 if path == "/dsm/elevation" else 100.0
            return J({"wkt": f"POINT Z({VX_LON} {VX_LAT} {z})", "srid": 4326, "collection": ["zz-test-2030"]})
        if path == "/oriented/query":
            return fc([{"image-name": f"zz~img~{d}_0001", "product-type": f"oblique-{name}", "layer": "urban", "capture-date": "2030-01-0%dT10:00:00" % (i + 1),
                        "collection": "zz-test-2030"} for i, (d, name) in enumerate((("N", "north"), ("E", "east"), ("S", "south"), ("W", "west")))])
        if path == "/oriented/extract":
            assert params["crop"] == "bbox"             # without it, naming an image returns the whole source frame
            return (200, "image/jpeg", _vx_image(120, 90, {"image-name": params["image-name"], "product-type": params["product-type"], "capture-date": "2030-01-01 10:00:00.0",
                                                            "collection": "zz-test-2030", "layer": "urban", "min-gsd": "0.06", "pixel-bbox": "500,700,240,180"}))
        if path == "/oriented/transform-points":
            n = params["wkt"].count(",") + 1
            return J({"srid": 4326, "points": [{"x": 1240.0 + 10 * i, "y": 1580.0} for i in range(n)], "dem": ["vexcel-dsm"]})
        return (400, "application/json", b'{"message":"unexpected"}')


@pytest.fixture()
def vx(tmp_path):
    fake = FakeVexcel()
    v = VexcelLive(VX_KEY, {"property": {"lat": VX_LAT, "lon": VX_LON}}, transport=fake, out_root=tmp_path / "live" / "vexcel")
    return v, fake


def test_vexcel_collections_parse_and_the_newest_blue_sky_wins():
    from sandtable.adapters.vexcel_live import best_collection, parse_collections
    _s, _c, body = FakeVexcel()("/ortho/collections", {})
    cols = parse_collections(json.loads(body))
    assert [c["layer"] for c in cols] == ["wide-area", "urban-r", "urban", "graysky"] and cols[1]["min_gsd_m"] == 0.05 and cols[1]["max_zoom"] == 21
    assert best_collection(cols)["collection"] == "zz-test-2030"            # same capture year as the refresh: urban preferred; graysky never
    assert best_collection(cols[:2])["collection"] == "zz-test_rf01-2030"
    assert best_collection([]) is None


def test_vexcel_ortho_bounds_are_read_from_the_image_not_assumed(vx):
    from sandtable.adapters.vexcel_live import mercator_bbox
    v, _fake = vx
    ortho = next(i for i in v.aerial()["images"] if i["id"] == "ortho")
    assert ortho["bbox_basis"].startswith("XMP") and ortho["width"] == 200 and ortho["height"] == 180
    asked_west = VX_LON - 125.0 / (111320.0 * 0.766)
    assert ortho["bbox"][0] == pytest.approx(asked_west + 5e-6, abs=1e-9)    # the returned extent, not the requested one
    w, s, e, n = ortho["bbox"]
    assert v.world_to_pixel("ortho", n, w) == pytest.approx([0, 0]) and v.world_to_pixel("ortho", s, e) == pytest.approx([200, 180])
    lat, lon = v.pixel_to_world("ortho", *v.world_to_pixel("ortho", VX_LAT, VX_LON))
    assert lat == pytest.approx(VX_LAT, abs=1e-10) and lon == pytest.approx(VX_LON, abs=1e-10)
    assert ortho["capture_date"] == "2030-06-02" and ortho["synthetic"] is False and ortho["source"].startswith("Vexcel ortho")
    assert ortho["url"].startswith("/live/vexcel/site_") and v.get_image("ortho")["abs_path"].endswith("ortho.jpg")
    assert v.find_imagery(VX_LAT, VX_LON) and not v.find_imagery(VX_LAT + 1, VX_LON)
    assert mercator_bbox("0,0,256,256", 1) == pytest.approx([-180.0, 0.0, 0.0, 85.0511287798], abs=1e-6)   # a quarter of the world at zoom 1


def test_vexcel_obliques_are_named_by_look_direction_and_mapped_through_the_crop(vx):
    from sandtable.adapters.vexcel_live import crop_to_frame, frame_to_crop
    v, fake = vx
    obl = {i["id"]: i for i in v.aerial()["images"] if i["kind"] == "oblique"}
    assert set(obl) == {"oblique_n", "oblique_e", "oblique_s", "oblique_w"}
    assert (obl["oblique_n"]["heading_deg"], obl["oblique_n"]["shows_face"], obl["oblique_w"]["shows_face"]) == (0.0, "south", "east")
    assert obl["oblique_e"]["image_name"] == "zz~img~E_0001" and obl["oblique_s"]["capture_date"] == "2030-01-01"
    crop = obl["oblique_n"]["crop"]
    ds = crop["downsample"]
    assert crop["x0"] == 500 and crop["sx"] == pytest.approx(0.5)
    # source frame (1240, 1580) -> pixel of the downsample level -> minus the crop origin -> scaled to the 120 x 90 file
    expect = [(1240 / 2 ** ds - 500) * 0.5, (1580 / 2 ** ds - 700) * 0.5]
    assert v.world_to_pixel("oblique_n", VX_LAT, VX_LON) == pytest.approx(expect)
    tp = [c for c in fake.calls if c[0] == "/oriented/transform-points"][-1][1]
    assert tp["operation"] == "world-2-pixel" and tp["image-name"] == "zz~img~N_0001"
    assert crop_to_frame(crop, *frame_to_crop(crop, 1240.0, 1580.0)) == pytest.approx([1240.0, 1580.0])
    before = len(fake.calls)
    many = v.world_to_pixel_many("oblique_n", [[VX_LAT, VX_LON], [VX_LAT + 1e-4, VX_LON], [VX_LAT, VX_LON + 1e-4]])
    assert len(many) == 3 and len(fake.calls) == before + 1 and fake.calls[-1][1]["wkt"].startswith("LINESTRING(")   # one call for all


def test_vexcel_structure_attributes_map_from_the_flat_dotted_keys(vx):
    v, _fake = vx
    s = v.get_structure(VX_LAT, VX_LON)
    assert s["roof_material"] == "membrane" and s["height_m"] == 21.3            # the structure containing the point, not the first one
    assert s["roof_condition"] == "good" and s["roof_condition_score"] == pytest.approx(4.2) and s["roof_shape"] == "flat"
    assert s["roof_objects"]["ac_units"] == 3 and s["roof_objects"]["roof_vents"] == 7 and s["roof_objects"]["solar"] is True
    assert s["roof_defects"] == ["discoloration"] and s["building_type"] == "commercial" and s["footprint_area_m2"] == 1234
    assert s["footprint"]["type"] == "Polygon" and len(s["footprint"]["coordinates"][0]) == 5 and s["roof_centroid"] == [VX_LAT, VX_LON]
    assert s["capture_date"] == "2030-01-02" and s["synthetic"] is False
    e = v.elevation(VX_LAT, VX_LON, s["roof_centroid"])
    assert e["height_m"] == 21.3 and e["surface_m"] == 121.3 and e["terrain_m"] == 100.0
    pr = v.get_property(VX_LAT, VX_LON)
    assert pr["vehicles"] == 12 and pr["pool"] is False and pr["inside_parcel"] is True
    cov = v.coverage(VX_LAT, VX_LON)
    assert cov["layers"]["damage"]["status"] == "no coverage here" and cov["layers"]["3d"]["status"].startswith("not licensed")
    assert cov["layers"]["structure"]["status"] == "available"


def test_vexcel_responses_are_cached_on_disk_including_negative_answers(vx):
    from sandtable.adapters.vexcel_live import VexcelClient, VexcelError
    v, fake = vx
    v.aerial()
    n = len(fake.calls)
    again = VexcelLive(VX_KEY, {"property": {"lat": VX_LAT, "lon": VX_LON}}, transport=fake, out_root=v.root)
    again.fetch_site(VX_LAT, VX_LON)                                     # everything comes from the cache
    assert n > 10 and len(fake.calls) == n and again.client.stats["network"] == 0
    assert again.coverage(VX_LAT, VX_LON)["layers"]["3d"]["status"].startswith("not licensed") and len(fake.calls) == n   # the 403 is cached too
    offline = VexcelClient(VX_KEY, cache_dir=v.root / "cache", transport=fake, offline=True)
    with pytest.raises(VexcelError):
        offline.get_json("/ortho/collections", {"wkt": "POINT(1 2)"})
    flaky = VexcelClient(VX_KEY, cache_dir=v.root / "cache", transport=lambda p, q: (503, "text/plain", b"busy"))
    with pytest.raises(VexcelError):
        flaky.get_json("/ortho/collections", {"wkt": "POINT(3 4)"})
    assert not any("POINT(3 4)" in f.read_text() for f in (v.root / "cache").rglob("*.req.json"))   # transient errors are not cached


def test_vexcel_key_goes_in_the_header_only_never_in_a_url_or_on_disk(vx, monkeypatch):
    import httpx
    from sandtable.adapters.vexcel_live import VexcelClient
    v, _fake = vx
    v.aerial()
    for f in v.root.rglob("*"):
        if f.is_file():
            assert VX_KEY.encode() not in f.read_bytes() and VX_KEY not in str(f), f
    seen = []

    class Resp:
        status_code, headers, content = 200, {"content-type": "application/json"}, b'{"type": "FeatureCollection", "features": []}'

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append((url, params, headers))
        return Resp()
    monkeypatch.setattr(httpx, "get", fake_get)
    c = VexcelClient(VX_KEY, cache_dir=v.root / "cache2")
    c.get_json("/ortho/collections", {"wkt": "POINT(-100 40)", "srid": 4326})
    url, params, headers = seen[0]
    assert headers == {"X-API-KEY": VX_KEY} and VX_KEY not in url and VX_KEY not in json.dumps(params)
    assert VX_KEY not in repr(c) and VX_KEY not in repr(v)
    with pytest.raises(ValueError):
        c.get_json("/ortho/collections", {"wkt": VX_KEY})               # refuses to put the key in a query parameter
    assert not any(VX_KEY in f.read_text() for f in (v.root / "cache2").rglob("*.req.json"))


# --- Matterport: invented payloads shaped like the live Model API (structure only; no real ids, names or places) ---

MP_OX, MP_OY = -20.0, 5.0          # the invented model frame is offset, so the shift to the building frame is exercised


def mp_model():
    """A one-floor suite scan: an open room (6 x 6 m) and an office (4 x 6 m) behind a wall with a doorway, a doorway in the
    outer wall (the suite entrance), a Void region, four sweeps with Matterport-style neighbour lists."""
    v = {"v1": (0, 0), "v2": (6, 0), "v3": (10, 0), "v4": (10, 6), "v5": (6, 6), "v6": (0, 6), "v7": (0, 7), "v8": (1, 7)}
    door = lambda t: [{"id": "o" + t, "type": "doorway", "label": "door-pivot", "width": 1.0, "relativeCenter": 0.5}]  # noqa: E731
    edges = [("e1", "v1", "v2", door("1")), ("e2", "v2", "v3", []), ("e3", "v3", "v4", [{"id": "w1", "type": "window", "width": 1.2, "relativeCenter": 0.5}]),
             ("e4", "v4", "v5", []), ("e5", "v5", "v6", []), ("e6", "v6", "v1", []), ("e7", "v2", "v5", door("7")), ("e8", "v6", "v7", []), ("e9", "v7", "v8", [])]
    floor = {"id": "flA", "label": "Floor 1", "sequence": 0, "dimensions": {"areaFloor": 60.0},
             "vertices": [{"id": k, "position": {"x": x + MP_OX, "y": y + MP_OY}} for k, (x, y) in v.items()],
             "edges": [{"id": e, "type": "wall", "thickness": 0.12, "vertices": [{"id": a}, {"id": b}], "openings": o} for e, a, b, o in edges]}
    rooms = [{"id": "rOpen", "label": "", "tags": ["other", "unfurnished"], "keywords": [], "classifications": [{"label": "Other"}], "floor": {"id": "flA"},
              "dimensions": {"areaFloor": 36.0, "width": 6.0, "depth": 6.0}, "boundary": {"edges": [{"id": e} for e in ("e1", "e7", "e5", "e6")]}, "holes": []},
             {"id": "rOff", "label": None, "tags": ["office"], "keywords": [], "classifications": [{"label": "Office"}], "floor": {"id": "flA"},
              "dimensions": {"areaFloor": 24.0, "height": 2.7}, "boundary": {"edges": [{"id": e} for e in ("e2", "e3", "e4", "e7")]}, "holes": []},
             {"id": "rVoid", "label": None, "tags": [], "classifications": [{"label": "Void"}], "floor": {"id": "flA"}, "boundary": {"edges": [{"id": "e8"}]}}]
    pts = {"s1": (1.5, 1.5, "rOpen"), "s2": (3.0, 3.0, "rOpen"), "s3": (4.5, 4.5, "rOpen"), "s4": (8.0, 1.5, "rOff")}
    nb = {"s1": ["s2", "s4"], "s2": ["s1", "s3"], "s3": ["s2", "s4"], "s4": ["s3", "s1"]}       # s1-s4 passes through the wall, away from the door
    locs = [{"id": k, "label": k, "floor": {"id": "flA"}, "room": {"id": r}, "position": {"x": x + MP_OX, "y": y + MP_OY, "z": 1.5},
             "neighbors": nb[k], "tags": ["showcase"]} for k, (x, y, r) in pts.items()]
    return {"id": "invented-model", "name": "Invented suite", "state": "active", "visibility": "private",
            "address": {"address": "1 Example Way, Testville", "locality": "Testville", "administrativeArea": "ZZ"},
            "geolocation": {"lat": "34.0000500", "long": "-117.9999000"},
            "geocoordinates": {"source": "processing", "latitude": 34.0, "longitude": -118.0, "rotation": {"x": 0, "y": 0, "z": 0, "w": 1}},
            "floors": [floor], "rooms": rooms, "locations": locs,
            "mattertags": [{"id": "t1", "label": "Fire extinguisher", "floor": {"id": "flA"}, "anchorPosition": {"x": 2 + MP_OX, "y": 5 + MP_OY, "z": 1.0}}]}


def mp_geo(point):
    """Invented model->geo conversion: +x east, +y north, model (0, 0) at 34.0 N, 118.0 W."""
    import math
    return {"lat": str(34.0 + point["y"] / 111320.0), "long": str(-118.0 + point["x"] / (111320.0 * math.cos(math.radians(34.0)))), "status": "COMPLETE"}


def mp_transport(calls):
    def transport(query, variables):
        calls.append((query, variables))
        if "addMattertag" in query:
            return {"addMattertag": {"id": "written-tag"}}
        if "__type" in query:
            fields = {"Mutation": {"fields": [{"name": "addMattertag"}]},
                      "MattertagDetails": {"inputFields": [{"name": f} for f in ("floorId", "enabled", "color", "label", "description", "anchorPosition",
                                                                                   "stemEnabled", "stemNormal", "stemLength", "position")]}}
            return {"__type": {"name": variables["name"], **fields[variables["name"]]}}
        if "geoLocationOf" in query:
            return {"model": {"geocoordinates": {"geoLocationOf": mp_geo(variables["point"])}}}
        if "floorplans" in query:
            return {"model": {"assets": {"floorplans": [{"id": "fp1", "format": "png", "floor": {"id": "flA"}, "width": 800, "height": 600}]}}}
        if "getModelForTwin" in query:
            return {"model": mp_model()}
        raise AssertionError(query[:80])
    return transport


def test_matterport_normalisation_uses_the_scan_geometry():
    from sandtable.spatial import Twin
    calls = []
    t = mp_transport(calls)
    samples = [{"model": [x, y], **{k: float(v) if k != "status" else v for k, v in mp_geo({"x": x, "y": y}).items()}} for x, y in ((MP_OX + 4, MP_OY + 3), (MP_OX + 14, MP_OY + 3), (MP_OX + 4, MP_OY + 13))]
    samples = [{"model": s["model"], "lat": s["lat"], "lon": s["long"], "status": s["status"]} for s in samples]
    twin = normalize_model(t("query getModelForTwin", {})["model"], geo_samples=samples, storey=7, suite="700")
    assert twin["schema"] == "sandtable.twin/1" and twin["source"]["kind"] == "matterport-live" and twin["source"]["synthetic"] is False
    assert [f["label"] for f in twin["floors"]] == ["7"] and twin["exercise_floor"] == "flA"          # the real storey, so tenants join by floor
    rooms = {r["id"]: r for r in twin["rooms"]}
    assert set(rooms) == {"rOpen", "rOff"} and twin["source"]["counts"]["void_regions_skipped"] == 1
    assert rooms["rOff"]["kind"] == "suite" and rooms["rOff"]["label"] == "Office" and rooms["rOpen"]["label"] == "Room"   # noise tags never name a room
    assert all(r["suite"] == "700" for r in rooms.values())
    assert abs(abs(_mp_area(rooms["rOpen"]["polygon"])) - 36.0) < 1e-6 and abs(abs(_mp_area(rooms["rOff"]["polygon"])) - 24.0) < 1e-6   # real boundaries
    assert len(twin["walls"]) == 9 and sorted(d["kind"] for d in twin["doors"]) == ["door", "exit"]
    c = twin["source"]["counts"]
    assert c["sweep_links_kept"] == 3 and c["sweep_links_dropped_wall"] == 1                           # Matterport's neighbours, minus the one through a wall
    assert twin["stairs"][0]["id"] == "stairA" and twin["stairs"][0]["label"] == "Suite entrance"
    assert "suite entrance" in twin["source"]["note"].lower()
    T = Twin(twin)
    for rid in rooms:
        assert T.route(rid, "exit")["ok"]
    loc = T.locate(*T.room_centre("rOff"))
    assert loc["room"] == "rOff" and loc["sweep"] == "s4"
    r = T.route("rOff", "exit")
    assert r["doors"] and r["length_m"] > 5
    assert twin["georef"]["bearing_deg"] == pytest.approx(90.0, abs=0.05)
    dx, dy = twin["_frame"]["dx"], twin["_frame"]["dy"]
    lat, lon = T.to_geo(5.0, 2.0)
    want = mp_geo({"x": 5.0 + dx, "y": 2.0 + dy})
    assert lat == pytest.approx(float(want["lat"]), abs=2e-7) and lon == pytest.approx(float(want["long"]), abs=2e-7)
    assert twin["tags"][0]["kind"] == "extinguisher"


def _mp_area(poly):
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def test_matterport_live_caches_responses_and_the_twin(tmp_path):
    calls = []
    m = MatterportLive("id", "secret", "invented-model", transport=mp_transport(calls), cache_dir=tmp_path, storey=3)
    twin = m.get_model()
    assert calls and twin["floors"][0]["label"] == "3" and (tmp_path / "twin_invented-model.json").exists()
    assert list((tmp_path / "cache").glob("*.json"))                                                   # every response is on disk

    def refuse(query, variables):
        raise AssertionError("the network was used although everything is cached")
    again = MatterportLive("id", "secret", "invented-model", transport=refuse, cache_dir=tmp_path, storey=3)
    assert again.get_model()["rooms"] == twin["rooms"]                                                  # read from the twin cache
    (tmp_path / "twin_invented-model.json").unlink()
    rebuilt = MatterportLive("id", "secret", "invented-model", transport=refuse, cache_dir=tmp_path, storey=3).get_model()
    assert rebuilt["rooms"] == twin["rooms"]                                                            # re-normalised from cached responses
    n = len(calls)
    MatterportLive("id", "secret", "invented-model", transport=mp_transport(calls), cache_dir=tmp_path, refresh=True, storey=3).get_model()
    assert len(calls) > n                                                                               # refresh refetches


def test_matterport_add_tag_is_a_dry_run_unless_enabled(tmp_path):
    calls = []
    m = MatterportLive("id", "secret", "invented-model", transport=mp_transport(calls), cache_dir=tmp_path)
    twin = m.get_model()
    tag = m.add_tag("flA", [3.0, 2.0, 1.2], "Ceiling collapse", "note")
    assert not any("addMattertag" in q and "mutation" in q for q, _v in calls)                          # nothing written to the model
    assert tag["id"] == "dryrun-1" and "dry run" in tag["status"] and "NOT sent" in tag["status"] and "schema check found" not in tag["status"]
    log = json.loads((tmp_path / "tags_dryrun_invented-model.json").read_text())
    v = log[0]["variables"]
    assert v["floorId"] == "flA" and v["anchorPositionX"] == pytest.approx(3.0 + twin["_frame"]["dx"]) and v["anchorPositionZ"] == pytest.approx(1.2)
    assert MatterportLive("id", "secret", "invented-model", transport=mp_transport([]), cache_dir=tmp_path).list_tags()[-1]["id"] == "dryrun-1"
    w = MatterportLive("id", "secret", "invented-model", transport=mp_transport(calls), cache_dir=tmp_path, dry_run=False)
    out = w.add_tag("flA", [3.0, 2.0, 1.2], "Ceiling collapse")
    q, v = calls[-1]
    assert "addMattertag(modelId: $modelId, field: id" in q and out["id"] == "written-tag" and "written to the Matterport model" in out["status"]


def test_matterport_live_from_env_never_writes_tags_by_default(monkeypatch):
    for k, val in (("MATTERPORT_TOKEN_ID", "x"), ("MATTERPORT_TOKEN_SECRET", "y"), ("MATTERPORT_MODEL_ID", "invented-model")):
        monkeypatch.setenv(k, val)
    monkeypatch.delenv("MATTERPORT_WRITE_TAGS", raising=False)
    assert MatterportLive.from_env().dry_run is True


def test_costar_live_maps_toolkit_fields_through_one_table(p):
    raw = {"propertyId": "P-1", "propertyName": "Test Tower", "address": {"street": "1 Main St", "city": "Houston", "state": "TX", "postalCode": "77002"},
           "location": {"latitude": 29.76, "longitude": -95.36}, "numberOfStories": 12, "rentableBuildingArea": 250000, "yearBuilt": 1982,
           "constructionMaterial": "Steel", "sprinklers": "Wet", "propertyManager": {"name": "PM Co", "phone": "000"}}
    posted = []

    def transport(method, path, body=None):
        if method == "POST":
            posted.append((path, body))
            return {"id": "RR-9"}
        if path.endswith("/tenants"):
            return {"items": [{"tenantId": "T1", "tenantName": "Acme", "suite": "300", "floor": 3, "squareFeetOccupied": 4000, "industry": "Law"}]}
        if path.endswith("/available-spaces"):
            return [{"suite": "310", "floor": 3, "squareFeetAvailable": 2000, "spaceStatus": "Vacant"}]
        return raw

    c = CoStarLive("key", "https://example.invalid/api", "P-1", transport=transport, open_bundle=p.site())
    site = c.site()
    assert site["property"]["stories"] == 12 and site["property"]["sprinklers"] == "Wet" and site["property"]["address"] == "1 Main St"
    assert set(COSTAR_FIELD_MAP) <= set(site["property"]) and site["field_sources"]["sprinklers"] == {"source": "CoStar", "synthetic": False}
    assert c.list_tenants(3)[0]["name"] == "Acme" and c.list_available_space()[0]["suite"] == "310"
    assert site["context"]["hydrants"] and site["footprint"]["type"] == "Polygon"                 # public context is kept from the open bundle
    out = c.file_correction("stories", 12, 13, {"why": "test"})
    assert out["id"] == "RR-9" and posted[0][0].endswith("/research-requests") and out["status"].startswith("sent to CoStar")
