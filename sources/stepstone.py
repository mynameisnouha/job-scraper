"""StepStone job search, read from the public pages with plain HTTP.

Probed 2026-10-17 from a laptop and from a GitHub Actions runner: both get the
full result list and job pages.

Result cards are server-rendered with stable `data-at` attributes; each job page
carries a schema.org JobPosting block with the ad text. StepStone's apply control
("Ich bin interessiert") is either a redirect to the employer or a one-click Quick
Apply that submits at once, and nothing on the page tells them apart reliably, so
this module never follows it and stores no apply URL. The employer link for a
StepStone job comes from another source carrying the same posting.
"""

import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import config
from sources import dedup, jsonld

SOURCE = "stepstone"
BASE_URL = "https://www.stepstone.de"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
_POSTING_ID = re.compile(r"--(\d+)-inline\.html")


def _delay() -> None:
    time.sleep(random.uniform(3.0, 8.0))


def _get(url: str):
    return requests.get(url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT)


def _text(response) -> str:
    return (response.content or b"").decode("utf-8", errors="replace")


def search_url(query: str) -> str:
    """Newest first (sort=2), last 24 hours (ag=age_1)."""
    slug = quote(re.sub(r"\s+", "-", query.strip().lower()))
    return f"{BASE_URL}/jobs/{slug}?sort=2&ag=age_1"


def search(query: str, outcome=None) -> List[Dict[str, Any]]:
    """Result cards for one query: id, url, title, company, location, posted_at."""
    from sources.scraper import parse_relative_posted_time

    try:
        response = _get(search_url(query))
    except requests.RequestException as e:
        logging.warning(f"StepStone search '{query}' failed: {e}")
        if outcome is not None:
            outcome.record_attempt(f"stepstone:{query}", error=type(e).__name__)
        return []
    if response.status_code != 200:
        if outcome is not None:
            outcome.record_attempt(f"stepstone:{query}", status_code=response.status_code,
                                   body_bytes=len(response.content or b""))
        return []

    def text_of(card, at):
        element = card.select_one(f'[data-at="{at}"]')
        return element.get_text(" ", strip=True) if element else None

    cards = []
    for card in BeautifulSoup(_text(response), "html.parser").select('article[data-at="job-item"]'):
        link = card.select_one('a[data-at="job-item-title"]') or card.find("a", href=_POSTING_ID)
        href = link.get("href") if link else None
        match = _POSTING_ID.search(href or "")
        if not match:
            continue
        cards.append({
            "id": match.group(1),
            "url": BASE_URL + href if href.startswith("/") else href,
            "title": text_of(card, "job-item-title"),
            "company": text_of(card, "job-item-company-name"),
            "location": text_of(card, "job-item-location"),
            "posted_at": parse_relative_posted_time(text_of(card, "job-item-timeago")),
        })
    if outcome is not None:
        outcome.record_attempt(f"stepstone:{query}", status_code=200,
                               body_bytes=len(response.content or b""), items_parsed=len(cards))
    return cards


def fetch_detail(url: str) -> Optional[str]:
    try:
        response = _get(url)
    except requests.RequestException as e:
        logging.warning(f"StepStone job page {url} failed: {e}")
        return None
    return _text(response) if response.status_code == 200 else None


def normalize(card: Dict[str, Any], page: str) -> Optional[Dict[str, Any]]:
    """Card + job page -> the shared record. None without a readable description."""
    from sources.scraper import convert_html_to_markdown

    posting = jsonld.job_posting(page)
    if not posting or not (posting.get("description") or "").strip():
        return None
    description = convert_html_to_markdown(posting["description"])
    if not description:
        return None
    return {
        "job_id": f"{SOURCE}_{card['id']}",
        "job_title": posting.get("title") or card.get("title"),
        "company": (posting.get("hiringOrganization") or {}).get("name") or card.get("company"),
        "location": jsonld.locality(posting) or card.get("location"),
        "level": None,
        "description": description,
        "provider": SOURCE,
        "posted_at": posting.get("datePosted") or card.get("posted_at"),
        "job_url": card["url"],
    }


def process_query(query: str, limit: Optional[int] = None, outcome=None) -> List[Dict[str, Any]]:
    """Search, dedup against the DB, fetch details, return records ready to save.

    Same accounting as arbeitsagentur.process_query.
    """
    from db import supabase_utils
    from sources.scraper import is_freelance_role, is_internship_role, program_type_of

    def _filtered(reason, count=1):
        if outcome is not None:
            outcome.record_filtered(reason, count)

    cards = search(query, outcome=outcome)
    if not cards:
        return []
    try:
        existing_ids, existing_keys = supabase_utils.get_existing_jobs_from_supabase()
    except Exception as e:
        logging.error(f"Could not read existing jobs; treating everything as new: {e}")
        existing_ids, existing_keys = set(), set()

    candidates = [c for c in cards if f"{SOURCE}_{c['id']}" not in existing_ids]
    if outcome is not None:
        outcome.record_query(fetched=len(cards), already_in_db=len(cards) - len(candidates))
    if limit is not None and len(candidates) > limit:
        _filtered("over_per_query_limit", len(candidates) - limit)
        candidates = candidates[:limit]

    records = []
    for card in candidates:
        key = dedup.identity_key(card.get("company"), card.get("title"), card.get("location"))
        if all(key) and key in existing_keys:
            _filtered("repost_same_company_title")
            continue
        _delay()
        page = fetch_detail(card["url"])
        if page is None:
            _filtered("detail_fetch_failed")
            continue
        record = normalize(card, page)
        if record is None:
            _filtered("no_description")
            continue
        if is_internship_role(record.get("job_title")):
            _filtered("internship")
            continue
        if is_freelance_role(record.get("job_title"), None, record.get("description")):
            _filtered("freelance")
            continue
        record["program_type"] = program_type_of(record.get("job_title"))
        # Re-checked on the page's own employer and title: the card often shows the
        # agency that placed the ad ("meinestadt.de") rather than the employer.
        key = dedup.identity_key(record.get("company"), record.get("job_title"), record.get("location"))
        if all(key) and key in existing_keys:
            _filtered("repost_same_company_title")
            continue
        if all(key):
            existing_keys.add(key)
        records.append(record)

    logging.info(f"StepStone '{query}': {len(cards)} cards, {len(records)} new job(s) ready to save.")
    return records
