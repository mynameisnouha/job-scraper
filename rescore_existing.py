import time
import json
import logging
import os
from typing import Optional

import config
import supabase_utils
from llm_client import primary_client
from models import ScoreBreakdown
from score_jobs import format_resume_to_text, get_resume_score_from_ai

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def construct_job_url(provider: str, job_id: str) -> Optional[str]:
    """Construct a job URL from provider and job_id."""
    provider = (provider or "").lower().strip()
    if provider == "linkedin":
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    return None


def process_backfill():
    """Main function to backfill job_url and rescore existing jobs with old scores."""
    logging.info("=== Starting Backfill + Rescore for Existing Jobs ===")
    overall_start = time.time()

    # ── Load base resume ──────────────────────────────────────────────
    resume_path = getattr(config, 'BASE_RESUME_PATH', 'resume.json')
    resume_data = supabase_utils.get_base_resume()

    if resume_data:
        logging.info("Loaded base resume from Supabase.")
    elif os.path.exists(resume_path):
        logging.info(f"Fallback to local resume: {resume_path}")
        try:
            with open(resume_path, 'r', encoding='utf-8') as f:
                resume_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read {resume_path}: {e}")
            resume_data = None
    else:
        logging.error("No base resume found in Supabase or locally.")
        resume_data = None

    resume_text = format_resume_to_text(resume_data) if resume_data else None
    have_llm = config.LLM_API_KEY is not None and resume_text is not None

    # ── Fetch jobs needing backfill ───────────────────────────────────
    limit = getattr(config, 'JOBS_TO_SCORE_PER_RUN', 5)
    jobs = supabase_utils.get_jobs_needing_backfill(limit)

    if not jobs:
        logging.info("No jobs need backfill or rescore. Exiting.")
        return

    logging.info(f"Fetched {len(jobs)} jobs for processing.")

    url_backfilled = 0
    url_skipped = 0
    rescored = 0
    rescore_skipped = 0

    for i, job in enumerate(jobs):
        job_id = job.get("job_id")
        if not job_id:
            continue

        logging.info(f"[{i+1}/{len(jobs)}] Processing job_id={job_id} | {job.get('job_title')} @ {job.get('company')}")

        # ── Step A: backfill job_url if missing ──────────────────
        if not job.get("job_url"):
            provider = job.get("provider", "")
            constructed = construct_job_url(provider, job_id)
            if constructed:
                ok = supabase_utils.update_job_url(job_id, constructed)
                if ok:
                    url_backfilled += 1
                    job["job_url"] = constructed  # keep local copy for scoring log
                else:
                    url_skipped += 1
            else:
                logging.info(f"  Cannot construct URL for provider='{provider}', skipping URL backfill.")
                url_skipped += 1
        else:
            url_skipped += 1

        # ── Step B: rescore if old score (null resume_score_stage) ──
        has_old_score = job.get("resume_score") is not None and job.get("resume_score_stage") is None

        if have_llm and has_old_score:
            score = get_resume_score_from_ai(resume_text, job)
            if score is not None:
                ok = supabase_utils.update_job_score(job_id, score, resume_score_stage="initial")
                if ok:
                    verified = supabase_utils.verify_job_score_update(job_id, score, "initial")
                    if not verified:
                        logging.warning(f"  Score write for job_id {job_id} appeared to succeed but read-back mismatch!")
                    rescored += 1
                else:
                    rescore_skipped += 1
            else:
                rescore_skipped += 1
        elif has_old_score and not have_llm:
            logging.warning(f"  Job {job_id} needs rescore but LLM is not configured. Skipping.")
            rescore_skipped += 1
        else:
            rescore_skipped += 1

        if i < len(jobs) - 1:
            time.sleep(config.LLM_REQUEST_DELAY_SECONDS if have_llm and has_old_score else 0.5)

    # ── Summary ───────────────────────────────────────────────────────
    elapsed = time.time() - overall_start
    logging.info("=" * 50)
    logging.info("BACKFILL + RESCORE SUMMARY")
    logging.info(f"  Total jobs processed:     {len(jobs)}")
    logging.info(f"  URL backfilled:           {url_backfilled}")
    logging.info(f"  URL skipped (exists/err): {url_skipped}")
    logging.info(f"  Rescored (new system):    {rescored}")
    logging.info(f"  Rescore skipped:          {rescore_skipped}")
    logging.info(f"  Total time:               {elapsed:.2f}s")
    logging.info("=" * 50)


if __name__ == "__main__":
    process_backfill()
