"""One LLM wrapper, three backends (see the bench conventions):

    anthropic   official SDK, key from ANTHROPIC_API_KEY (what runs at the event)
    claude-cli  the headless Claude CLI on this bench (`claude -p ... --output-format json < /dev/null`)
    replay      recorded real responses in fixtures/llm_cache/<sha256 of the canonical request>.json

`LLM_RECORD=1` writes every real response into the cache. A cache file is only ever
written from a real model call; nothing in fixtures/llm_cache/ is hand-written.
The canonical request (tier, system prompt, messages, image content hash) does not
depend on the backend or on any absolute path, so a recording made here replays anywhere.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .. import config

CLI_MODEL = {"fast": "haiku", "main": "sonnet", "hard": "opus"}
CLI_TIMEOUT_S = 180.0


class ReplayMiss(Exception):
    pass


class LLMError(Exception):
    pass


def canonical_request(tier: str, system: str, messages: list[dict], image_sha: str | None) -> str:
    return json.dumps({"tier": tier, "system": system, "messages": messages, "image_sha": image_sha}, sort_keys=True, ensure_ascii=True)


def request_key(tier: str, system: str, messages: list[dict], image_sha: str | None) -> str:
    return hashlib.sha256(canonical_request(tier, system, messages, image_sha).encode()).hexdigest()


def file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LLM:
    def __init__(self, backend: str | None = None, record: bool | None = None, cache_dir: Path | None = None):
        self.backend = backend or config.LLM_BACKEND
        self.record = config.LLM_RECORD if record is None else record
        self._cache_dir = cache_dir
        self.refresh = os.environ.get("LLM_REFRESH") == "1"
        self.calls = {"replay": 0, "live": 0, "miss": 0}

    @property
    def cache_dir(self) -> Path:
        """The replay cache, resolved per call: fixtures/llm_cache/ in fixture mode, data/live/llm_cache/ once any sponsor layer is live."""
        return self._cache_dir or config.llm_cache_dir()

    @cache_dir.setter
    def cache_dir(self, value: Path | None) -> None:
        self._cache_dir = value

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    async def complete(self, system: str, messages: list[dict], tier: str = "main",
                       image_path: str | None = None, image_sha: str | None = None) -> tuple[str, dict]:
        """Returns (text, meta). meta["source"] is "replay" for a cache hit, else the live backend name."""
        if image_path and not image_sha:
            image_sha = file_sha(image_path)
        key = request_key(tier, system, messages, image_sha)
        path = self.cache_path(key)
        if path.exists() and not (self.refresh and self.backend != "replay"):
            self.calls["replay"] += 1
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached["response"], {"source": "replay", "key": key, "recorded_with": cached.get("backend"), "model": cached.get("model")}
        if self.backend == "replay":
            self.calls["miss"] += 1
            miss_dir = os.environ.get("SANDTABLE_MISS_DIR")      # debugging aid: keep the request that had no recording, to diff it
            if miss_dir:
                try:
                    out = Path(miss_dir) / f"{key}.json"
                    config.guard_write(out)                       # live mode: only under data/live/
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps({"tier": tier, "request": {"system": system, "messages": messages, "image_sha": image_sha}}), encoding="utf-8")
                except Exception:
                    pass
            raise ReplayMiss(key)
        if self.backend == "claude-cli":
            text, model = await self._cli(system, messages, tier, image_path)
        elif self.backend == "anthropic":
            text, model = await self._anthropic(system, messages, tier, image_path)
        else:
            raise LLMError(f"unknown LLM_BACKEND {self.backend!r}")
        self.calls["live"] += 1
        if self.record:
            config.guard_write(path)                  # live mode: recordings only under data/live/, fixtures/llm_cache/ is read-only
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"key": key, "backend": self.backend, "model": model, "tier": tier,
                                        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                        "request": {"system": system, "messages": messages, "image_sha": image_sha},
                                        "response": text}, indent=1), encoding="utf-8")
        return text, {"source": self.backend, "key": key, "model": model}

    # ---- claude-cli -------------------------------------------------------------------
    async def _cli(self, system: str, messages: list[dict], tier: str, image_path: str | None) -> tuple[str, str]:
        exe = shutil.which("claude")
        if not exe:
            raise LLMError("the claude CLI is not installed on this machine")
        if len(messages) == 1:
            prompt = messages[0]["content"]
        else:
            parts = [f"[{m['role'].upper()}]\n{m['content']}" for m in messages]
            prompt = "\n\n".join(parts) + "\n\nContinue as the assistant: reply with your next JSON object only."
        args = [exe, "-p", prompt, "--model", CLI_MODEL[tier], "--output-format", "json", "--system-prompt", system,
                "--no-session-persistence", "--strict-mcp-config"]
        if image_path:
            abs_path = str(Path(image_path).resolve())
            args[2] = f"First read the image file at {abs_path} with the Read tool (it is the image the request refers to). Then answer.\n\n{prompt}"
            args += ["--tools", "Read", "--allowedTools", "Read", "--add-dir", str(Path(abs_path).parent)]
        else:
            args += ["--tools", ""]
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), CLI_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            raise LLMError(f"claude CLI timed out after {CLI_TIMEOUT_S:.0f} s")
        if proc.returncode != 0:
            raise LLMError(f"claude CLI exited {proc.returncode}: {err.decode(errors='replace')[:300]}")
        try:
            data = json.loads(out.decode())
        except json.JSONDecodeError:
            raise LLMError("claude CLI returned non-JSON output")
        if data.get("is_error") or "result" not in data:
            raise LLMError(f"claude CLI error: {str(data.get('result'))[:300]}")
        model = next(iter(data.get("modelUsage") or {}), CLI_MODEL[tier])
        return data["result"], model

    # ---- anthropic SDK ------------------------------------------------------------------
    async def _anthropic(self, system: str, messages: list[dict], tier: str, image_path: str | None) -> tuple[str, str]:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("the anthropic package is not installed") from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set")
        model = {"fast": config.LLM_MODEL_FAST, "main": config.LLM_MODEL_MAIN, "hard": config.LLM_MODEL_HARD}[tier]
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        if image_path:
            data = base64.standard_b64encode(Path(image_path).read_bytes()).decode()
            media = "image/png" if str(image_path).lower().endswith(".png") else "image/jpeg"
            msgs[0] = {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                                                   {"type": "text", "text": messages[0]["content"]}]}
        client = anthropic.AsyncAnthropic()
        try:
            resp = await client.messages.create(model=model, max_tokens=3000, system=system, messages=msgs)
        except Exception as exc:
            raise LLMError(f"anthropic API error: {type(exc).__name__}: {exc}") from exc
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text"), model


def extract_json(text: str) -> dict:
    """First balanced JSON object in the text (models sometimes wrap it in prose or fences)."""
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
