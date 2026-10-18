"""Auto-apply runner: claim loop, worker subprocess, write-back. Everything is faked."""
import subprocess
import threading
from types import SimpleNamespace

import pytest

from apply import runner
from apply.bundle import BundleError

OK_JSON = '{"result": "READY|https://x/review|stopped before submit", "total_cost_usd": 0.5}'


def _job(jid):
    return {"job_id": jid, "job_title": "DS", "company": "Acme", "location": "Berlin",
            "job_url": "https://x/j", "apply_url": "https://x/apply", "ats_type": "lever",
            "description": "d", "why_me_pitch": "w", "score_breakdown": {}}


class Env:
    def __init__(self):
        self.queue = [_job("j0")]
        self.lock = threading.Lock()
        self.claims = []
        self.results = []
        self.runs = []
        self.reset_arg = None
        self.run_impl = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=OK_JSON, stderr="")


@pytest.fixture
def env(monkeypatch):
    e = Env()

    def claim(worker):
        with e.lock:
            if not e.queue:
                return None
            e.claims.append(worker)
            return e.queue.pop(0)

    def reset(minutes):
        e.reset_arg = minutes
        return 2

    def run(cmd, **kw):
        e.runs.append((cmd, kw))
        return e.run_impl(cmd, **kw)

    su = runner.supabase_utils
    monkeypatch.setattr(su, "claim_next_auto_apply", claim)
    monkeypatch.setattr(su, "set_auto_apply_result", lambda *a: e.results.append(a) or True)
    monkeypatch.setattr(su, "reset_stale_auto_apply", reset)
    monkeypatch.setattr(su, "get_queued_auto_apply", lambda limit=50: list(e.queue))
    monkeypatch.setattr(su, "get_job_for_auto_apply", lambda jid: _job(jid))
    monkeypatch.setattr(runner.bundle, "build", lambda job, root, **kw: f"/work/{job['job_id']}")
    monkeypatch.setattr(runner.worker, "write_mcp_config", lambda *a: "/work/mcp.json")
    monkeypatch.setattr(runner.worker, "build_command",
                        lambda b, m, model: ["claude", "-p", "--model", model])
    monkeypatch.setattr(runner.worker, "prompt_text", lambda: "THE PROMPT")
    monkeypatch.setattr(runner.shutil, "which", lambda x: "/bin/claude")
    monkeypatch.setattr(runner.subprocess, "run", run)
    return e


def test_missing_claude_claims_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(runner.shutil, "which", lambda x: None)
    monkeypatch.setattr(runner.os.path, "isfile", lambda p: False)
    assert runner.main([]) == 2
    assert "Claude Code CLI not found; set CLAUDE_BIN" in capsys.readouterr().out
    assert env.claims == [] and env.runs == [] and env.results == []
    assert env.reset_arg is None


def test_stale_rows_reset_at_start(env, capsys):
    env.queue = []
    assert runner.main([]) == 0
    assert env.reset_arg == runner.config.AUTO_APPLY_TIMEOUT_MINUTES + 5
    assert "2" in capsys.readouterr().out


def test_prompt_on_stdin_and_cwd_is_bundle(env):
    assert runner.main([]) == 0
    cmd, kw = env.runs[0]
    assert kw["cwd"] == "/work/j0"
    assert kw["input"] == "THE PROMPT"
    assert "THE PROMPT" not in cmd
    assert kw["timeout"] == runner.config.AUTO_APPLY_TIMEOUT_MINUTES * 60
    assert env.results[0][:2] == ("j0", "ready_to_submit")


def test_timeout_marks_failed(env):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    env.run_impl = boom
    assert runner.main([]) == 1
    assert env.results[0][1] == "failed"
    assert env.results[0][2] == "timed out after 15 min"


def test_oserror_marks_failed(env):
    def boom(cmd, **kw):
        raise FileNotFoundError("claude gone")
    env.run_impl = boom
    assert runner.main([]) == 1
    assert env.results[0][1] == "failed" and "claude gone" in env.results[0][2]


def test_nonzero_exit_with_json_is_parsed(env):
    env.run_impl = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=OK_JSON, stderr="boom")
    assert runner.main([]) == 0
    assert env.results[0][1] == "ready_to_submit"
    assert env.results[0][3] == 0.5


def test_nonzero_exit_without_json_fails_with_capped_detail(env):
    env.run_impl = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="e" * 2000)
    assert runner.main([]) == 1
    assert env.results[0][1] == "failed" and len(env.results[0][2]) <= 500


def test_exception_in_bundle_marks_failed(env, monkeypatch):
    def bad(job, root, **kw):
        raise BundleError("CV missing")
    monkeypatch.setattr(runner.bundle, "build", bad)
    assert runner.main([]) == 1
    assert env.results[0][:3] == ("j0", "failed", "CV missing")
    assert env.runs == []


def test_unexpected_exception_marks_failed(env, monkeypatch):
    def bad(*a):
        raise RuntimeError("kaput")
    monkeypatch.setattr(runner.worker, "write_mcp_config", bad)
    assert runner.main([]) == 1
    assert env.results[0][1] == "failed" and "kaput" in env.results[0][2]


def test_missing_job_row_marks_failed(env, monkeypatch):
    monkeypatch.setattr(runner.supabase_utils, "get_job_for_auto_apply", lambda j: None)
    assert runner.main([]) == 1
    assert env.results[0][1] == "failed"


def test_build_command_valueerror_fails_and_stops_claiming(env, monkeypatch, capsys):
    env.queue = [_job(f"j{i}") for i in range(3)]

    def bad(b, m, model):
        raise ValueError("CLAUDE_BIN must be a real executable")
    monkeypatch.setattr(runner.worker, "build_command", bad)
    assert runner.main([]) == 1
    assert len(env.claims) == 1
    assert env.results[0][1] == "failed" and "real executable" in env.results[0][2]
    assert "real executable" in capsys.readouterr().out


def test_max_limits_total_jobs_across_threads(env):
    env.queue = [_job(f"j{i}") for i in range(5)]
    assert runner.main(["--parallel", "2", "--max", "3"]) == 0
    assert len(env.claims) == 3 and len(env.results) == 3


def test_dry_run_writes_nothing(env, capsys):
    env.queue = [_job("j0"), _job("j1")]
    assert runner.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "claude" in out and "j0" in out
    assert env.claims == [] and env.results == [] and env.runs == []
    assert env.reset_arg is None


def test_cost_summed_in_output(env, capsys):
    env.queue = [_job("j0"), _job("j1")]
    runner.main([])
    out = capsys.readouterr().out
    assert "Total cost: $1.00 over 2 jobs" in out
    assert "j0 Acme ready_to_submit" in out


def test_no_jobs_exits_zero(env, capsys):
    env.queue = []
    assert runner.main([]) == 0
    assert "Total cost: $0.00 over 0 jobs" in capsys.readouterr().out
