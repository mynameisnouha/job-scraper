"""Give stored jobs an apply_url where their job_url already is an ATS form.

    python -m maintenance.backfill_apply_url             # report only
    python -m maintenance.backfill_apply_url --apply     # write

No network and no LLM: the URL is classified by sources/ats_detect.py. Only a
recognised ATS counts; an employer's own site ("other") may be a careers landing
page rather than the form, so it is left for a source that knows better.
"""

import argparse
import logging
import sys
from typing import List, Optional, Tuple

import config
from db import supabase_utils
from sources import ats_detect


def fetch_rows_without_apply_url(limit: int) -> list:
    response = supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)\
                                      .select("job_id, job_url")\
                                      .is_("apply_url", "null")\
                                      .not_.is_("job_url", "null")\
                                      .limit(limit)\
                                      .execute()
    return response.data or []


def plan_updates(rows: list) -> List[Tuple[str, str, str]]:
    """(job_id, apply_url, ats_type) for each row whose job_url is a known ATS."""
    updates = []
    for row in rows:
        ats_type = ats_detect.detect(row.get("job_url"))
        if ats_type and ats_type != "other":
            updates.append((str(row["job_id"]), row["job_url"], ats_type))
    return updates


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill apply_url from job_url where it is an ATS form.")
    parser.add_argument("--apply", action="store_true", help="Write the updates (default: report only).")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)

    updates = plan_updates(fetch_rows_without_apply_url(args.limit))
    for job_id, url, ats_type in updates:
        print(f"{job_id}: {ats_type}  {url}")
    if not args.apply:
        print(f"Dry run: would update {len(updates)} row(s). Re-run with --apply to write.")
        return 0
    written = sum(1 for update in updates if supabase_utils.set_apply_url(*update))
    print(f"Done: updated {written} of {len(updates)} row(s).")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
