import json
import logging
import os
import time
import uuid

import config
import supabase_utils
from score_jobs import format_resume_to_text, get_resume_score_from_ai

logger = logging.getLogger(__name__)


def load_manual_jobs() -> list[dict]:
    """Read manually-added jobs from the JSON file. Returns a list of job dicts."""
    path = config.MANUAL_JOBS_PATH
    if not os.path.exists(path):
        logger.info(f"No manual jobs file at {path}, skipping.")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read manual jobs file {path}: {e}")
        return []

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        logger.warning(f"manual_jobs.json should contain a list or object, got {type(raw).__name__}")
        return []

    valid = []
    for entry in raw:
        if entry.get("job_title") and entry.get("description"):
            entry.setdefault("company", "Unknown")
            entry.setdefault("provider", "manual")
            entry.setdefault("level", "N/A")
            entry.setdefault("location", "N/A")
            entry.setdefault("job_url", "")
            entry.setdefault("job_id", f"manual_{uuid.uuid4().hex[:12]}")
            valid.append(entry)
        else:
            logger.warning(f"Skipping manual job entry missing 'job_title' or 'description': {entry}")

    return valid


def process_manual_jobs(resume_text: str) -> tuple[int, int]:
    """Score and upsert manual jobs. Returns (success, failed) counts."""
    jobs = load_manual_jobs()
    if not jobs:
        return 0, 0

    logger.info(f"Processing {len(jobs)} manually-added jobs...")
    success = 0
    failed = 0

    for i, job in enumerate(jobs):
        job_id = job["job_id"]
        logger.info(f"[{i+1}/{len(jobs)}] Scoring manual job: {job.get('job_title')} @ {job.get('company')}")

        breakdown = get_resume_score_from_ai(resume_text, job)
        if breakdown is not None:
            job["resume_score"] = breakdown.overall_score
            job["resume_score_stage"] = "initial"
            job["score_breakdown"] = breakdown.model_dump()

        supabase_utils.save_jobs_to_supabase([job])

        if breakdown is not None:
            ok = supabase_utils.update_job_score(job_id, breakdown.overall_score, resume_score_stage="initial",
                                                 score_breakdown=breakdown.model_dump())
            if ok:
                success += 1
            else:
                failed += 1
        else:
            failed += 1

        if i < len(jobs) - 1:
            time.sleep(config.LLM_REQUEST_DELAY_SECONDS)

    return success, failed
