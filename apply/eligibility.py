"""Whether a job may be auto-applied, and if not, the one reason shown to the owner.

The reason is the help text under a disabled Auto apply button, so each is a short
sentence naming what to fix. Checks run cheapest and most fundamental first and the
first failure wins: showing every problem at once would bury the one to fix next.
"""
import os
from typing import Callable, Optional

import config
from apply import documents
from apply.bundle import start_url
from review.application_pack import choose_cv, unfilled_answers


def check(job: dict, answers: dict,
          cv_exists: Callable[[str], bool] = os.path.exists) -> tuple[bool, Optional[str]]:
    """(True, None) when the job can be queued, else (False, reason).

    cv_exists is injectable so tests need no files on disk.
    """
    if job.get("auto_apply_missing"):
        return False, "Run supabase_setup/add_auto_apply.sql first"
    # Any job the owner picks may be queued: score and ATS are not gates. Submission
    # stays limited to config.AUTO_SUBMIT_ATS, decided in the bundle and the parser.
    if not start_url(job):
        return False, "No link to apply from"
    # Queue rows do not select `status` (the queue query already filters
    # status='new'), so a missing key means queued; only a present, non-new value refuses.
    if ("status" in job and job["status"] != "new") or job.get("dismissed_at"):
        return False, "Already applied or skipped"
    state = job.get("auto_apply_status")
    if state in ("queued", "running"):
        return False, "Already queued or running"
    # Only the requeue states (ready_to_submit / verify / failed) pass: the click
    # retries them. Anything else, known or not, is treated as handled.
    if state and state not in config.AUTO_APPLY_REQUEUE_STATES:
        return False, f"Already handled: {state}"
    unfilled = unfilled_answers(answers or {})
    if unfilled:
        return False, f"Fill in application_answers.json: {unfilled[0]}"
    cv = choose_cv(job.get("job_title")) or documents.generic_cv()
    if not cv or not cv_exists(cv):
        return False, "No CV yet: upload one on the Set up page"
    return True, None
