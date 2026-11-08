"""Run one LLM request through `claude -p`, on the owner's Claude subscription.

LLMClient routes any `claude-code/<model>` model string here (scoring/llm_client.py),
so screening, scoring and tailoring move to the subscription by configuration alone.
The prompts, the Pydantic validation and the database writes stay the pipeline's own.

Fencing, the same as the measurement script it grew out of: Claude Code's system
prompt is replaced by ours (`--system-prompt-file`; inline would hit Windows'
command-line limit on the scoring prompt), no tools, one turn, no MCP servers, only
local settings in an empty working directory, no saved session, and an environment
without credentials (apply.worker.scrubbed_env) so claude bills the subscription.
Thinking is switched off (`MAX_THINKING_TOKENS=0`, `--effort`): measured full scores
ran to ~8.5k output tokens against ~2.3k on the API.

Two stops protect the owner's own Claude Code use. A daily call budget
(LOCAL_DAILY_CALL_BUDGET), counted in output/claude_code_usage.json so it holds
across runs on the same day. And a usage-limit answer from claude stops every later
call in this process at once; the jobs left are picked up by the next run or by CI.
"""

import json
import os
import re
import subprocess
import tempfile
from datetime import date
from typing import Any, Dict, List, Optional

import config
from apply.worker import check_claude_bin, scrubbed_env

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TIMEOUT_SECONDS = 300
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
               "cache_creation_input_tokens")
# claude's wording for a spent plan limit is not documented; this is a guess kept in
# one place. A miss only means the budget, not the limit, stops the run.
_LIMIT_RE = re.compile(r"usage limit|rate limit|limit reached", re.IGNORECASE)

_limit_reached = False


class ClaudeCodeLimitReached(RuntimeError):
    """The daily budget is spent or claude reported a plan usage limit."""


def reset_limit() -> None:
    """Forget a usage limit seen earlier in this process."""
    global _limit_reached
    _limit_reached = False


def usage_path() -> str:
    return os.path.join(_REPO_ROOT, "output", "claude_code_usage.json")


def claude_argv(model: str, system_prompt_file: str) -> List[str]:
    """The fenced claude command line; the user prompt goes on stdin."""
    return [check_claude_bin(), "-p",
            "--output-format", "json",
            "--model", model,
            "--system-prompt-file", system_prompt_file,
            "--tools", "",
            "--max-turns", "1",
            "--effort", config.CLAUDE_CODE_EFFORT,
            "--strict-mcp-config",
            "--setting-sources", "local",
            "--no-session-persistence"]


def parse_usage(stdout: str) -> Dict[str, Any]:
    """Tokens, cost and the answer text from claude's JSON output."""
    out: Dict[str, Any] = {"ok": False, "cost_usd": None, "duration_ms": None, "result": None,
                           **{k: None for k in _USAGE_KEYS}}
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return out
    if not isinstance(data, dict):
        return out
    usage = data.get("usage") or {}
    for key in _USAGE_KEYS:
        out[key] = usage.get(key)
    cost = data.get("total_cost_usd")
    out["cost_usd"] = float(cost) if isinstance(cost, (int, float)) else None
    out["duration_ms"] = data.get("duration_ms")
    out["result"] = data.get("result") if isinstance(data.get("result"), str) else None
    out["ok"] = not data.get("is_error") and out["result"] is not None
    return out


def strip_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def with_schema(prompt: str, model_cls) -> str:
    schema = json.dumps(model_cls.model_json_schema())
    return (f"{prompt}\n\nAnswer with only one JSON object that matches this JSON schema, "
            f"no prose and no code fence:\n{schema}")


def system_text(system_prompt) -> str:
    if isinstance(system_prompt, (list, tuple)):
        return "\n\n".join(p for p in system_prompt if p)
    return system_prompt or ""


def _spend_one_call() -> None:
    """Count a call against today's budget, or raise when it is spent."""
    today = date.today().isoformat()
    path = usage_path()
    try:
        with open(path, encoding="utf-8") as f:
            usage = json.load(f)
    except (OSError, ValueError):
        usage = {}
    calls = usage.get("calls", 0) if usage.get("date") == today else 0
    if calls >= config.LOCAL_DAILY_CALL_BUDGET:
        raise ClaudeCodeLimitReached(
            f"Daily Claude Code budget spent ({calls}/{config.LOCAL_DAILY_CALL_BUDGET} calls).")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "calls": calls + 1}, f)


def calls_today() -> int:
    try:
        with open(usage_path(), encoding="utf-8") as f:
            usage = json.load(f)
    except (OSError, ValueError):
        return 0
    return usage.get("calls", 0) if usage.get("date") == date.today().isoformat() else 0


def complete(prompt: str, system_prompt, model: str, response_format=None) -> str:
    """One claude -p call. Returns the answer (validated JSON text when a
    response_format is given). Raises ClaudeCodeLimitReached or RuntimeError."""
    global _limit_reached
    if _limit_reached:
        raise ClaudeCodeLimitReached("Claude Code usage limit reached earlier in this run.")
    try:
        check_claude_bin()  # before the budget is spent on a call that cannot run
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"claude is not available: {exc}") from exc
    _spend_one_call()

    user_prompt = with_schema(prompt, response_format) if response_format is not None else prompt
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(system_text(system_prompt))
        system_file = f.name
    empty_dir = tempfile.mkdtemp(prefix="claude-code-")
    env = {**scrubbed_env(), "MAX_THINKING_TOKENS": "0"}
    try:
        proc = subprocess.run(claude_argv(model, system_file), input=user_prompt, capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=_TIMEOUT_SECONDS, cwd=empty_dir)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"claude -p failed to run: {exc}") from exc
    finally:
        os.unlink(system_file)
        os.rmdir(empty_dir)

    usage = parse_usage(proc.stdout)
    if not usage["ok"]:
        detail = (usage["result"] or proc.stderr or proc.stdout or "")[-300:]
        if _LIMIT_RE.search(detail):
            _limit_reached = True
            raise ClaudeCodeLimitReached(f"Claude Code usage limit: {detail}")
        raise RuntimeError(f"claude -p returned no answer: {detail}")

    text = strip_fence(usage["result"])
    if response_format is not None:
        try:
            response_format.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"claude -p answer failed validation: {str(exc)[:200]}") from exc
    return text
