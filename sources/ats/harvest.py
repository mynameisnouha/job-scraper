"""Learn companies' career feeds from the employer links the scrapers collect.

A Xing or Arbeitsagentur job whose apply link is jobs.lever.co/<name>/... names
that company's feed exactly, which beats guessing a slug from the company name
(maintenance/detect_ats.py). Every run that saves jobs with such links switches
polling on for those companies, so the `ats` source grows by itself.

Never fails a scrape: a database error is logged and nothing is learnt.
"""

import logging
from typing import Dict, Iterable, Tuple

from db import supabase_utils
from sources import ats_detect, dedup
from sources.ats import ADAPTERS


def harvest(records: Iterable[dict]) -> int:
    """Record the feed named by each record's apply_url. Returns companies newly enabled."""
    found: Dict[str, Tuple[str, str, str]] = {}
    for record in records:
        hit = ats_detect.slug_from_url(record.get("apply_url"))
        norm = dedup.normalize_company(record.get("company"))
        if hit and norm and hit[0] in ADAPTERS:
            found.setdefault(norm, (record["company"], hit[0], hit[1]))
    if not found:
        return 0
    try:
        known = {row["name_normalized"]: row for row in supabase_utils.get_all_companies()}
        new_rows = [{"name": name, "name_normalized": norm}
                    for norm, (name, _, _) in found.items() if norm not in known]
        if new_rows:
            supabase_utils.upsert_companies(new_rows)
        enabled = 0
        for norm, (_, ats_type, slug) in found.items():
            if (known.get(norm) or {}).get("ats_type"):
                continue  # a feed already found or pinned by hand wins
            if supabase_utils.record_probe(norm, ats_type, slug, "harvested"):
                enabled += 1
        if enabled:
            logging.info(f"Learnt {enabled} career feed(s) from employer links.")
        return enabled
    except Exception as e:
        logging.error(f"Could not record feeds from employer links: {e}")
        return 0
