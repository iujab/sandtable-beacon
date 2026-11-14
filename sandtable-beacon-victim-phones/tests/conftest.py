"""Shared fixtures for the W3 tests (session, feeds, server). Offline, no network, no hardware."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beacon import config  # noqa: E402

HAVE_LOCATOR = importlib.util.find_spec("beacon.locate") is not None and importlib.util.find_spec("beacon.track") is not None
HAVE_SIM = importlib.util.find_spec("beacon.sim") is not None


@pytest.fixture(scope="session")
def twin_raw() -> dict:
    return copy.deepcopy(config.twin_raw())


@pytest.fixture(scope="session")
def site_raw() -> dict:
    return copy.deepcopy(config.site_raw())


@pytest.fixture(scope="session")
def sample() -> dict:
    return json.loads(config.SAMPLE_JSON.read_text(encoding="utf-8"))


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """make_client(source) -> a started TestClient on a fresh app (live-mode puck table goes to tmp_path)."""
    from fastapi.testclient import TestClient
    from beacon import server

    monkeypatch.setattr(config, "PUCKS_LIVE_JSON", tmp_path / "pucks_live.json")
    stack = contextlib.ExitStack()

    def make(source: str, **kw):
        app = server.create_app(source=source, **kw)
        return stack.enter_context(TestClient(app))

    yield make
    stack.close()
