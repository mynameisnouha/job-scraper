"""The `ats` source: poll every company with a known career feed.

Per company: one feed request, then the cheap checks in order of cost — already
stored, on-topic title, German location, internship, same posting from another
source — and only then the detail request some feeds need, the freelance check on
the full text, and the save. Every fetched posting lands in exactly one
scrape_guard bucket, so `fetched = already_in_db + filtered + new`.

A run with no companies to poll fetches nothing and is reported BROKEN: an empty
companies table is a setup problem (run `python -m maintenance.detect_ats`), not
a quiet day.
"""

import logging
import time
from typing import Any, Dict, List

import config
from db import supabase_utils
from sources import dedup, intent, scrape_guard
from sources.ats import ADAPTERS
from sources.ats.base import PROVIDER


def _fill_apply_url(job_id: str, adapter, raw: Dict[str, Any], slug: str, name: str,
                    outcome: scrape_guard.SourceOutcome) -> None:
    """A stored job (usually LinkedIn's, whose guest page hides the employer link)
    gets this feed's apply URL for the same posting."""
    detailed = adapter.enrich(raw, slug, outcome=outcome)
    record = adapter.normalize(detailed, slug, name) if detailed else None
    if record and record.get("apply_url"):
        supabase_utils.set_apply_url(job_id, record["apply_url"], record.get("ats_type"))


def _poll_company(company: Dict[str, Any], adapter, outcome: scrape_guard.SourceOutcome,
                  existing_ids: set, existing_keys: set,
                  missing_links: Dict[tuple, str]) -> List[Dict[str, Any]]:
    from sources.scraper import is_freelance_role, is_internship_role, program_type_of

    slug, name = company["ats_slug"], company.get("name")
    raws = adapter.fetch(slug, outcome=outcome)
    records, already = [], 0
    for raw in raws:
        job_id = f"{PROVIDER}_{adapter.ats_type}_{slug}_{adapter.id_of(raw)}"
        if job_id in existing_ids:
            already += 1
            continue
        title, location = adapter.title_of(raw), adapter.location_of(raw)
        if not intent.matches_search_intent(title):
            outcome.record_filtered("off_target_title")
            continue
        if not intent.in_target_location(location):
            outcome.record_filtered("off_target_location")
            continue
        if is_internship_role(title):
            outcome.record_filtered("internship")
            continue
        key = dedup.identity_key(name, title, location)
        if all(key) and key in existing_keys:
            outcome.record_filtered("repost_same_company_title")
            if key in missing_links:
                _fill_apply_url(missing_links.pop(key), adapter, raw, slug, name, outcome)
            continue
        detailed = adapter.enrich(raw, slug, outcome=outcome)
        if detailed is None:
            outcome.record_filtered("detail_fetch_failed")
            continue
        record = adapter.normalize(detailed, slug, name)
        if record is None:
            outcome.record_filtered("no_description")
            continue
        if is_freelance_role(record.get("job_title"), None, record.get("description")):
            outcome.record_filtered("freelance")
            continue
        record["company_id"] = company.get("id")
        record["program_type"] = program_type_of(record.get("job_title"))
        if all(key):
            existing_keys.add(key)  # two copies inside one run collapse to one row
        existing_ids.add(job_id)
        records.append(record)
    outcome.record_query(fetched=len(raws), already_in_db=already)
    return records


def run_ats() -> scrape_guard.SourceOutcome:
    outcome = scrape_guard.SourceOutcome(PROVIDER)
    companies = supabase_utils.get_companies_to_poll()
    if not companies:
        logging.error("No companies to poll. Run `python -m maintenance.detect_ats` first.")
        return outcome

    existing_ids, existing_keys = supabase_utils.get_existing_jobs_from_supabase()
    missing_links = supabase_utils.get_jobs_missing_apply_url_by_key()
    delay = getattr(config, "ATS_COMPANY_DELAY_SECONDS", 1)
    for index, company in enumerate(companies):
        adapter = ADAPTERS.get(company.get("ats_type"))
        if adapter is None:
            logging.warning(f"No adapter for ATS '{company.get('ats_type')}' ({company.get('name')}). Skipping.")
            continue
        if index:
            time.sleep(delay)
        records = _poll_company(company, adapter, outcome, existing_ids, existing_keys, missing_links)
        if records:
            saved = supabase_utils.save_jobs_to_supabase(records)
            outcome.record_query(new=saved)
            outcome.record_filtered(scrape_guard.SAVE_FAILED, len(records) - saved)
        supabase_utils.mark_polled(company.get("id"))
        logging.info(f"{company.get('name')} ({adapter.ats_type}:{company.get('ats_slug')}): "
                     f"{len(records)} new posting(s).")
    return outcome
