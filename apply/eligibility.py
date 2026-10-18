"""Whether a job may be auto-applied, and if not, the one reason shown to the owner.

The reason is the help text under a disabled Auto apply button, so each is a short
sentence naming what to fix. Checks run cheapest and most fundamental first and the
first failure wins: showing every problem at once would bury the one to fix next.
"""
import os
from typing import Callable, Optional

import config
from review.application_pack import choose_cv, unfilled_answers


def check(job: dict, answers: dict,
          cv_exists: Callable[[str], bool] = os.path.exists) -> tuple[bool, Optional[str]]:
    """(True, None) when the job can be queued, else (False, reason).

    cv_exists is injectable so tests need no files on disk.
    """
    if job.get("auto_apply_missing"):
        return False, "Run supabase_setup/add_auto_apply.sql first"
    if (job.get("resume_score") or 0) <= config.AUTO_APPLY_MIN_SCORE:
        return False, f"Score must be above {config.AUTO_APPLY_MIN_SCORE}"
    ats = (job.get("ats_type") or "").strip().lower()
    if not job.get("apply_url") or not ats:
        return False, "No employer application form known"
    if ats in config.AUTO_APPLY_MANUAL_ATS:
        return False, "iCIMS is applied to by hand"
    if job.get("status") != "new" or job.get("dismissed_at"):
        return False, "Already applied or skipped"
    state = job.get("auto_apply_status")
    if state in ("queued", "running"):
        return False, "Already queued or running"
    # ready_to_submit / verify / failed are deliberately allowed: the click retries.
    if state in ("applied", "dead", "email"):
        return False, f"Already handled: {state}"
    unfilled = unfilled_answers(answers or {})
    if unfilled:
        return False, f"Fill in application_answers.json: {unfilled[0]}"
    cv = choose_cv(job.get("job_title"))
    if not cv or not cv_exists(cv):
        return False, "No CV for this title: set CV_DEFAULT or CV_KEYWORD_ROUTES"
    return True, None
