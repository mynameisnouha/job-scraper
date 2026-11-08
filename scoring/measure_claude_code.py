"""What does a screen or a full score cost through `claude -p`? Measure before moving.

Run on the owner's laptop, logged in to Claude Code, watching the plan's usage meter:

    python -m scoring.measure_claude_code --screens 20 --scores 20 --model haiku

It sends the pipeline's real screen and score prompts for the most recent jobs
through `claude -p`, one job per call, and records the tokens and the notional cost
claude reports. It is read-only: nothing is written to Supabase; results go to
output/claude_code_measure/<timestamp>.csv plus a printed summary. The numbers decide
which of screening and scoring moves to the subscription (design, Phase 2 step 1).

Fencing: `--system-prompt-file` replaces Claude Code's own system prompt (an inline
prompt would also hit Windows' command-line length limit on the scoring prompt),
`--tools ""` disables every tool, `--max-turns 1`, no MCP servers, no saved session.
The environment is apply.worker.scrubbed_env(): without ANTHROPIC_API_KEY claude
bills the owner's subscription, and no other credential reaches it.

Structured output: claude -p has no tool-forced schema here, so the user prompt ends
with the JSON schema and the answer is validated with the same Pydantic model the
pipeline uses. A validation failure is counted, not repaired.
"""

import argparse
import csv
import json
import logging
import os
import statistics
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from apply.worker import check_claude_bin, scrubbed_env
from scoring.claude_code import (_USAGE_KEYS, claude_argv, parse_usage,
                                 strip_fence as _strip_fence, system_text as _system_text,
                                 with_schema as _with_schema)
from db import supabase_utils
from models import ScoreBreakdown, ScreenResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

_OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "output", "claude_code_measure")
_TIMEOUT_SECONDS = 300


def run_one(kind: str, job: Dict[str, Any], request: Dict[str, Any], model: str) -> Dict[str, Any]:
    """One claude -p call for one prepared request; never raises."""
    model_cls = ScreenResult if kind == "screen" else ScoreBreakdown
    row: Dict[str, Any] = {"kind": kind, "job_id": job.get("job_id"), "valid": False, "error": ""}
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_system_text(request.get("system_prompt")))
        system_file = f.name
    empty_dir = tempfile.mkdtemp(prefix="claude-measure-")
    try:
        proc = subprocess.run(claude_argv(model, system_file),
                              input=_with_schema(request["prompt"], model_cls),
                              capture_output=True, text=True, encoding="utf-8",
                              # Same thinking switch as the pipeline (scoring/claude_code.py).
                              env={**scrubbed_env(), "MAX_THINKING_TOKENS": "0"},
                              timeout=_TIMEOUT_SECONDS,
                              cwd=empty_dir)
        usage = parse_usage(proc.stdout)
        row.update({k: v for k, v in usage.items() if k != "result"})
        if usage["ok"]:
            try:
                model_cls.model_validate_json(_strip_fence(usage["result"]))
                row["valid"] = True
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"invalid: {str(exc)[:120]}"
        else:
            row["error"] = (proc.stderr or proc.stdout or "")[-200:]
    except Exception as exc:  # noqa: BLE001
        row["error"] = str(exc)[:200]
    finally:
        os.unlink(system_file)
        os.rmdir(empty_dir)
    return row


def recent_jobs(n: int) -> List[Dict[str, Any]]:
    """The n most recently scraped active jobs that have a description. Read-only."""
    rows = (
        supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
        .select("job_id, job_title, company, level, description, program_type")
        .eq("is_active", True)
        .order("scraped_at", desc=True)
        .limit(n * 3)
        .execute()
    ).data or []
    return [r for r in rows if (r.get("description") or "").strip()][:n]


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for kind in ("screen", "score"):
        mine = [r for r in rows if r["kind"] == kind]
        if not mine:
            continue
        stats: Dict[str, Any] = {"calls": len(mine), "valid": sum(r["valid"] for r in mine)}
        for key in (*_USAGE_KEYS, "cost_usd", "duration_ms"):
            values = [r[key] for r in mine if isinstance(r.get(key), (int, float))]
            stats[f"{key}_mean"] = round(statistics.mean(values), 4) if values else None
            stats[f"{key}_median"] = round(statistics.median(values), 4) if values else None
        out[kind] = stats
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Measure claude -p cost per screen and score.")
    parser.add_argument("--screens", type=int, default=20)
    parser.add_argument("--scores", type=int, default=20)
    parser.add_argument("--model", default="haiku")
    args = parser.parse_args(argv)

    # Imported here: score_jobs builds its LLM clients and reads the profile on import.
    from scoring import score_jobs

    jobs = recent_jobs(max(args.screens, args.scores))
    if not jobs:
        logging.error("No jobs with a description found.")
        return 1
    resume = supabase_utils.get_base_resume()
    resume_text = score_jobs.format_resume_to_text(resume) if resume else None

    rows = []
    for job in jobs[:args.screens]:
        request = score_jobs.build_screen_request(job)
        if request:
            rows.append(run_one("screen", job, request, args.model))
            logging.info(f"screen {job['job_id']}: {rows[-1]}")
    if args.scores and not resume_text:
        logging.error("No base resume in Supabase; skipping the score measurement.")
    for job in jobs[:args.scores] if resume_text else []:
        request = score_jobs.build_score_request(resume_text, job)
        if request:
            rows.append(run_one("score", job, request, args.model))
            logging.info(f"score {job['job_id']}: {rows[-1]}")

    os.makedirs(_OUT_DIR, exist_ok=True)
    path = os.path.join(_OUT_DIR, datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv")
    fields = ["kind", "job_id", "valid", "error", "cost_usd", "duration_ms", *_USAGE_KEYS, "ok"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summarize(rows), indent=2))
    print(f"Rows written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
