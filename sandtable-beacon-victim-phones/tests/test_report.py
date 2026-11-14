"""The incident report (beacon/report.py) and the agent routes (beacon/agent_routes.py). Offline, replay backend."""
from __future__ import annotations

import copy
import html.parser
import json
import re
import xml.etree.ElementTree as ET

import pytest

from beacon import config, report
from beacon.agent import BeaconAgent
from beacon.llm import LLM


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))


@pytest.fixture
def rep(sample) -> dict:
    return report.build(sample, None, report.Recorder())


def plan_svg(page: str) -> ET.Element:
    m = re.search(r'<svg xmlns="http://www\.w3\.org/2000/svg" viewBox="[^"]*" role="img".*?</svg>', page, re.S)
    assert m, "the plan SVG is missing"
    return ET.fromstring(m.group(0))


def by_phone(svg: ET.Element, pid: str) -> list[ET.Element]:
    return [e for e in svg.iter() if e.get("data-phone") == pid]


# ---------------------------------------------------------------------------- the report object
def test_builds_from_the_sample(rep, sample):
    json.dumps(rep)
    site = config.site_raw()
    assert rep["type"] == "beacon_report"
    assert rep["header"]["address"] == site["property"]["address"]
    assert rep["header"]["construction"] == site["property"]["construction"]
    assert rep["mode"]["stamp"] == "SAMPLE"
    assert "Not CoStar data" in rep["labels"]["record"] and "Not a Matterport scan" in rep["labels"]["twin"]
    assert [p["id"] for p in rep["phones"]] == [p["id"] for p in sample["phones"]]
    assert len(rep["accountability"]["suites"]) == len(sample["accountability"]["suites"])
    assert rep["history"]["source"].startswith("latest state only")
    assert rep["plan"]["room"] == sample["room"]
    assert any("not people" in x for x in rep["limits"]) and "Typed notes are not included" in rep["privacy"]


def test_final_geometry_is_exactly_as_published(rep, sample):
    for p, q in zip(rep["phones"], sample["phones"]):
        f = p["final"]
        assert f["geometry"] == q["geometry"]
        if q["geometry"] == "fix":
            assert (f["x"], f["y"]) == (q["x"], q["y"])
        else:
            assert "x" not in f and "y" not in f
        if q["geometry"] == "ring":
            assert f["ring"]["arcs"] == q["ring"]["arcs"] and f["ring"]["r_m"] == q["ring"]["r_m"]


def test_no_typed_notes_and_no_eids(rep, sample):
    text = json.dumps(rep)
    for p in sample["phones"]:
        if (p.get("report") or {}).get("text"):
            assert p["report"]["text"] not in text
        assert p["eid"] not in text


def test_recorder_keeps_the_geometry_sequence_and_the_timeline(sample):
    rec = report.Recorder()
    ph = copy.deepcopy(next(p for p in sample["phones"] if p["geometry"] == "fix"))
    t0 = 1794679200.0
    for i, g in enumerate(["ring", "candidates", "candidates", "fix"]):
        b = copy.deepcopy(sample)
        q = copy.deepcopy(ph)
        q["geometry"] = g
        if g != "fix":
            q["x"] = q["y"] = None
        b["phones"], b["t"] = [q], t0 + 5 * i
        rec.feed(b)
    rec.feed({"type": "beacon_event", "t": t0 + 1, "name": "phone_new", "id": ph["id"], "text": "Phone heard"})
    rec.feed({"type": "beacon_event", "t": t0 + 1, "name": "phone_new", "id": ph["id"], "text": "Phone heard"})   # duplicate
    r = report.build(b, None, rec)
    seq = r["phones"][0]["sequence"]
    assert [s["geometry"] for s in seq] == ["ring", "candidates", "fix"]
    assert [s["at"] for s in seq] == ["00:00", "00:05", "00:15"]
    assert [e["name"] for e in r["timeline"]] == ["phone_new"]
    assert r["history"]["source"] == "recorded from the stream"
    rec.feed({"type": "reset"})
    assert rec.phones == {} and rec.events == []


# ---------------------------------------------------------------------------- the page
def test_html_parses_and_is_self_contained(rep):
    page = report.to_html(rep)
    parser = html.parser.HTMLParser()
    parser.feed(page)
    parser.close()
    assert page.startswith("<!doctype html>") and "</html>" in page
    assert "<script" not in page and "<link" not in page
    assert not re.search(r'(src|href)="https?://', page)
    assert re.search(r'url\((?!data:|#)', page) is None        # fonts inlined, patterns in-page: nothing fetched
    assert "SAMPLE" in page and "Not CoStar data" in page
    plan_svg(page)


def test_a_ring_phone_has_no_point_marker(rep, sample):
    svg = plan_svg(report.to_html(rep))
    ring = next(p["id"] for p in sample["phones"] if p["geometry"] == "ring")
    els = by_phone(svg, ring)
    assert els and all(e.get("data-geometry") == "ring" for e in els)
    assert not [e for e in els if e.tag.endswith("circle")]
    assert any("ph-ring" in (e.get("class") or "") for e in els)
    fixp = next(p["id"] for p in sample["phones"] if p["geometry"] == "fix")
    assert any("ph-pt" in (e.get("class") or "") for e in by_phone(svg, fixp))
    cand = next(p for p in sample["phones"] if p["geometry"] == "candidates")
    ce = by_phone(svg, cand["id"])
    assert not [e for e in ce if "ph-pt" in (e.get("class") or "")]
    assert len([e for e in ce if "ph-cand" in (e.get("class") or "")]) == len(cand["candidates"])
    out = next(p["id"] for p in sample["phones"] if (p.get("report") or {}).get("safe_outside"))
    assert by_phone(svg, out) == []
    zone = next(p["id"] for p in sample["phones"] if p["geometry"] == "zone")
    assert [e.get("class") for e in by_phone(svg, zone)] == ["ph-zone"]


def test_report_carries_the_agent_plan(sample, tmp_path):
    agent = BeaconAgent(sample, None, None, None, llm=LLM("replay"), record_path=tmp_path / "rc.json", pace_s=0)
    import asyncio
    plan = asyncio.run(agent.run())
    r = report.build(sample, plan, report.Recorder())
    page = report.to_html(r)
    assert r["agent"]["summary"] == plan["summary"]
    for o in plan["orders"]:
        assert html.escape(o["text"]) in page or o["text"] in page


# ---------------------------------------------------------------------------- the routes
def test_routes(sample, tmp_path):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from beacon.agent_routes import register

    agent = BeaconAgent(sample, None, None, None, llm=LLM("replay"), record_path=tmp_path / "rc.json", pace_s=0)
    router = APIRouter()
    rec = register(router, lambda: sample, lambda: agent, recorder=report.Recorder())
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        assert c.get("/api/agent").json() == {"busy": False, "backend": "replay", "plan": None, "log": []}
        r = c.post("/api/agent/run?wait=1", json={"reason": "test"})
        assert r.status_code == 200 and r.json()["plan"]["backend"] in ("replay", "reflex-fallback")   # replay hits: test_agent.py
        st = c.get("/api/agent").json()
        assert st["plan"]["summary"] and st["log"][0]["kind"] == "start"
        agent.busy = True
        assert c.post("/api/agent/run").status_code == 409
        agent.busy = False
        rj = c.get("/api/report").json()
        assert rj["agent"]["run"] == st["plan"]["run"]
        page = c.get("/report.html")
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        assert "Command agent: plan and orders" in page.text
    assert rec.plan is not None
