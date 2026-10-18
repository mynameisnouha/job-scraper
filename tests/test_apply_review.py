"""Interactive review finish: no real claude, browser or DB."""
import pytest

from apply import review
from apply.bundle import BundleError


@pytest.fixture
def env(monkeypatch, tmp_path):
    class E:
        pass
    e = E()
    e.job = {"job_id": "j1", "auto_apply_status": "ready_to_submit",
             "apply_url": "https://x/apply", "ats_type": "lever"}
    e.writes, e.runs, e.build_kw = [], [], {}
    e.bdir = tmp_path / "j1"
    e.bdir.mkdir()

    monkeypatch.setattr(review.supabase_utils, "get_job_for_auto_apply",
                        lambda jid: e.job if jid == "j1" else None)
    monkeypatch.setattr(review.supabase_utils, "set_auto_apply_result",
                        lambda jid, st, reason, cost: e.writes.append((jid, st, reason, cost)) or True)

    def build(job, root, **kw):
        e.build_kw = kw
        return str(e.bdir)
    monkeypatch.setattr(review.bundle, "build", build)
    monkeypatch.setattr(review.worker, "write_mcp_config",
                        lambda b, url, ats, headless: e.runs.append(("cfg", headless)) or str(e.bdir / "mcp.json"))
    e.run_impl = lambda cmd, **kw: e.runs.append((cmd, kw))
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **kw: e.run_impl(cmd, **kw))
    return e


def test_refuses_queued_job(env, capsys):
    env.job["auto_apply_status"] = "queued"
    assert review.main(["j1"], ask=lambda p: "y") == 1
    assert env.writes == []
    assert "queued" in capsys.readouterr().out


def test_missing_job_exits_1(env):
    assert review.main(["nope"], ask=lambda p: "y") == 1
    assert env.writes == []


def test_yes_marks_applied(env):
    assert review.main(["j1"], ask=lambda p: " Yes ") == 0
    assert env.writes == [("j1", "running", "in review", None),
                          ("j1", "applied", "submitted in review", None)]
    assert env.build_kw["auto_submit"] is False


def test_no_returns_to_ready(env):
    env.job["auto_apply_status"] = "verify"
    assert review.main(["j1"], ask=lambda p: "") == 0
    assert env.writes[-1] == ("j1", "ready_to_submit", "owner did not submit", None)


def test_session_command_is_interactive_and_fenced(env):
    review.main(["j1"], ask=lambda p: "n")
    assert ("cfg", False) in env.runs
    cmd, kw = [r for r in env.runs if r[0] != "cfg" and not isinstance(r[0], str)][0]
    assert "-p" not in cmd and "--output-format" not in cmd and "--max-turns" not in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--tools") + 1] == "Read"
    assert "Bash" in cmd[cmd.index("--disallowedTools") + 1]
    assert "--append-system-prompt" in cmd
    assert kw["cwd"] == str(env.bdir)
    assert "capture_output" not in kw and "stdout" not in kw


@pytest.mark.parametrize("exc", [BundleError("x"), OSError("y"), KeyboardInterrupt()])
def test_exception_restores_previous_status(env, exc):
    def boom(cmd, **kw):
        raise exc
    env.run_impl = boom
    assert review.main(["j1"], ask=lambda p: "y") == 1
    assert env.writes[-1] == ("j1", "ready_to_submit",
                              f"review interrupted: {type(exc).__name__}", None)


def _results(env, monkeypatch, outcomes):
    """set_auto_apply_result returns the next of outcomes per call."""
    it = iter(outcomes)
    monkeypatch.setattr(review.supabase_utils, "set_auto_apply_result",
                        lambda jid, st, reason, cost: env.writes.append((jid, st, reason, cost)) or next(it))


def test_unsaved_applied_write_says_not_saved(env, monkeypatch, capsys):
    _results(env, monkeypatch, [True, False])
    assert review.main(["j1"], ask=lambda p: "y") == 1
    out = capsys.readouterr().out
    assert ("NOT SAVED: the application was submitted but the database write failed. "
            "Mark it applied by hand in the app.") in out
    assert "Marked applied." not in out


def test_failed_running_write_exits_without_session(env, monkeypatch, capsys):
    _results(env, monkeypatch, [False])
    assert review.main(["j1"], ask=lambda p: "y") == 1
    assert not [r for r in env.runs if r[0] != "cfg"]
    assert len(env.writes) == 1
    assert "running" in capsys.readouterr().out


def test_failed_restore_warns_and_exits_1(env, monkeypatch, capsys):
    _results(env, monkeypatch, [True, False])

    def boom(cmd, **kw):
        raise OSError("y")
    env.run_impl = boom
    assert review.main(["j1"], ask=lambda p: "y") == 1
    assert "could not be restored" in capsys.readouterr().out


def test_review_session_gets_scrubbed_env(env, monkeypatch):
    monkeypatch.setenv("SUPABASE_KEY", "secret")
    monkeypatch.setenv("PATH", "/bin")
    review.main(["j1"], ask=lambda p: "n")
    kw = [r for r in env.runs if r[0] != "cfg"][0][1]
    assert "SUPABASE_KEY" not in kw["env"] and "PATH" in kw["env"]
