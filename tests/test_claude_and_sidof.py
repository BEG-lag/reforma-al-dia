import json
import stat
from datetime import date

import httpx
import pytest

from reforma_al_dia.claude import Claude, ClaudeError, UsageLimitError, cli_args
from reforma_al_dia.edits import SCHEMA
from reforma_al_dia.sidof import Sidof, SidofError

OK = {"edits": [], "divisions": [], "needs_review": [{"article": "Artículo 1.", "reason": "x"}]}


def fake_claude(tmp_path, payload: dict, name: str = "claude"):
    """A stand-in executable that records its arguments and stdin and prints ``payload``."""
    log = tmp_path / f"{name}.args.json"
    script = tmp_path / name
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'cwd': os.getcwd()}}, open({str(log)!r}, 'w'))\n"
        f"print({json.dumps(json.dumps(payload))})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def test_cli_disables_tools_mcp_and_personal_config():
    args = cli_args("SYSTEM", SCHEMA)
    joined = " ".join(args)
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    for flag in ("-p", "--strict-mcp-config", "--safe-mode", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--output-format") + 1] == "json"
    assert "--mcp-config" not in joined
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA


def test_call_parses_structured_output_and_caches(tmp_path):
    binary, log = fake_claude(
        tmp_path,
        {
            "structured_output": OK,
            "result": "",
            "duration_ms": 1200,
            "total_cost_usd": 0.01,
            "modelUsage": {"claude-opus-5-5": {}},
        },
    )
    claude = Claude(tmp_path / "cache", binary=str(binary))
    first = claude.run("SYSTEM", "PROMPT", SCHEMA)
    assert first.data == OK and not first.cached and first.model == "claude-opus-5-5"
    seen = json.loads(log.read_text())
    assert seen["stdin"] == "PROMPT"
    assert seen["cwd"] != str(tmp_path)  # a fresh empty folder, not the project
    log.unlink()
    second = claude.run("SYSTEM", "PROMPT", SCHEMA)
    assert second.cached and second.data == OK
    assert not log.exists()  # served from the cache, the CLI was not called
    assert claude.calls == 1


def test_offline_mode_never_calls(tmp_path):
    with pytest.raises(ClaudeError):
        Claude(tmp_path, binary="/nonexistent", offline=True).run("S", "P", SCHEMA)


def test_text_result_is_accepted_when_there_is_no_structured_output(tmp_path):
    binary, _ = fake_claude(tmp_path, {"result": "```json\n" + json.dumps(OK) + "\n```"})
    assert Claude(tmp_path / "c", binary=str(binary)).run("S", "P", SCHEMA).data == OK


def test_usage_limit_is_not_retried(tmp_path):
    binary, _ = fake_claude(tmp_path, {"is_error": True, "result": "You've hit your 5-hour limit"})
    claude = Claude(tmp_path / "c", binary=str(binary), retries=3)
    with pytest.raises(UsageLimitError):
        claude.run("S", "P", SCHEMA)
    assert claude.calls == 1


def test_sidof_sends_a_wildcard_accept_header_and_retries(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(503)
        assert "*/*" in request.headers["accept"]
        return httpx.Response(
            200, json={"NotasMatutinas": [{"codNota": 1, "titulo": "Decreto x", "orden": 1}], "NotasVespertinas": []}
        )

    client = Sidof(tmp_path, min_interval=0, transport=httpx.MockTransport(handler))
    client.retries = 2
    import reforma_al_dia.sidof as mod

    sleep = mod.time.sleep
    mod.time.sleep = lambda s: None
    try:
        notes = client.day_index(date(2020, 1, 2))
    finally:
        mod.time.sleep = sleep
    assert [n.code for n in notes] == [1]
    assert (tmp_path / "notas__02-01-2020.json").exists()
    with pytest.raises(SidofError):
        Sidof(tmp_path, offline=True).note(123)
