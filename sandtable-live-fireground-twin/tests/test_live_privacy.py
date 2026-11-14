"""The privacy guard: once a sponsor layer is live, nothing is written outside a `live` directory.

A fake live provider (the fixture bundle relabelled `live`, no network) runs a pre-plan on the deterministic
fallback policy while every file write of the process is recorded. Also: the fixture replay cache is untouched and
read-only, a live recording lands in the private cache, a failed live start leaves the process in fixture mode, and
no tracked path is under a `live` directory.
"""
import asyncio
import builtins
import io
import os
import subprocess

import pytest

from sandtable import config
from sandtable.adapters import AdapterError, CoStarFixture, MatterportFixture, Provider, VexcelFixture, build_provider
from sandtable.agent import CommandAgent
from sandtable.agent.llm import LLM
from sandtable.incident import Incident
from sandtable.spatial import Twin


class FakeCoStarLive(CoStarFixture):
    mode = "live"


class FakeMatterportLive(MatterportFixture):
    mode = "live"


class FakeVexcelLive(VexcelFixture):
    mode = "live"


@pytest.fixture
def private_root(tmp_path, monkeypatch):
    """A fresh data root for this test; the live switch is undone afterwards so other tests stay in fixture mode."""
    root = tmp_path / "data"
    root.mkdir()
    for name, value in (("DATA_DIR", root), ("DATA_ROOT", root), ("LIVE_DIR", root / "live"),
                        ("TAGS_OVERLAY_JSON", root / "twin_tags_overlay.json"), ("CORRECTIONS_JSON", root / "record_corrections.json"),
                        ("_LIVE_LAYERS", set()), ("REPLAY_PACE_S", 0)):
        monkeypatch.setattr(config, name, value)
    return root


@pytest.fixture
def writes(monkeypatch):
    """Every path this process opens for writing (open, Path.write_*, PIL saves, os.replace targets)."""
    seen = []
    real_open, real_io_open, real_replace = builtins.open, io.open, os.replace

    def rec_open(real):
        def _open(file, mode="r", *a, **k):
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wax+"):
                seen.append(os.fspath(file))
            return real(file, mode, *a, **k)
        return _open

    def rec_replace(src, dst, *a, **k):
        seen.append(os.fspath(dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(builtins, "open", rec_open(real_open))
    monkeypatch.setattr(io, "open", rec_open(real_io_open))
    monkeypatch.setattr(os, "replace", rec_replace)
    return seen


def _cache_state():
    return sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in config.LLM_CACHE_DIR.glob("*"))


def test_preplan_in_live_mode_writes_only_under_live(private_root, writes):
    fixture_cache = _cache_state()
    provider = Provider(FakeCoStarLive(), FakeMatterportLive(), FakeVexcelLive(site=FakeCoStarLive().site()))
    assert config.live_active() and config.live_layers() == ["costar", "matterport", "vexcel"]
    assert config.DATA_DIR == private_root / "live" and config.llm_cache_dir() == private_root / "live" / "llm_cache"

    msgs = []
    twin = Twin(provider.twin_raw())
    inc = Incident(twin, provider, msgs.append, clock=lambda: 1_000_000.0)
    agent = CommandAgent(provider, twin, inc, msgs.append, LLM(backend="replay", record=True))
    assert agent.llm.cache_dir == private_root / "live" / "llm_cache"       # never fixtures/llm_cache in a live process
    plan = asyncio.run(agent.run_preplan())                                 # the private cache is empty: every model call misses
    assert plan["backend"] == "reflex-fallback"
    provider.matterport.add_tag("f3", [1.0, 2.0, 1.2], "observation from the live run")
    provider.costar.file_correction("stories", 3, 4, {"why": "test"})
    agent.tools.annotate_aerial("ortho")

    assert writes, "the run should have written its local files"
    outside = [w for w in writes if not config.is_private(w)]
    assert outside == [], f"written outside a 'live' directory: {outside}"
    assert (private_root / "live" / "twin_tags_overlay.json").is_file() and (private_root / "live" / "record_corrections.json").is_file()
    assert not (private_root / "twin_tags_overlay.json").exists() and not (private_root / "record_corrections.json").exists()
    assert _cache_state() == fixture_cache


def test_live_recordings_go_to_the_private_cache_and_fixtures_are_read_only(private_root, writes):
    class FakeModel(LLM):
        async def _cli(self, system, messages, tier, image_path):
            return '{"answer": "made against live data"}', "fake-model"

    fixture_cache = _cache_state()
    config.enter_live(["costar"])
    llm = FakeModel(backend="claude-cli", record=True)
    text, meta = asyncio.run(llm.complete("system", [{"role": "user", "content": "a prompt about a live building"}]))
    assert meta["source"] == "claude-cli" and (private_root / "live" / "llm_cache" / f"{meta['key']}.json").is_file()
    pinned = FakeModel(backend="claude-cli", record=True, cache_dir=config.LLM_CACHE_DIR)
    with pytest.raises(config.PrivacyError):
        asyncio.run(pinned.complete("system", [{"role": "user", "content": "another live prompt"}]))
    assert _cache_state() == fixture_cache
    assert all(config.is_private(w) for w in writes)


def test_guard_write_and_output_dirs(private_root):
    assert not config.live_active() and config.output_dir("results") == config.ROOT / "results"
    config.guard_write(config.ROOT / "results" / "x.json")                  # fixture mode: allowed
    config.enter_live(["vexcel"])
    for bad in (config.ROOT / "results" / "x.json", config.ROOT / "figures" / "shot.png", config.ROOT / "fixtures" / "llm_cache" / "k.json", private_root / "x.json"):
        with pytest.raises(config.PrivacyError):
            config.guard_write(bad)
    config.guard_write(private_root / "live" / "vexcel" / "ortho.png")
    assert config.output_dir("figures") == private_root / "live" / "figures"
    assert config.private_path("results", "eval.json").parent.is_dir()


def test_a_failed_live_start_leaves_the_process_in_fixture_mode(private_root, monkeypatch):
    monkeypatch.delenv("VEXCEL_API_KEY", raising=False)
    with pytest.raises(AdapterError):
        build_provider(vexcel_mode="live")
    assert not config.live_active() and config.DATA_DIR == private_root and config.llm_cache_dir() == config.LLM_CACHE_DIR


def test_no_tracked_path_is_under_a_live_directory():
    out = subprocess.run(["git", "ls-files"], cwd=config.ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    assert [p for p in out.stdout.splitlines() if "live" in p.split("/")[:-1]] == []


def test_gitignore_keeps_live_data_and_env_files_out():
    out = subprocess.run(["git", "check-ignore", "data/live/x.json", "web/live/x.png", "a/b/live/c.bin", ".env", ".env.local"],
                         cwd=config.ROOT, capture_output=True, text=True)
    if out.returncode not in (0, 1):
        pytest.skip("not a git checkout")
    assert len(out.stdout.split()) == 5
    assert subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=config.ROOT).returncode == 1
