"""Xing job search, read from the public pages with plain HTTP.

Probed 2026-10-17 from a laptop and from a GitHub Actions runner: both get the
full search results and job pages, no challenge.

Search results are server-rendered cards. A card whose link leaves Xing (the
"Externes Job-Angebot" partner ads, which go through an aggregator) is not a Xing
posting and is skipped. Each job page carries a schema.org JobPosting block with
the title, ad text, date, employer and address, and an inline state blob with the
employer's own apply URL. That blob holds the apply URLs of several jobs (the
"similar jobs" rail), so the one taken is the one following this job's own
posting id, never simply the first on the page.
"""

import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import config
from sources import ats_detect, dedup

SOURCE = "xing"
SEARCH_URL = "https://www.xing.com/jobs/search?keywords={}&sort=date&sincePeriod={}"
BASE_URL = "https://www.xing.com"
# A current browser string: the shared user-agent pool is old enough that a site
# could reasonably serve it a different page.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

_NATIVE_LINK = re.compile(r"^(?:https://www\.xing\.com)?/jobs/[a-z0-9-]+-(\d+)$")
_MORE_PLACES = re.compile(r"\s*\+\s*\d+\s+weitere\s*$")
_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_APPLY_URL = re.compile(r'"applyUrl":"((?:[^"\\]|\\.)*)"')
_POSTING_ID = re.compile(r"jobs:posting:(\d+)")


def _delay() -> None:
    time.sleep(random.uniform(3.0, 8.0))


def _get(url: str):
    return requests.get(url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT)


def _text(response) -> str:
    # Xing serves UTF-8 without saying so in a way requests trusts; decoding the
    # bytes directly avoids "MÃ¼nchen".
    return (response.content or b"").decode("utf-8", errors="replace")


def search(query: str, outcome=None) -> List[Dict[str, Any]]:
    """Native Xing result cards for one query: id, url, title, company, location."""
    period = getattr(config, "XING_SINCE_PERIOD", "LAST_24_HOURS")
    url = SEARCH_URL.format(quote(query), period)
    try:
        response = _get(url)
    except requests.RequestException as e:
        logging.warning(f"Xing search '{query}' failed: {e}")
        if outcome is not None:
            outcome.record_attempt(f"xing:{query}", error=type(e).__name__)
        return []
    if response.status_code != 200:
        if outcome is not None:
            outcome.record_attempt(f"xing:{query}", status_code=response.status_code,
                                   body_bytes=len(response.content or b""))
        return []

    cards = []
    for card in BeautifulSoup(_text(response), "html.parser").select('[data-testid="job-search-result"]'):
        link = card.find("a", href=True)
        match = _NATIVE_LINK.match(link["href"]) if link else None
        if not match:
            continue
        title = card.select_one('[data-testid="job-teaser-list-title"]')
        lines = [p.get_text(" ", strip=True) for p in card.find_all("p")]
        cards.append({
            "id": match.group(1),
            "url": BASE_URL + link["href"] if link["href"].startswith("/") else link["href"],
            "title": title.get_text(" ", strip=True) if title else None,
            "company": lines[0] if lines else None,
            "location": _MORE_PLACES.sub("", lines[1]) if len(lines) > 1 else None,
        })
    if outcome is not None:
        outcome.record_attempt(f"xing:{query}", status_code=200,
                               body_bytes=len(response.content or b""), items_parsed=len(cards))
    return cards


def fetch_detail(url: str) -> Optional[str]:
    try:
        response = _get(url)
    except requests.RequestException as e:
        logging.warning(f"Xing job page {url} failed: {e}")
        return None
    return _text(response) if response.status_code == 200 else None


def _job_posting(page: str) -> Optional[Dict[str, Any]]:
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _own_apply_url(page: str, posting_id: str) -> Optional[str]:
    for match in _APPLY_URL.finditer(page):
        before = _POSTING_ID.findall(page[max(0, match.start() - 1500):match.start()])
        if not before or before[-1] != posting_id:
            continue
        try:
            url = json.loads(f'"{match.group(1)}"')
        except ValueError:
            return None
        if url.startswith(("http://", "https://")) and not ats_detect.is_job_board(url):
            return url
        return None
    return None


def _clean_description(text: Optional[str]) -> str:
    """The JSON-LD description starts with literal 'null' lines for empty sections."""
    lines = [line for line in (text or "").splitlines() if line.strip() not in ("", "null")]
    return "\n".join(lines).strip()


def parse_detail(page: str, posting_id: str) -> Dict[str, Any]:
    posting = _job_posting(page) or {}
    places = [p.get("address") or {} for p in posting.get("jobLocation") or [] if isinstance(p, dict)]
    return {
        "title": posting.get("title"),
        "description_html": _clean_description(posting.get("description")),
        "company": (posting.get("hiringOrganization") or {}).get("name"),
        "location": ", ".join(dict.fromkeys(a.get("addressLocality") for a in places
                                            if a.get("addressLocality"))) or None,
        "country": next((a.get("addressCountry") for a in places if a.get("addressCountry")), None),
        "posted_at": posting.get("datePosted"),
        "apply_url": _own_apply_url(page, posting_id),
    }


def normalize(card: Dict[str, Any], page: str) -> Optional[Dict[str, Any]]:
    """Card + job page -> the shared record. None without a readable description."""
    from sources.scraper import convert_html_to_markdown

    detail = parse_detail(page, card["id"])
    if not detail["description_html"]:
        return None
    description = convert_html_to_markdown(detail["description_html"])
    if not description:
        return None
    return {
        "job_id": f"{SOURCE}_{card['id']}",
        "job_title": detail["title"] or card.get("title"),
        "company": detail["company"] or card.get("company"),
        "location": detail["location"] or card.get("location"),
        "level": None,
        "description": description,
        "provider": SOURCE,
        "posted_at": detail["posted_at"],
        "job_url": card["url"],
        "apply_url": detail["apply_url"],
        "ats_type": ats_detect.detect(detail["apply_url"]),
        "_country": detail["country"],
    }


def _is_foreign(location: Optional[str]) -> bool:
    words = f" {(location or '').lower()} "
    return any(f" {place} " in words or f" {place}," in words
               for place in getattr(config, "XING_FOREIGN_PLACES", []))


def process_query(query: str, limit: Optional[int] = None, outcome=None) -> List[Dict[str, Any]]:
    """Search, dedup against the DB, fetch details, return records ready to save.

    Same accounting as arbeitsagentur.process_query: the caller saves what comes
    back and records the saved count, so the counters partition `fetched`.
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
        if _is_foreign(card.get("location")):
            _filtered("foreign_location")
            continue
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
        country = record.pop("_country", None)
        if country and country.upper() != "DE":
            _filtered("foreign_location")
            continue
        if is_internship_role(record.get("job_title")):
            _filtered("internship")
            continue
        if is_freelance_role(record.get("job_title"), None, record.get("description")):
            _filtered("freelance")
            continue
        record["program_type"] = program_type_of(record.get("job_title"))
        key = dedup.identity_key(record.get("company"), record.get("job_title"), record.get("location"))
        if all(key) and key in existing_keys:
            _filtered("repost_same_company_title")
            continue
        if all(key):
            existing_keys.add(key)
        records.append(record)

    logging.info(f"Xing '{query}': {len(cards)} cards, {len(records)} new job(s) ready to save.")
    return records
