"""
Rescore jobs whose stored breakdown is missing the calibration fields.

Run: python rescore_degraded.py [--limit 20] [--dry-run]

The normal scorer only picks up jobs with resume_score IS NULL, and
rescore_existing.py only picks up jobs with resume_score_stage IS NULL — so
neither touches a job that was scored successfully but came back without
competitive_context. Those jobs keep a score that calibration can't use.

This is also how the required-fields fix gets exercised against the live model:
every job here previously produced a degraded answer, so a clean run is real
evidence the fix holds.
"""
import argparse
import json
import logging
import os
import time

import config
import supabase_utils
from check_scoring_health import classify, missing_fields
from score_jobs import format_resume_to_text, get_resume_score_from_ai, finalize_batch_recommendations

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def load_resume_text():
    resume_data = supabase_utils.get_base_resume()
    if resume_data:
        logging.info("Loaded base resume from Supabase.")
    else:
        path = getattr(config, "BASE_RESUME_PATH", "resume.json")
        if not os.path.exists(path):
            logging.error("No base resume in Supabase or on disk — cannot rescore.")
            return None
        logging.info(f"Falling back to local resume: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                resume_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read {path}: {e}")
            return None
    return format_resume_to_text(resume_data)


def find_degraded(limit, min_score=0):
    """
    Jobs that were fully scored but came back without the required fields.

    `min_score` exists because rescoring is only worth paying for on jobs you
    might actually apply to — a repaired breakdown on a job scored 18 is a
    prediction that will never be checked against an outcome. Ordered by score
    descending so a small --limit spends the budget on the best jobs.
    """
    rows = supabase_utils.get_scored_jobs_for_health_check(limit=1000)
    degraded = [r for r in rows if classify(r.get("score_breakdown")) == "degraded"]
    logging.info(f"Found {len(degraded)} degraded job(s) out of {len(rows)} scored.")

    if min_score:
        kept = [r for r in degraded if (r.get("resume_score") or 0) >= min_score]
        logging.info(f"{len(kept)} of them score >= {min_score}; skipping the other "
                     f"{len(degraded) - len(kept)} as not worth rescoring.")
        degraded = kept

    degraded.sort(key=lambda r: r.get("resume_score") or 0, reverse=True)
    return degraded[:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="Max jobs to rescore in this run")
    parser.add_argument("--min-score", type=int, default=0,
                        help="Only rescore jobs scoring at least this — below it you'd never apply, "
                             "so the repaired prediction would never be checked")
    parser.add_argument("--delay", type=float, default=None,
                        help=f"Seconds between calls (default {config.LLM_REQUEST_DELAY_SECONDS} "
                             "from config). Lower it to finish sooner if your rate limit allows")
    parser.add_argument("--dry-run", action="store_true", help="List what would be rescored, change nothing")
    args = parser.parse_args()

    delay = config.LLM_REQUEST_DELAY_SECONDS if args.delay is None else args.delay
    jobs = find_degraded(args.limit, min_score=args.min_score)
    if not jobs:
        logging.info("Nothing to rescore — every fully-scored job carries the required fields.")
        return

    if args.dry_run:
        print(f"\nWould rescore {len(jobs)} job(s), ~{delay:.0f}s apart "
              f"(~{len(jobs) * (delay + 25) / 60:.0f} min including model time):")
        for job in jobs:
            print(f"  [{job.get('resume_score')}] {job.get('job_id')} — "
                  f"{(job.get('job_title') or '')[:50]} "
                  f"(missing: {', '.join(missing_fields(job.get('score_breakdown')))})")
        return

    resume_text = load_resume_text()
    if not resume_text or not config.LLM_API_KEY:
        logging.error("LLM key or resume unavailable — cannot rescore. "
                      "Run this via the GitHub workflow, which has the secrets.")
        return

    fixed = still_degraded = failed = 0
    scored = []  # (job_id, breakdown) — the P(interview) gate is batch-relative
    for i, job in enumerate(jobs, 1):
        job_id = job.get("job_id")
        logging.info(f"[{i}/{len(jobs)}] Rescoring {job_id} — {job.get('job_title')}")

        breakdown = get_resume_score_from_ai(resume_text, job)
        if breakdown is None:
            logging.warning(f"  Scoring returned nothing for {job_id}; leaving the old breakdown in place.")
            failed += 1
        else:
            payload = breakdown.model_dump()
            # The model can satisfy the schema with hollow values; only count it
            # fixed if the fields actually carry something.
            if classify(payload) == "degraded":
                logging.warning(f"  {job_id} still degraded after rescore "
                                f"(missing: {', '.join(missing_fields(payload))}).")
                still_degraded += 1
            else:
                fixed += 1
            if supabase_utils.update_job_score(job_id, breakdown.overall_score,
                                               resume_score_stage="initial", score_breakdown=payload):
                scored.append((job_id, breakdown))

        if i < len(jobs) and delay > 0:
            time.sleep(delay)

    finalize_batch_recommendations(scored, resume_score_stage="initial")

    logging.info("=" * 50)
    logging.info("RESCORE SUMMARY")
    logging.info(f"  Attempted:       {len(jobs)}")
    logging.info(f"  Fixed:           {fixed}")
    logging.info(f"  Still degraded:  {still_degraded}")
    logging.info(f"  Failed outright: {failed}")
    if still_degraded or failed:
        logging.info("  Any remaining failures mean the schema fix is not holding — "
                     "check the logs above for 'failed validation … retrying'.")
    logging.info("=" * 50)


if __name__ == "__main__":
    main()
