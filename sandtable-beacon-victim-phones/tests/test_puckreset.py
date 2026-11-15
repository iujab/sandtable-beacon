"""Restarting a puck's board (beacon/puckreset.py, POST /api/pucks/restart). No hardware: OpenOCD is faked."""
from __future__ import annotations

import subprocess

import pytest

from beacon import puckreset as pr


class FakeOpenOCD:
    """Answers `mdw 0x100000a4 1` with the board's address and records every reset."""

    def __init__(self, boards: dict[str, str], reset_rc: int = 0):
        self.boards, self.reset_rc, self.calls = boards, reset_rc, []

    def __call__(self, serial, commands, timeout=20.0):
        self.calls.append((serial, tuple(commands)))
        if commands == ["reset run"]:
            return subprocess.CompletedProcess([], self.reset_rc, "", "" if self.reset_rc == 0 else "Error: no device found")
        return subprocess.CompletedProcess([], 0, "", f"0x100000a4: {self.boards[serial].lower()}\n")


@pytest.fixture(autouse=True)
def _fresh_cache():
    pr._cache.update(t=0.0, serials=(), map={})
    yield
    pr._cache.update(t=0.0, serials=(), map={})


def test_the_address_comes_from_the_board_name_with_or_without_the_apps_suffix():
    assert pr.addr_from_accessory("DWM3001CDK (1BDB5E43)") == "1BDB5E43"
    assert pr.addr_from_accessory("DWM3001CDK (088d9bf3)#1900") == "088D9BF3"
    assert pr.addr_from_accessory("Some phone") is None and pr.addr_from_accessory(None) is None


def test_jlinks_are_found_in_sysfs_by_vendor(tmp_path):
    for name, vendor, serial in (("1-1.1", "1366", "000760226620"), ("1-1.4", "1366", "000760226574"), ("1-1.2", "2109", "x")):
        d = tmp_path / name
        d.mkdir()
        (d / "idVendor").write_text(vendor + "\n")
        (d / "serial").write_text(serial + "\n")
    assert pr.jlink_serials(str(tmp_path)) == ["000760226574", "000760226620"]


def test_restart_resets_only_the_board_with_that_address():
    ocd = FakeOpenOCD({"000760226620": "088D9BF3", "000760226574": "1BDB5E43"})
    out = pr.restart("DWM3001CDK (1BDB5E43)#1900", run=ocd, serials=["000760226574", "000760226620"])
    assert out == {"ok": True, "dry_run": False, "address": "1BDB5E43", "jlink": "000760226574"}
    assert [c for c in ocd.calls if c[1] == ("reset run",)] == [("000760226574", ("reset run",))]


def test_dry_run_resolves_the_board_and_touches_nothing():
    ocd = FakeOpenOCD({"000760226620": "088D9BF3", "000760226574": "1BDB5E43"})
    out = pr.restart("DWM3001CDK (088D9BF3)", dry_run=True, run=ocd, serials=["000760226574", "000760226620"])
    assert out["dry_run"] is True and out["jlink"] == "000760226620"
    assert not any(c[1] == ("reset run",) for c in ocd.calls)


def test_the_address_map_is_cached_and_rescanned_when_a_board_is_missing():
    ocd = FakeOpenOCD({"000760226620": "088D9BF3", "000760226574": "1BDB5E43"})
    serials = ["000760226574", "000760226620"]
    pr.restart("DWM3001CDK (088D9BF3)", dry_run=True, run=ocd, serials=serials)
    reads = len(ocd.calls)
    pr.restart("DWM3001CDK (1BDB5E43)", dry_run=True, run=ocd, serials=serials)
    assert len(ocd.calls) == reads                                  # served from the cache: no SWD reads
    with pytest.raises(ValueError, match="no attached board has the address"):
        pr.restart("DWM3001CDK (DEADBEEF)", run=ocd, serials=serials)
    assert pr._cache["map"] == {}                                   # cleared, so the next call rescans


def test_a_failed_reset_is_reported_not_hidden():
    ocd = FakeOpenOCD({"000760226574": "1BDB5E43"}, reset_rc=1)
    out = pr.restart("DWM3001CDK (1BDB5E43)", run=ocd, serials=["000760226574"])
    assert out["ok"] is False and "no device found" in out["detail"]


def test_no_board_attached_or_no_address_is_a_clear_error():
    with pytest.raises(ValueError, match="no J-Link"):
        pr.restart("DWM3001CDK (1BDB5E43)", run=FakeOpenOCD({}), serials=[])
    with pytest.raises(ValueError, match="carries no address"):
        pr.restart("P2", run=FakeOpenOCD({}), serials=["1"])


def test_restart_route(make_client, monkeypatch):
    c = make_client("live")
    c.post("/api/pucks", json={"room": "r310", "pucks": [
        {"id": "P1", "x": 9.15, "y": 6.7, "accessory": "DWM3001CDK (088D9BF3)#1900"},
        {"id": "P2", "x": 9.15, "y": 2.7}]})
    seen = []
    monkeypatch.setattr(pr, "restart", lambda acc, dry_run=False: seen.append((acc, dry_run)) or
                        {"ok": True, "dry_run": dry_run, "address": "088D9BF3", "jlink": "000760226620"})
    r = c.post("/api/pucks/restart", json={"puck": "P1", "dry_run": True})
    assert r.status_code == 200 and r.json()["jlink"] == "000760226620" and seen == [("DWM3001CDK (088D9BF3)#1900", True)]
    assert c.post("/api/pucks/restart", json={"puck": "P9"}).status_code == 400          # not placed
    assert c.post("/api/pucks/restart", json={"puck": "P2"}).status_code == 409          # no board name tied to it
    monkeypatch.setattr(pr, "restart", lambda acc, dry_run=False: (_ for _ in ()).throw(subprocess.TimeoutExpired("openocd", 20)))
    assert c.post("/api/pucks/restart", json={"puck": "P1"}).status_code == 503


def test_restart_route_refuses_simulated_pucks(make_client):
    c = make_client("sim")
    c.post("/api/pucks", json={"n": 2})
    assert c.post("/api/pucks/restart", json={"puck": "P1"}).status_code in (400, 409)
