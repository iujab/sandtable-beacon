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


def test_vexcel_live_builds_the_documented_requests(tmp_path, p):
    calls = []

    def transport(path, params, binary=False):
        calls.append((path, params))
        if binary:
            return (config.AERIAL_DIR / "ortho.png").read_bytes()
        if path == "/structure/extract":
            return {"features": [{"properties": {"roof-height": 13.5, "footprint-area": 1500, "roof-characteristics": {"roof-material": "membrane", "roof-condition": 4}}}]}
        if path == "/oriented/transform-points":
            return {"points": [{"x": 101.0, "y": 202.0}]}
        return {"collections": [{"collection": "us-ca-los-angeles-2025", "last-capture-date": "2025-06-01", "min-gsd": 0.075}], "images": [{"image-name": "img_1", "capture-date": "2025-06-01"}]}

    v = VexcelLive("test-key", p.site(), transport=transport, out_dir=config.DATA_DIR / "vexcel-test")
    aerial = v.aerial()
    ids = [i["id"] for i in aerial["images"]]
    assert ids[:2] == ["ortho", "ortho_context"] and {"oblique_n", "oblique_e", "oblique_s", "oblique_w"} <= set(ids)
    ortho = next(c for c in calls if c[0] == "/ortho/extract")[1]
    assert ortho["layer"] == "urban" and ortho["wkt"].startswith("POINT(-118.") and ortho["srid"] == "4326" and ortho["bbox-dimensions"] == "250,250"
    assert {c[1]["product-type"] for c in calls if c[0] == "/oriented/extract"} == {"oblique-north", "oblique-east", "oblique-south", "oblique-west"}
    assert aerial["images"][0]["source"].startswith("Vexcel") and aerial["images"][0]["synthetic"] is False
    s = v.get_structure(34.2, -118.6)
    assert s["height_m"] == 13.5 and s["roof_material"] == "membrane"
    assert v.world_to_pixel("oblique_n", 34.2, -118.6) == [101.0, 202.0]
    tp = next(c for c in calls if c[0] == "/oriented/transform-points")[1]
    assert tp["operation"] == "world-2-pixel" and tp["image-name"] == "img_1"
    prop = p.site()["property"]
    px, py = v.world_to_pixel("ortho", prop["lat"], prop["lon"])
    assert abs(px - 625) < 1 and abs(py - 625) < 1


def matterport_payloads():
    locs = [{"id": f"s{i}", "position": {"x": 10 + 3.0 * i, "y": 20.0, "z": 4.5}, "floor": {"id": "fl2"}, "room": {"id": "hall" if i < 4 else "st"}} for i in range(6)]
    locs += [{"id": "g0", "position": {"x": 10.0, "y": 20.0, "z": 0.5}, "floor": {"id": "fl1"}, "room": {"id": "lobby"}}]
    return ({"id": "abc123", "name": "Test model", "publication": {"address": "1 Test St"}}, {"locations": locs},
            {"floors": [{"id": "fl1", "label": "1"}, {"id": "fl2", "label": "2"}],
             "rooms": [{"id": "hall", "label": "Hallway", "dimensions": {"areaFloor": 30.0, "height": 2.7}}, {"id": "st", "label": "Stair A"}, {"id": "lobby", "label": "Lobby"}]},
            {"mattertags": [{"id": "t1", "label": "Standpipe outlet", "floor": {"id": "fl2"}, "anchorPosition": {"x": 12.0, "y": 21.0, "z": 5.0}, "description": ""}]},
            {"geocoordinates": {"latitude": 29.76, "longitude": -95.36, "rotation": {"x": 0, "y": 0, "z": 0, "w": 1}}})


def test_matterport_normalisation_produces_a_usable_twin():
    from sandtable.spatial import Twin
    twin_dict = normalize_model(*matterport_payloads())
    assert twin_dict["schema"] == "sandtable.twin/1" and twin_dict["source"]["kind"] == "matterport-live" and twin_dict["source"]["synthetic"] is False
    assert [f["id"] for f in twin_dict["floors"]] == ["fl1", "fl2"] and twin_dict["exercise_floor"] == "fl2"
    kinds = {r["id"]: r["kind"] for r in twin_dict["rooms"]}
    assert kinds == {"hall": "corridor", "st": "stair", "lobby": "lobby"}
    assert twin_dict["stairs"][0]["id"] == "stairA" and twin_dict["tags"][0]["kind"] == "standpipe"
    twin = Twin(twin_dict)
    r = twin.route(twin_dict["sweeps"][1]["node"], "stairA")
    assert r["ok"] and r["length_m"] > 5
    assert twin.to_geo(0, 0)[0] == pytest.approx(29.76)


def test_matterport_live_sends_the_documented_graphql_and_writes_tags():
    sent = []
    details, sweeps, dims, tags, geo = matterport_payloads()

    def transport(query, variables):
        sent.append((query, variables))
        if "addMattertag" in query:
            return {"addMattertag": {"id": "new-tag"}}
        for needle, payload in (("getModelDetails", details), ("getSweeps", sweeps), ("getDimensions", dims), ("getTags", tags), ("getGeoCoordinates", geo)):
            if needle in query:
                return {"model": payload}
        raise AssertionError(query)

    m = MatterportLive("id", "secret", "abc123", transport=transport)
    twin = m.get_model()
    assert all(v == {"modelId": "abc123"} for _q, v in sent)
    tag = m.add_tag("fl2", [3.0, 2.0, 1.2], "Ceiling collapse", "note")
    q, v = sent[-1]
    assert "addMattertag(modelId: $modelId, field: id" in q and v["floorId"] == "fl2" and v["label"] == "Ceiling collapse"
    assert v["anchorPositionX"] == pytest.approx(3.0 + twin["_frame"]["dx"]) and v["anchorPositionZ"] == pytest.approx(1.2)
    assert tag["id"] == "new-tag" and "Matterport" in tag["status"] and m.list_tags()[-1]["id"] == "new-tag"


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
