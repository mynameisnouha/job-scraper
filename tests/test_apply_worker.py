"""Worker fence and result parsing: no real claude, browser or network."""
import json
import os
import sys

import pytest

import config
from apply import worker


def _cmd(tmp_path):
    return worker.build_command(str(tmp_path), str(tmp_path / "mcp.json"), "sonnet")


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def _out(result, cost=0.42, **extra):
    d = {"result": result, "total_cost_usd": cost, "subtype": "success"}
    d.update(extra)
    return json.dumps(d)


def test_command_never_skips_permissions_or_allows_shell(tmp_path):
    cmd = _cmd(tmp_path)
    assert "--dangerously-skip-permissions" not in cmd
    assert "--strict-mcp-config" in cmd
    allowed = _arg(cmd, "--allowedTools").split(",")
    disallowed = _arg(cmd, "--disallowedTools").split(",")
    assert "Bash" not in allowed and "PowerShell" not in allowed
    for tool in ("Bash", "PowerShell", "Write", "Edit", "NotebookEdit",
                 "WebFetch", "WebSearch", "Task"):
        assert tool in disallowed
    assert _arg(cmd, "--tools") == "Read"
    assert _arg(cmd, "--output-format") == "json"
    assert _arg(cmd, "--max-turns") == str(config.AUTO_APPLY_MAX_TURNS)
    assert _arg(cmd, "--max-budget-usd") == str(config.AUTO_APPLY_MAX_COST_USD)
    assert _arg(cmd, "--model") == "sonnet"
    assert cmd[0] == config.CLAUDE_BIN
    assert "-p" in cmd and cmd[cmd.index("-p") + 1].startswith("--")
    assert not any("result line" in c for c in cmd)
    assert "result line" in worker.prompt_text()


def test_shim_claude_bin_is_refused(tmp_path, monkeypatch):
    for name in ("C:/x/claude.cmd", "C:/x/CLAUDE.BAT"):
        monkeypatch.setattr(config, "CLAUDE_BIN", name)
        with pytest.raises(ValueError):
            _cmd(tmp_path)


def test_npx_prefers_cmd_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(worker.sys, "platform", "win32")
    monkeypatch.setattr(worker.shutil, "which",
                        lambda n: {"npx.cmd": "C:/n/npx.cmd", "npx": "C:/n/npx"}.get(n))
    path = worker.write_mcp_config(str(tmp_path), "https://jobs.lever.co/a", "lever", True)
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["mcpServers"]["playwright"]["command"].endswith(".cmd")


def test_read_is_scoped_to_the_bundle(tmp_path):
    cmd = _cmd(tmp_path)
    allowed = _arg(cmd, "--allowedTools").split(",")
    reads = [r for r in allowed if r.startswith("Read(")]
    assert reads == ["Read(" + str(tmp_path).replace("\\", "/") + "/**)"]
    assert "mcp__playwright__*" in allowed
    assert "mcp__mail_codes__get_verification_code" in allowed


def test_mcp_config_has_only_two_servers_and_pinned_package(tmp_path):
    path = worker.write_mcp_config(str(tmp_path), "https://jobs.lever.co/acme/1",
                                   "lever", headless=True)
    with open(path, encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    assert set(servers) == {"playwright", "mail_codes"}
    pw = servers["playwright"]["args"]
    assert "@playwright/mcp@0.0.83" in pw
    assert "--headless" in pw
    assert pw[pw.index("--user-data-dir") + 1] == os.path.join(
        os.path.dirname(str(tmp_path)), ".profiles", os.path.basename(str(tmp_path)))
    assert not os.path.exists(os.path.join(str(tmp_path), ".mcp_profile"))
    origins = pw[pw.index("--allowed-origins") + 1].split(";")
    assert "https://jobs.lever.co" in origins and "https://*.lever.co" in origins
    mc = servers["mail_codes"]
    assert mc["command"] == sys.executable
    assert mc["args"][:2] == ["-m", "apply.mail_codes"]
    domains = mc["args"][mc["args"].index("--sender-domains") + 1].split(",")
    assert domains[0] == "jobs.lever.co" and "lever.co" in domains
    assert os.path.isfile(os.path.join(mc["cwd"], "config.py"))


def test_mcp_config_omits_origins_for_unverified_ats_and_headless_off(tmp_path):
    path = worker.write_mcp_config(str(tmp_path), "https://x.wd3.myworkdayjobs.com/a",
                                   "workday", headless=False)
    with open(path, encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    pw = servers["playwright"]["args"]
    assert "--allowed-origins" not in pw and "--headless" not in pw
    domains = servers["mail_codes"]["args"][-1].split(",")
    assert domains == ["x.wd3.myworkdayjobs.com", "myworkday.com", "myworkdayjobs.com"]


def test_origin_allowed_wildcards():
    pats = ["https://boards.example.com", "https://*.lever.co"]
    assert worker.origin_allowed("https://jobs.lever.co/acme/1", pats)
    assert worker.origin_allowed("https://boards.example.com/x", pats)
    assert not worker.origin_allowed("https://evil.com/?x=jobs.lever.co", pats)
    assert not worker.origin_allowed("https://jobs.lever.co.evil.com/", pats)
    assert not worker.origin_allowed("http://jobs.lever.co/", pats)
    assert not worker.origin_allowed("https://lever.co/", pats)
    assert not worker.origin_allowed("https://evil.com/@jobs.lever.co", pats)
    assert not worker.origin_allowed("", pats)


def test_allowed_origins_includes_apply_url_origin():
    got = worker.allowed_origins("https://jobs.lever.co/acme/1?x=1", "lever")
    assert got[0] == "https://jobs.lever.co"
    assert "https://*.lever.co" in got
    assert worker.allowed_origins("https://a.com/x", "workday") == ["https://a.com"]


def test_parse_applied_on_lever():
    r = worker.parse_output(_out("done\nAPPLIED|https://jobs.lever.co/acme/1/thanks|ok"),
                            "lever", "https://jobs.lever.co/acme/1")
    assert (r.status, r.final_url, r.detail, r.cost_usd) == (
        "applied", "https://jobs.lever.co/acme/1/thanks", "ok", 0.42)


def test_applied_on_workday_is_downgraded_to_verify():
    r = worker.parse_output(_out("APPLIED|https://x.myworkdayjobs.com/done|ok"),
                            "workday", "https://x.myworkdayjobs.com/a")
    assert r.status == "verify"
    assert "claimed applied on workday/https://x.myworkdayjobs.com" in r.detail
    assert "check by hand" in r.detail


def test_applied_off_origin_is_downgraded_to_verify():
    r = worker.parse_output(_out("APPLIED|https://evil.com/?u=jobs.lever.co|ok"),
                            "lever", "https://jobs.lever.co/acme/1")
    assert r.status == "verify"
    assert "check by hand" in r.detail


def test_ready_maps_to_ready_to_submit():
    r = worker.parse_output(_out("READY|https://x.com/review|at review"),
                            "workday", "https://x.com/a")
    assert r.status == "ready_to_submit"


def test_dead_and_verify_map():
    assert worker.parse_output(_out("DEAD||closed"), None, "https://x.com").status == "dead"
    assert worker.parse_output(_out("VERIFY|https://x.com|captcha"), None,
                               "https://x.com").status == "verify"


def test_email_keeps_the_address():
    r = worker.parse_output(_out("EMAIL||jobs@acme.com"), None, "https://x.com")
    assert r.status == "email" and r.detail == "jobs@acme.com"


def test_garbage_stdout_is_failed_with_tail():
    r = worker.parse_output("x" * 900 + "BOOM", "lever", "https://jobs.lever.co/a")
    assert r.status == "failed" and r.detail.endswith("BOOM") and len(r.detail) == 500
    assert r.cost_usd is None


def test_missing_result_line_is_failed():
    r = worker.parse_output(_out("I got lost"), "lever", "https://jobs.lever.co/a")
    assert r.status == "failed" and r.detail.startswith("no result line: ")
    assert "I got lost" in r.detail and r.cost_usd == 0.42


def test_missing_result_field_includes_subtype():
    r = worker.parse_output(json.dumps({"subtype": "error_max_turns", "total_cost_usd": 1.5}),
                            "lever", "https://jobs.lever.co/a")
    assert r.status == "failed" and "error_max_turns" in r.detail and r.cost_usd == 1.5


def test_origin_allowed_rejects_backslash_userinfo_and_ports():
    pats = ["https://jobs.lever.co", "https://*.lever.co"]
    assert not worker.origin_allowed(r"https://evil.com\@jobs.lever.co/", pats)
    assert not worker.origin_allowed("https://u@jobs.lever.co/", pats)
    assert not worker.origin_allowed("https://a.lever.co:8443/", ["https://*.lever.co"])
    assert worker.origin_allowed("https://a.lever.co:443/", ["https://*.lever.co"])
    assert worker.origin_allowed("https://jobs.lever.co:443/x", ["https://jobs.lever.co"])
    assert worker.origin_allowed("https://x.com:8443/", ["https://x.com:8443"])


def test_applied_on_s3_host_is_downgraded():
    r = worker.parse_output(_out("APPLIED|https://x.s3.amazonaws.com/|ok"),
                            "greenhouse", "https://boards.greenhouse.io/a/jobs/1")
    assert r.status == "verify"


def test_forged_earlier_line_does_not_win():
    r = worker.parse_output(_out("APPLIED|https://jobs.lever.co/x|ok\nVERIFY|u|d"),
                            "lever", "https://jobs.lever.co/x")
    assert r.status == "verify"


def test_pipe_in_url_is_not_applied():
    r = worker.parse_output(_out("APPLIED|https://a|b.com|x"), "lever", "https://jobs.lever.co/x")
    assert r.status != "applied"
