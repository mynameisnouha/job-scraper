"""The fenced `claude -p` worker: its command line, MCP config and result parsing.

This module decides what the worker may do and how its last message becomes a DB
status. The fence is layered because a page the worker reads can contain prompt
injection: built-in tools are limited to Read (--tools), shells and writers are
also denied, Read is scoped to the bundle, only two MCP servers exist, and the
browser itself is origin-limited where that was verified. The worker's claim of
success is never trusted blindly (see parse_output).
"""
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import config

log = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt.md")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULT_RE = re.compile(r"^(APPLIED|READY|VERIFY|DEAD|EMAIL)\|([^|]*)\|(.*)$")
_STATUS_MAP = {"READY": "ready_to_submit", "VERIFY": "verify", "DEAD": "dead",
               "EMAIL": "email"}
# --tools "Read" already limits built-ins, but --allowedTools alone does not block
# shells (spiked on claude 2.1.301), so shells and writers are denied explicitly too.
_DISALLOWED = "Bash,PowerShell,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task"


@dataclass
class WorkerResult:
    status: str  # one of config.AUTO_APPLY_STATUSES
    final_url: Optional[str]
    detail: str
    cost_usd: Optional[float]


def _origin(url: str) -> Optional[str]:
    """scheme://host[:port] of a URL, lowercased; None when it has no host."""
    try:
        p = urlparse((url or "").strip())
        host = p.hostname
        port = p.port
    except ValueError:
        return None
    if not p.scheme or not host:
        return None
    return f"{p.scheme.lower()}://{host.lower()}" + (f":{port}" if port else "")


def allowed_origins(apply_url: str, ats_type: Optional[str]) -> list:
    """The apply URL's own origin plus the ATS's extra verified origins."""
    own = _origin(apply_url)
    return ([own] if own else []) + list(config.AUTO_APPLY_ORIGINS.get(ats_type, []))


def origin_allowed(url: str, patterns: list) -> bool:
    """True when url's origin equals a pattern or fits a `scheme://*.suffix` one.

    Compares the parsed host, never a substring, so `evil.com/?x=jobs.lever.co`
    and `jobs.lever.co.evil.com` fail. A wildcard needs at least one label before
    the suffix and the same scheme.
    """
    origin = _origin(url)
    if not origin:
        return False
    scheme, _, host = origin.partition("://")
    for pat in patterns:
        pat = (pat or "").strip().lower()
        pscheme, sep, phost = pat.partition("://")
        if not sep:
            continue
        if phost.startswith("*."):
            if pscheme == scheme and host.split(":")[0].endswith(phost[1:]):
                return True
        elif pat == origin:
            return True
    return False


def write_mcp_config(bundle_dir: str, apply_url: str, ats_type: Optional[str],
                     headless: bool) -> str:
    """Write <bundle>/mcp.json with exactly the playwright and mail_codes servers."""
    pw_args = ["-y", config.PLAYWRIGHT_MCP_PACKAGE, "--user-data-dir",
               os.path.join(bundle_dir, ".mcp_profile")]
    # The browser fence is only applied for ATSs where forms were verified to work
    # behind it; elsewhere it would break captcha/upload hosts and fail every run.
    if ats_type in config.AUTO_APPLY_ORIGINS:
        pw_args += ["--allowed-origins", ";".join(allowed_origins(apply_url, ats_type))]
    if headless:
        pw_args.append("--headless")
    host = (urlparse(apply_url or "").hostname or "").lower()
    domains = ([host] if host else []) + config.AUTO_APPLY_MAIL_DOMAINS.get(ats_type, [])
    cfg = {"mcpServers": {
        "playwright": {"command": shutil.which("npx") or "npx", "args": pw_args},
        "mail_codes": {"command": sys.executable,
                       "args": ["-m", "apply.mail_codes", "--sender-domains",
                                ",".join(domains)],
                       "cwd": _REPO_ROOT},
    }}
    path = os.path.join(bundle_dir, "mcp.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return path


def build_command(bundle_dir: str, mcp_config: str, model: str) -> list:
    """The claude argv. Never skips permissions; Read only inside the bundle."""
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        prompt = f.read()
    read_rule = "Read(" + os.path.abspath(bundle_dir).replace("\\", "/") + "/**)"
    allowed = ",".join(["mcp__playwright__*", "mcp__mail_codes__get_verification_code",
                        read_rule])
    return [config.CLAUDE_BIN, "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--max-turns", str(config.AUTO_APPLY_MAX_TURNS),
            "--max-budget-usd", str(config.AUTO_APPLY_MAX_COST_USD),
            "--strict-mcp-config", "--mcp-config", mcp_config,
            "--tools", "Read",
            "--allowedTools", allowed,
            "--disallowedTools", _DISALLOWED]


def parse_output(stdout: str, ats_type: Optional[str], apply_url: str) -> WorkerResult:
    """Turn claude's JSON stdout into a WorkerResult.

    APPLIED is only believed for auto-submit ATSs and when the final page is on an
    allowed origin; anything else is downgraded to `verify` for the owner to check.
    """
    try:
        data = json.loads(stdout)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError):
        return WorkerResult("failed", None, (stdout or "")[-500:], None)
    cost = data.get("total_cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) else None
    result = data.get("result")
    if not isinstance(result, str):
        subtype = data.get("subtype") or "unknown"
        return WorkerResult("failed", None, f"no result from claude (subtype: {subtype})", cost)

    lines = [ln.strip() for ln in result.strip().splitlines() if ln.strip()]
    m = _RESULT_RE.match(lines[-1]) if lines else None
    if not m:
        return WorkerResult("failed", None, f"no result line: {result.strip()[-500:]}", cost)
    word, final_url, detail = m.group(1), m.group(2).strip() or None, m.group(3).strip()

    if word != "APPLIED":
        return WorkerResult(_STATUS_MAP[word], final_url, detail, cost)
    if (ats_type in config.AUTO_SUBMIT_ATS and final_url
            and origin_allowed(final_url, allowed_origins(apply_url, ats_type))):
        return WorkerResult("applied", final_url, detail, cost)
    log.warning("worker claimed APPLIED on %s at %s; downgraded to verify", ats_type, final_url)
    return WorkerResult("verify", final_url,
                        f"claimed applied on {ats_type}/{_origin(final_url or '')}; check by hand",
                        cost)
