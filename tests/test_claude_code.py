"""One fenced `claude -p` call per request, a daily budget kept across runs, and a
stop for the rest of the process once Claude Code reports a usage limit."""

import json
import os
from datetime import date, timedelta

import pytest

from models import ScreenResult
from scoring import claude_code as cc

SCREEN_JSON = json.dumps({"passes": True, "rough_score": 70, "reason": "fit",
                          "german_required": "none", "jd_language": "en"})


def _stdout(result, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": result,
                       "total_cost_usd": 0.001, "duration_ms": 10,
                       "usage": {"input_tokens": 2, "output_tokens": 100}})


@pytest.fixture
def fake(monkeypatch, tmp_path):
    calls = []
    state = {"stdout": _stdout(f"```json\n{SCREEN_JSON}\n```"), "stderr": ""}

    def run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return type("P", (), {"stdout": state["stdout"], "stderr": state["stderr"], "returncode": 0})()

    usage = str(tmp_path / "usage.json")
    monkeypatch.setattr(cc, "usage_path", lambda: usage)
    monkeypatch.setattr(cc, "check_claude_bin", lambda: "claude.exe")
    monkeypatch.setattr(cc.subprocess, "run", run)
    monkeypatch.setattr(cc.config, "LOCAL_DAILY_CALL_BUDGET", 150)
    cc.reset_limit()
    yield type("F", (), {"calls": calls, "state": state, "usage": usage})
    cc.reset_limit()


def test_complete_returns_validated_json(fake):
    out = cc.complete("p", "s", "haiku", ScreenResult)
    assert json.loads(out) == json.loads(SCREEN_JSON)


def test_prose_around_json_raises(fake):
    fake.state["stdout"] = _stdout(f"Here you go: {SCREEN_JSON}")
    with pytest.raises(RuntimeError):
        cc.complete("p", "s", "haiku", ScreenResult)


def test_budget_counts_across_calls_and_dates(fake, monkeypatch):
    monkeypatch.setattr(cc.config, "LOCAL_DAILY_CALL_BUDGET", 2)
    cc.complete("p", "s", "haiku", ScreenResult)
    cc.complete("p", "s", "haiku", ScreenResult)
    with pytest.raises(cc.ClaudeCodeLimitReached):
        cc.complete("p", "s", "haiku", ScreenResult)
    assert len(fake.calls) == 2

    cc.reset_limit()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with open(fake.usage, "w", encoding="utf-8") as f:
        json.dump({"date": yesterday, "calls": 2}, f)
    cc.complete("p", "s", "haiku", ScreenResult)
    with open(fake.usage, encoding="utf-8") as f:
        assert json.load(f) == {"date": date.today().isoformat(), "calls": 1}


def test_usage_limit_message_stops_the_process(fake):
    fake.state["stdout"] = _stdout("Claude AI usage limit reached|1731000000", is_error=True)
    with pytest.raises(cc.ClaudeCodeLimitReached):
        cc.complete("p", "s", "haiku", ScreenResult)
    with pytest.raises(cc.ClaudeCodeLimitReached):
        cc.complete("p", "s", "haiku", ScreenResult)
    assert len(fake.calls) == 1


def test_missing_claude_raises_runtime_error(fake, monkeypatch):
    def missing():
        raise FileNotFoundError("claude.exe")
    monkeypatch.setattr(cc, "check_claude_bin", missing)
    with pytest.raises(RuntimeError):
        cc.complete("p", "s", "haiku", ScreenResult)


def test_env_disables_thinking_and_drops_keys(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    cc.complete("p", "s", "haiku", ScreenResult)
    call = fake.calls[0]
    assert call["env"]["MAX_THINKING_TOKENS"] == "0"
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert os.path.abspath(call["cwd"]) != os.path.abspath(os.getcwd())
    argv = call["argv"]
    assert argv[argv.index("--effort") + 1] == cc.config.CLAUDE_CODE_EFFORT
    assert argv[argv.index("--tools") + 1] == ""
