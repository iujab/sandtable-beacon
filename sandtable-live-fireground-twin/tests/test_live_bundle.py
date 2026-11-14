"""The private laptop bundle: pack and unpack round-trip with identical hashes, exclusions, and the refusals."""
import importlib.util
import json
import zipfile

import pytest

from sandtable import config

spec = importlib.util.spec_from_file_location("pack_live_bundle", config.ROOT / "scripts" / "pack_live_bundle.py")
plb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plb)


def made_up_live_tree(live):
    """An invented data/live tree (no real values)."""
    from sandtable.adapters.vexcel_live import site_slug
    files = {"demo_building.json": json.dumps({"address": "1 Invented Way", "lat": 10.5, "lon": -20.25, "model_id": "madeUpModel", "property_id": 1}),
             "site/1-invented-way/site.json": "{}", "site/1-invented-way/aerial/ortho.png": "png",
             "costar/_index/property_1.json": "{}", "matterport/twin_madeUpModel.json": "{}", "matterport/cache/a.json": "{}",
             f"vexcel/{site_slug(10.5, -20.25)}/ortho.jpg": "jpg", "vexcel/cache/ortho-extract/b.bin": "bin",
             "llm_cache/" + "0" * 64 + ".json": "{}", "beacon_bundle/web/index.html": "<html>"}
    skipped = {"matterport/board.log": "log", "figures/shot.png": "png", "annotated_ortho.png": "png", "vexcel/site_1.00000_2.00000/ortho.jpg": "other site",
               f"vexcel/{site_slug(10.5, -20.25)}/_review/x.jpg": "scratch", "r5_demo.log": "log"}
    for rel, text in {**files, **skipped}.items():
        (live / rel).parent.mkdir(parents=True, exist_ok=True)
        (live / rel).write_text(text)
    return files, skipped


def test_pack_and_unpack_round_trip_with_identical_hashes(tmp_path):
    src = tmp_path / "a" / "live"
    files, skipped = made_up_live_tree(src)
    archive = tmp_path / "carry" / "live" / "bundle.zip"
    m = plb.pack(src, archive)
    assert {f["path"] for f in m["files"]} == set(files) and m["contains_sponsor_data"] is True and "Never commit" in m["warning"]
    assert m["parts"]["llm_cache"]["rebuild"] == "only from this bundle" and m["parts"]["building"]["files"] == 1
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
    assert "MANIFEST.json" in names and not any(rel in n for n in names for rel in skipped)
    dest = tmp_path / "b" / "live"
    plb.unpack(archive, dest)
    for rel in files:
        assert plb.sha256(dest / rel) == plb.sha256(src / rel)


def test_pack_refuses_key_values_and_public_paths_and_unpack_refuses_non_live(tmp_path, monkeypatch):
    src = tmp_path / "a" / "live"
    made_up_live_tree(src)
    with pytest.raises(SystemExit):
        plb.pack(src, tmp_path / "public" / "bundle.zip")                 # not git-ignored, not under live
    monkeypatch.setenv("INVENTED_API_KEY", "not-a-real-key-value-123")
    (src / "matterport" / "cache" / "leak.json").write_text('{"k": "not-a-real-key-value-123"}')
    with pytest.raises(SystemExit):
        plb.pack(src, tmp_path / "carry" / "live" / "bundle.zip")
    (src / "matterport" / "cache" / "leak.json").unlink()
    archive = tmp_path / "carry" / "live" / "bundle.zip"
    plb.pack(src, archive)
    with pytest.raises(SystemExit):
        plb.unpack(archive, tmp_path / "somewhere")
