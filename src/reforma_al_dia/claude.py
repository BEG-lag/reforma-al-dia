"""Call Claude headless through the owner's Claude Code CLI, with tools disabled and a cache.

The call is ``claude -p --output-format json --json-schema ...`` with every way
for the model to act on the machine switched off:

* ``--tools ""``: no built-in tools (no Bash, no file edits, no web);
* ``--strict-mcp-config`` without ``--mcp-config``: no MCP servers;
* ``--safe-mode``: no CLAUDE.md, hooks, skills, plugins or custom agents, so the
  owner's personal instructions (for example, to write notes after each task)
  cannot fire inside a pipeline run;
* ``--setting-sources ""``, ``--disable-slash-commands``, ``--no-session-persistence``;
* a working directory that is a fresh empty temporary folder.

The model can only return text, and the text must validate against the edit
schema. Responses are cached by a hash of everything that shapes them, so a
rerun of the backtest costs nothing and CI never calls Claude.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class ClaudeError(RuntimeError):
    pass


class UsageLimitError(ClaudeError):
    """The account hit its usage limit: retrying now is pointless."""


_LIMIT = re.compile(r"hit your .{0,40}limit|usage limit|rate limit|quota", re.IGNORECASE)


@dataclass
class Response:
    data: dict
    cached: bool
    duration_ms: int
    cost_usd: float
    model: str
    key: str


def cli_args(system: str, schema: dict, *, model: str | None = None, effort: str | None = None) -> list[str]:
    args = [
        "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        "--tools", "",
        "--strict-mcp-config",
        "--safe-mode",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", system,
    ]  # fmt: skip
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    return args


class Claude:
    def __init__(
        self,
        cache_dir: Path,
        *,
        binary: str = "claude",
        model: str | None = None,
        effort: str | None = None,
        timeout: float = 1200.0,
        offline: bool = False,
        retries: int = 2,
    ) -> None:
        self.cache_dir = cache_dir
        self.binary = binary
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self.offline = offline
        self.retries = retries
        self.calls = 0  # real calls made by this instance

    def key(self, system: str, prompt: str, schema: dict) -> str:
        blob = json.dumps(
            {
                "model": self.model or "default",
                "effort": self.effort,
                "system": system,
                "prompt": prompt,
                "schema": schema,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def cached(self, system: str, prompt: str, schema: dict) -> bool:
        return self._path(self.key(system, prompt, schema)).exists()

    def run(self, system: str, prompt: str, schema: dict) -> Response:
        key = self.key(system, prompt, schema)
        path = self._path(key)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return self._response(raw, key, cached=True)
        if self.offline:
            raise ClaudeError(f"no recorded response for request {key[:12]} and offline mode is on")

        error = ""
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(5 * attempt)
            try:
                raw = self._call(system, prompt, schema)
                response = self._response(raw, key, cached=False)
            except UsageLimitError:
                raise
            except ClaudeError as exc:
                error = str(exc)
                log.warning("claude call failed (attempt %d): %s", attempt + 1, error)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            keep = {
                k: raw.get(k)
                for k in (
                    "structured_output",
                    "result",
                    "duration_ms",
                    "total_cost_usd",
                    "modelUsage",
                    "num_turns",
                    "is_error",
                    "subtype",
                )
            }
            path.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            return response
        raise ClaudeError(error)

    def _call(self, system: str, prompt: str, schema: dict) -> dict:
        args = [self.binary, *cli_args(system, schema, model=self.model, effort=self.effort)]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_ENTRYPOINT")}
        with tempfile.TemporaryDirectory(prefix="reforma-al-dia-") as cwd:
            started = time.monotonic()
            try:
                proc = subprocess.run(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    env=env,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ClaudeError(f"claude timed out after {self.timeout:.0f} s") from exc
            except FileNotFoundError as exc:
                raise ClaudeError(f"Claude Code CLI not found: {self.binary}") from exc
        self.calls += 1
        log.info("claude call finished in %.1f s (exit %d)", time.monotonic() - started, proc.returncode)
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeError(
                f"claude returned non-JSON output (exit {proc.returncode}): {proc.stderr[-400:] or proc.stdout[:400]}"
            ) from exc
        if raw.get("is_error"):
            message = str(raw.get("result"))[:400]
            if _LIMIT.search(message):
                raise UsageLimitError(f"usage limit: {message}")
            raise ClaudeError(f"claude reported an error: {message}")
        return raw

    def _response(self, raw: dict, key: str, *, cached: bool) -> Response:
        data = raw.get("structured_output")
        if not isinstance(data, dict):
            data = _json_from_text(str(raw.get("result") or ""))
        if not isinstance(data, dict) or "edits" not in data:
            raise ClaudeError("the response does not contain the edits object")
        models = list((raw.get("modelUsage") or {}).keys())
        return Response(
            data=data,
            cached=cached,
            duration_ms=int(raw.get("duration_ms") or 0),
            cost_usd=float(raw.get("total_cost_usd") or 0.0),
            model=models[0] if models else (self.model or "default"),
            key=key,
        )


def _json_from_text(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
