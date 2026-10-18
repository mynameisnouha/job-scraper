"""`python -m apply.review <job_id>`: finish a stopped application with the owner.

The batch worker stops before Submit for non-auto-submit ATSs (ready_to_submit) or
when unsure (verify). This runs an interactive, headed claude session in the job's
bundle under the same tool fence; the session must ask the owner before clicking
Submit, and the outcome is recorded from the owner's own answer afterwards.
"""
import argparse
import logging
import os
import subprocess
import sys
from typing import Callable, Optional

import config
from apply import bundle, worker
from db import supabase_utils

log = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_prompt.md")
_OPENING = "Read job.json and playbook.md in this folder and fill the application form."
_RESUMABLE = ("ready_to_submit", "verify")


def _session_command(bundle_dir: str, mcp_config: str) -> list:
    """Interactive argv: the worker's fence, no -p, no turn/budget caps, no skip-permissions."""
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        prompt = f.read()
    return [worker.check_claude_bin(), *worker.fence_args(bundle_dir, mcp_config),
            "--append-system-prompt", prompt, _OPENING]


def main(argv: Optional[list] = None, ask: Callable[[str], str] = input) -> int:
    ap = argparse.ArgumentParser(prog="apply.review")
    ap.add_argument("job_id")
    job_id = ap.parse_args(argv).job_id

    job = supabase_utils.get_job_for_auto_apply(job_id)
    if not job:
        print(f"Job {job_id} not found.")
        return 1
    previous = job.get("auto_apply_status")
    if previous not in _RESUMABLE:
        print(f"Job {job_id} is {previous!r}; review needs ready_to_submit or verify.")
        return 1

    if not supabase_utils.set_auto_apply_result(job_id, "running", "in review", None):
        print(f"Could not mark {job_id} as running in the database; not starting the session.")
        return 1
    try:
        # auto_submit forced off: the owner, not the playbook, decides to submit.
        bdir = bundle.build(job, config.AUTO_APPLY_WORK_DIR, auto_submit=False)
        cfg = worker.write_mcp_config(bdir, job.get("apply_url"), job.get("ats_type"),
                                      headless=False)
        subprocess.run(_session_command(bdir, cfg), cwd=bdir, env=worker.scrubbed_env())
        answer = ask("Did you click Submit and see a confirmation? [y/N] ")
    except (Exception, KeyboardInterrupt) as e:
        log.warning("review of %s interrupted: %r", job_id, e)
        if supabase_utils.set_auto_apply_result(
                job_id, previous, f"review interrupted: {type(e).__name__}", None):
            print(f"Review interrupted ({type(e).__name__}); status restored to {previous}.")
        else:
            print(f"Review interrupted ({type(e).__name__}); status could not be restored "
                  f"to {previous}, the job may show as running.")
        return 1

    if (answer or "").strip().lower() in ("y", "yes"):
        if not supabase_utils.set_auto_apply_result(job_id, "applied", "submitted in review", None):
            print("NOT SAVED: the application was submitted but the database write failed. "
                  "Mark it applied by hand in the app.")
            return 1
        print("Marked applied.")
    else:
        supabase_utils.set_auto_apply_result(job_id, "ready_to_submit", "owner did not submit", None)
        print("Left as ready_to_submit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
