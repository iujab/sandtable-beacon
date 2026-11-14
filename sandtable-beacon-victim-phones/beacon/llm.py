"""One LLM wrapper, three backends (the bench conventions):

    anthropic   the official SDK, key from ANTHROPIC_API_KEY (what runs at the event); default model claude-sonnet-5-5
    claude-cli  the headless Claude CLI (`claude -p "<prompt>" --model sonnet --output-format json < /dev/null`)
    replay      recorded real responses in fixtures/llm_cache/<sha256 of the canonical request>.json

`LLM_BACKEND` picks one (default `replay`). `LLM_RECORD=1` writes every real response into the cache.
A cache file is only ever written from a real model call: nothing in fixtures/llm_cache/ is hand-written.
The canonical request (tier, system prompt, messages) depends neither on the backend nor on any path,
so a recording made on this machine replays anywhere.

Pattern copied from Sandtable's command agent (`sandtable-live-fireground-twin/sandtable/agent/llm.py`,
sibling project, lane L01), with credit; adapted to read its settings from the environment here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import config

BACKENDS = ("replay", "claude-cli", "anthropic")
CACHE_DIR = config.FIXTURES / "llm_cache"
CLI_MODEL = {"fast": "haiku", "main": "sonnet", "hard": "opus"}
API_MODEL = {"fast": os.environ.get("LLM_MODEL_FAST", "claude-haiku-5-5"),
             "main": os.environ.get("LLM_MODEL_MAIN", "claude-sonnet-5-5"),
             "hard": os.environ.get("LLM_MODEL_HARD", "claude-opus-5-5")}
CLI_TIMEOUT_S = float(os.environ.get("LLM_CLI_TIMEOUT_S", "180"))
MAX_TOKENS = 3000


class ReplayMiss(Exception):
    """No recorded response for this exact request, and the backend is replay."""


class LLMError(Exception):
    """The live backend failed (not installed, no key, timed out, returned garbage)."""


def backend_from_env() -> str:
    b = (os.environ.get("LLM_BACKEND") or "replay").strip().lower()
    return b if b in BACKENDS else "replay"


def canonical_request(tier: str, system: str, messages: list[dict]) -> str:
    return json.dumps({"tier": tier, "system": system, "messages": messages}, sort_keys=True, ensure_ascii=True)


def request_key(tier: str, system: str, messages: list[dict]) -> str:
    return hashlib.sha256(canonical_request(tier, system, messages).encode("utf-8")).hexdigest()


class LLM:
    def __init__(self, backend: str | None = None, record: bool | None = None, cache_dir: Path | None = None):
        self.backend = (backend or backend_from_env()).strip().lower()
        if self.backend not in BACKENDS:
            raise ValueError(f"unknown LLM_BACKEND {self.backend!r}: one of {BACKENDS}")
        self.record = (os.environ.get("LLM_RECORD") == "1") if record is None else bool(record)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        self.refresh = os.environ.get("LLM_REFRESH") == "1"          # live backends only: ignore the cache, call the model
        self.calls = {"replay": 0, "live": 0, "miss": 0}

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    async def complete(self, system: str, messages: list[dict], tier: str = "main") -> tuple[str, dict]:
        """(text, meta). meta["source"] is "replay" for a cache hit, otherwise the live backend's name."""
        key = request_key(tier, system, messages)
        path = self.cache_path(key)
        if path.is_file() and not (self.refresh and self.backend != "replay"):
            self.calls["replay"] += 1
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached["response"], {"source": "replay", "key": key, "recorded_with": cached.get("backend"), "model": cached.get("model")}
        if self.backend == "replay":
            self.calls["miss"] += 1
            raise ReplayMiss(key)
        if self.backend == "claude-cli":
            text, model = await self._cli(system, messages, tier)
        else:
            text, model = await self._anthropic(system, messages, tier)
        self.calls["live"] += 1
        if self.record:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "backend": self.backend, "model": model, "tier": tier,
                                       "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                       "note": "real model output recorded by beacon/llm.py; never hand-written",
                                       "request": {"system": system, "messages": messages},
                                       "response": text}, indent=1), encoding="utf-8")
            tmp.replace(path)
        return text, {"source": self.backend, "key": key, "model": model}

    # ---- claude-cli ---------------------------------------------------------------------
    async def _cli(self, system: str, messages: list[dict], tier: str) -> tuple[str, str]:
        exe = shutil.which("claude")
        if not exe:
            raise LLMError("the claude CLI is not installed on this machine")
        if len(messages) == 1:
            prompt = messages[0]["content"]
        else:
            parts = [f"[{m['role'].upper()}]\n{m['content']}" for m in messages]
            prompt = "\n\n".join(parts) + "\n\nContinue as the assistant: reply with your next JSON object only."
        args = [exe, "-p", prompt, "--model", CLI_MODEL.get(tier, "sonnet"), "--output-format", "json",
                "--system-prompt", system, "--no-session-persistence", "--strict-mcp-config", "--tools", ""]
        # A neutral working directory, so no project instruction file is picked up as extra context.
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, cwd=tempfile.gettempdir())
        try:
            out, err = await asyncio.wait_for(proc.communicate(), CLI_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            raise LLMError(f"claude CLI timed out after {CLI_TIMEOUT_S:.0f} s")
        if proc.returncode != 0:
            raise LLMError(f"claude CLI exited {proc.returncode}: {err.decode(errors='replace')[:300]}")
        try:
            data = json.loads(out.decode("utf-8"))
        except json.JSONDecodeError:
            raise LLMError("claude CLI returned non-JSON output")
        if data.get("is_error") or "result" not in data:
            raise LLMError(f"claude CLI error: {str(data.get('result'))[:300]}")
        model = next(iter(data.get("modelUsage") or {}), CLI_MODEL.get(tier, "sonnet"))
        return data["result"], model

    # ---- anthropic SDK ----------------------------------------------------------------------
    async def _anthropic(self, system: str, messages: list[dict], tier: str) -> tuple[str, str]:
        try:
            import anthropic                                       # optional: the demo runs without it (replay)
        except ImportError as exc:
            raise LLMError("the anthropic package is not installed (pip install anthropic)") from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set")
        model = API_MODEL.get(tier, API_MODEL["main"])
        client = anthropic.AsyncAnthropic()
        try:
            resp = await client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system,
                                                messages=[{"role": m["role"], "content": m["content"]} for m in messages])
        except Exception as exc:
            raise LLMError(f"anthropic API error: {type(exc).__name__}: {exc}") from exc
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text"), model


def extract_json(text: str) -> dict:
    """First balanced JSON object in the text (models sometimes wrap it in prose or a fence)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in the model reply")
