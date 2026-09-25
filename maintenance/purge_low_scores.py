"""Delete the postings that scored too low to ever be worth reading.

Run it with ``python -m maintenance.purge_low_scores``. It runs in the pipeline
after scoring, so the corpus stops accumulating rows nobody will open: on this
database 1048 of 1872 rows scored under 20, and 1028 of those were rejected by
the cheap screen before the full scorer ever saw them.

Three rules make an automatic delete safe enough to leave unattended.

**It only removes rows nobody ever acted on.** `status` must still be `new`,
with no application date and no dismissal. An applied job is a record of
something you did; a dismissal is a decision, and the reason attached to it is
what the skip statistics are built from. A low score is not a reason to erase
either, so both are excluded whatever they scored.

**Every delete leaves a tombstone**, as the manual delete does - the scrapers
read those keys, so a purged posting does not come back on the next scrape and
get re-screened at your expense. The tombstone also carries the score and the
language fields, which is what keeps the corpus-wide German picture answerable
after the rows are gone.

**It is capped per run.** A bug that widened the filter would otherwise empty the
table in one pass. The cap is generous enough to clear a normal backlog over a
few runs and small enough that a mistake is visible before it is total.
"""

import argparse
import json
import logging
from typing import Any, Dict, List

import config
from db import supabase_utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# config._int_env, reused rather than re-imported so this script stays runnable
# with nothing but Supabase credentials. An undefined repository variable reaches
# the runner as the empty string, not as an unset name, so a bare int() on it
# would crash the step on every database that had not set one.
DEFAULT_MIN_SCORE = config._int_env("PURGE_MIN_SCORE", 20)

# Ceiling per run. Not a performance limit - a blast radius. Each delete is two
# round trips, so a few hundred is a minute or so of workflow time.
DEFAULT_LIMIT = config._int_env("PURGE_LIMIT", 400)

REASON = "below_score_floor"


def find_purgeable(min_score: int, limit: int) -> List[Dict[str, Any]]:
    """Untouched postings scoring below the floor, lowest first.

    Lowest first so that if the cap bites, what goes is the worst of the backlog
    rather than an arbitrary page of it.
    """
    try:
        query = (
            supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
            .select("job_id, job_title, company, resume_score, score_breakdown")
            .lt("resume_score", min_score)
            .eq("status", "new")
            .is_("application_date", None)
            .order("resume_score", desc=False)
            .limit(limit)
        )
        try:
            response = query.is_("dismissed_at", None).execute()
        except Exception as exc:  # noqa: BLE001
            # Pre-add_dismissal.sql databases have no such column. Without it a
            # dismissed row could be purged, losing its reason - so say so rather
            # than quietly widening what this deletes.
            if "dismissed_at" not in str(exc):
                raise
            logging.warning("No dismissed_at column; dismissals cannot be excluded.")
            response = query.execute()
        return response.data or []
    except Exception as exc:  # noqa: BLE001
        logging.error(f"Could not list purgeable jobs: {exc}")
        return []


def purge(min_score: int = DEFAULT_MIN_SCORE, limit: int = DEFAULT_LIMIT,
          dry_run: bool = False) -> Dict[str, int]:
    """Delete what the filter finds. Returns counts for the workflow log."""
    rows = find_purgeable(min_score, limit)
    if not rows:
        logging.info(f"Nothing scoring under {min_score} to purge.")
        return {"found": 0, "deleted": 0, "failed": 0}

    scores = [r.get("resume_score") for r in rows if r.get("resume_score") is not None]
    logging.info(
        "Purging %d posting(s) scoring under %d (range %s-%s)%s.",
        len(rows), min_score, min(scores, default="?"), max(scores, default="?"),
        " — DRY RUN, nothing will be deleted" if dry_run else "",
    )

    deleted = failed = 0
    for row in rows:
        label = f"{row.get('job_title')} @ {row.get('company')} ({row.get('resume_score')})"
        if dry_run:
            logging.info(f"  would delete: {label}")
            continue
        if supabase_utils.delete_job(row, REASON,
                                     note=f"auto-purged: scored under {min_score}"):
            deleted += 1
        else:
            failed += 1
            logging.warning(f"  failed to delete: {label}")

    logging.info(f"Purge finished: {deleted} deleted, {failed} failed, {len(rows)} matched.")
    if failed:
        logging.warning("Failures usually mean supabase_setup/add_deleted_jobs.sql has "
                        "not been run: without the tombstone table nothing is deleted.")
    return {"found": len(rows), "deleted": deleted, "failed": failed}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete postings that scored too low to read.")
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE,
                        help=f"Delete scores strictly below this (default {DEFAULT_MIN_SCORE}).")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Most postings to delete in one run (default {DEFAULT_LIMIT}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would go without deleting anything.")
    args = parser.parse_args(argv)

    if args.min_score > 50:
        # A floor this high is not a purge, it is the queue. Refused rather than
        # confirmed, because this runs unattended where no one can answer.
        logging.error(f"Refusing to purge everything under {args.min_score}: that is most "
                      "of the corpus. Lower --min-score, or delete by hand in the app.")
        return 2

    counts = purge(args.min_score, args.limit, args.dry_run)
    print(json.dumps(counts))
    return 0 if not counts["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
