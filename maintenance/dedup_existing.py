"""Collapse duplicate job rows that are already in the database.

Dedup at scrape time stops new duplicates; this clears the ones already stored.
Dry run by default — it prints what it would do and changes nothing. `--apply`
writes, and only then, after backing up every row it is about to delete.

    python dedup_existing.py                # report only
    python dedup_existing.py --apply        # merge, with a backup written first
"""

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import config
from sources import dedup
from db import supabase_utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BACKUP_DIR = "output/dedup_backups"


BASE_COLUMNS = ("job_id, provider, company, job_title, location, description, "
                "job_url, scraped_at, resume_score, application_stage, status, score_breakdown")


def fetch_all_jobs() -> tuple:
    """(rows, alt_sources_exists).

    The report is useful before add_alt_sources.sql has been run — that migration
    needs a human in the Supabase SQL editor — so a missing column degrades to a
    read-only run rather than a crash.
    """
    columns = BASE_COLUMNS + ", alt_sources"
    has_alt_sources = True
    rows, page, size = [], 0, 1000
    while True:
        try:
            response = (supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
                        .select(columns)
                        .range(page * size, page * size + size - 1).execute())
        except Exception as e:
            if has_alt_sources and "alt_sources" in str(e):
                logging.warning("No alt_sources column yet — run "
                                "supabase_setup/add_alt_sources.sql before --apply. "
                                "Reporting without it.")
                columns, has_alt_sources = BASE_COLUMNS, False
                continue
            raise
        rows.extend(response.data or [])
        if len(response.data or []) < size:
            break
        page += 1
    return rows, has_alt_sources


def back_up(rows: list) -> str:
    """Write the rows about to be deleted to disk first.

    A merge is irreversible against a live table, and the losing rows carry scores
    that cost money to produce. The backup is cheap insurance.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(BACKUP_DIR, f"deleted_duplicates_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2, default=str)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually merge. Without this the script only reports.")
    parser.add_argument("--threshold", type=float, default=dedup.DESCRIPTION_SIMILARITY_THRESHOLD,
                        help="Description-similarity floor for a looser match.")
    args = parser.parse_args(argv)

    jobs, has_alt_sources = fetch_all_jobs()
    logging.info(f"Fetched {len(jobs)} rows.")

    groups = dedup.group_duplicates(jobs, threshold=args.threshold)
    duplicate_rows = sum(len(group) - 1 for group in groups)
    logging.info(f"{len(groups)} duplicate group(s) covering {sum(len(g) for g in groups)} rows; "
                 f"{duplicate_rows} row(s) would be removed "
                 f"({100 * duplicate_rows / max(len(jobs), 1):.1f}% of the corpus).")

    cross_source = [g for g in groups if len({row.get("provider") for row in g}) > 1]
    touching_applications = [g for g in groups
                             if any(dedup.has_application_history(row) for row in g)]
    logging.info(f"  cross-source: {len(cross_source)} | "
                 f"involving an application: {len(touching_applications)}")

    to_delete, updates = [], []
    for group in groups:
        merged = dedup.merge_group(group)
        survivor = merged["survivor"]
        existing_alts = survivor.get("alt_sources") or []
        known_urls = {alt.get("job_url") for alt in existing_alts}
        new_alts = existing_alts + [alt for alt in merged["alt_sources"]
                                    if alt.get("job_url") not in known_urls]

        payload = {"alt_sources": new_alts}
        if merged["apply_url"]:
            payload["job_url"] = merged["apply_url"]
        updates.append((survivor, payload))
        to_delete.extend(merged["duplicates"])

        logging.info(
            f"  keep {survivor.get('provider')}/{survivor.get('job_id')} "
            f"({survivor.get('company')} — {survivor.get('job_title')}) "
            f"+{len(merged['duplicates'])} merged"
            + ("  [applied]" if dedup.has_application_history(survivor) else "")
            + (f"  url->{merged['apply_url']}" if merged["apply_url"] else "")
        )

    if not args.apply:
        logging.info("Dry run — nothing written. Re-run with --apply to merge.")
        return 0

    if not has_alt_sources:
        logging.error("Refusing to merge: the alt_sources column does not exist, so the "
                      "duplicate URLs would be deleted rather than recorded. Run "
                      "supabase_setup/add_alt_sources.sql first.")
        return 1

    if to_delete:
        path = back_up(to_delete)
        logging.info(f"Backed up {len(to_delete)} row(s) to {path}")

    merged_count = 0
    for survivor, payload in updates:
        try:
            (supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
             .update(payload).eq("job_id", survivor["job_id"]).execute())
            merged_count += 1
        except Exception as e:
            logging.error(f"Could not update survivor {survivor['job_id']}: {e}")

    deleted = 0
    for row in to_delete:
        try:
            (supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
             .delete().eq("job_id", row["job_id"]).execute())
            deleted += 1
        except Exception as e:
            logging.error(f"Could not delete duplicate {row['job_id']}: {e}")

    logging.info(f"Merged {merged_count} survivor(s), deleted {deleted} duplicate row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
