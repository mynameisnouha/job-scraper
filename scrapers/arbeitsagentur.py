"""Bundesagentur für Arbeit — Jobsuche API.

The largest job database in Germany, and free for employers to post to, which is
why it carries Mittelstand companies that never appear on LinkedIn. A public JSON
API: no browser, no user-agent rotation, no politeness theatre, no ToS grey zone.

Verified against the live API on 2026-09-06. Two things differ from the public
API notes and matter:

- Search results come back under `ergebnisliste`, with the total in
  `maxErgebnisse`. Only `/pc/v6/jobs` answers; v2/v4/v5 return 403.
- Details are the opposite — only `/pc/v4/jobdetails/{base64(refnr)}` answers,
  v5 and v6 return 403. The search payload carries no description at all, so the
  detail call is required, not an enrichment.

`zeitarbeit=false` excludes Zeitarbeit (staffing) postings at the source rather
than paying an LLM to recognise them later: for "Data Scientist" over 7 days it
returned 52 postings against 6 with `zeitarbeit=true`, so the flag selects rather
than merely includes.
"""

import base64
import logging
import time
from typing import Any, Dict, List, Optional

import requests

import config

SEARCH_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
DETAIL_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{}"
# Where a human opens the posting. The API's own externeURL points at whichever
# partner board syndicated it, which is not where you apply.
POSTING_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{}"

# The key is a fixed public value, not a credential — it is published in the API
# documentation and is identical for every caller.
HEADERS = {
    "X-API-Key": "jobboerse-jobsuche",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; job-scraper/1.0)",
}

SOURCE = "arbeitsagentur"


def _delay() -> None:
    seconds = getattr(config, "ARBEITSAGENTUR_REQUEST_DELAY", 0.3)
    if seconds:
        time.sleep(seconds)


def search_jobs(query: str, *, location: Optional[str] = None,
                published_since_days: Optional[int] = None,
                size: Optional[int] = None, page: int = 1,
                outcome=None) -> List[Dict[str, Any]]:
    """One search page. Returns the raw offers; [] on any failure.

    Records the HTTP status and body size on `outcome` so a source returning
    nothing can be told apart from a source being blocked (see scrape_guard).
    """
    params = {
        "was": query,
        "wo": location or getattr(config, "ARBEITSAGENTUR_LOCATION", "Deutschland"),
        "size": size or getattr(config, "ARBEITSAGENTUR_PAGE_SIZE", 100),
        "page": page,
        # false EXCLUDES staffing-firm postings. See the module docstring.
        "zeitarbeit": "false",
        "veroeffentlichtseit": (published_since_days
                                if published_since_days is not None
                                else getattr(config, "ARBEITSAGENTUR_PUBLISHED_SINCE_DAYS", 1)),
    }

    def _record(status_code=None, body_bytes=0, items_parsed=0, error=""):
        if outcome is not None:
            outcome.record_attempt(query, status_code=status_code, body_bytes=body_bytes,
                                   items_parsed=items_parsed, error=error)

    try:
        response = requests.get(SEARCH_URL, headers=HEADERS, params=params,
                                timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        status = e.response.status_code if e.response is not None else None
        logging.error(f"Arbeitsagentur search failed for '{query}': {e}")
        _record(status_code=status, body_bytes=len(body), error=f"HTTPError {e}"[:120])
        return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Arbeitsagentur search errored for '{query}': {e}")
        _record(error=f"RequestException {e}"[:120])
        return []

    try:
        payload = response.json()
    except ValueError as e:
        logging.error(f"Arbeitsagentur search for '{query}' returned non-JSON: {e}")
        _record(status_code=response.status_code, body_bytes=len(response.content),
                error="invalid JSON")
        return []

    offers = payload.get("ergebnisliste") or []
    logging.info(f"Arbeitsagentur '{query}': {len(offers)} offer(s) on page {page} "
                 f"of {payload.get('maxErgebnisse')} total.")
    _record(status_code=response.status_code, body_bytes=len(response.content),
            items_parsed=len(offers))
    return offers


def fetch_job_detail(reference_number: str) -> Optional[Dict[str, Any]]:
    """The full posting. The search payload has no description, so this is required."""
    encoded = base64.b64encode(reference_number.encode("utf-8")).decode("ascii")
    try:
        response = requests.get(DETAIL_URL.format(encoded), headers=HEADERS,
                                timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        logging.warning(f"Arbeitsagentur detail fetch failed for {reference_number}: {e}")
        return None


def _location_of(offer: Dict[str, Any]) -> Optional[str]:
    for place in offer.get("stellenlokationen") or []:
        address = place.get("adresse") or {}
        city, region = address.get("ort"), address.get("region")
        if city and region:
            return f"{city}, {region.replace('_', ' ').title()}"
        if city:
            return city
    return None


def _facts_block(offer: Dict[str, Any], detail: Dict[str, Any]) -> str:
    """A short header prepended to the description.

    The `jobs` table has no salary or remote column, and adding one is a schema
    change that needs sign-off, so the structured fields the API gives us are put
    where the scorer already looks for them: the description text. It extracts
    salary_band and remote_scope from the JD, and these are more reliable than
    the same facts inferred from prose.
    """
    merged = {**offer, **{k: v for k, v in detail.items() if v is not None}}
    facts = []

    low, high = merged.get("gehaltsspanneVon"), merged.get("gehaltsspanneBis")
    if low and high:
        facts.append(f"Gehaltsspanne: {int(low):,} – {int(high):,} EUR "
                     f"({merged.get('verguetungsangabe', 'JAHRESGEHALT').lower()})".replace(",", "."))
    elif low:
        facts.append(f"Gehalt ab: {int(low):,} EUR".replace(",", "."))

    if merged.get("homeofficemoeglich"):
        home_office_type = (merged.get("homeofficetyp") or "").replace("_", " ").title()
        facts.append(f"Homeoffice: möglich{f' ({home_office_type})' if home_office_type else ''}")
    elif merged.get("homeofficemoeglich") is False:
        facts.append("Homeoffice: nicht angegeben")

    if merged.get("arbeitszeitVollzeit"):
        facts.append("Arbeitszeit: Vollzeit")
    if merged.get("vertragsdauer") and merged["vertragsdauer"] != "KEINE_ANGABE":
        facts.append(f"Vertragsdauer: {merged['vertragsdauer'].replace('_', ' ').lower()}")
    if merged.get("eintrittszeitraum", {}).get("von"):
        facts.append(f"Eintritt ab: {merged['eintrittszeitraum']['von']}")
    if merged.get("istPrivateArbeitsvermittlung"):
        facts.append("Hinweis: private Arbeitsvermittlung (kein Direktarbeitgeber)")

    if not facts:
        return ""
    return "## Rahmendaten\n\n" + "\n".join(f"- {fact}" for fact in facts) + "\n\n---\n\n"


def normalize(offer: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Search offer + detail -> the record every other source produces.

    Returns None when there is no description: a posting the scorer cannot read
    is worse than no posting, because it still costs an LLM call.
    """
    reference_number = offer.get("referenznummer")
    if not reference_number:
        return None

    detail = detail or {}
    description = (detail.get("stellenangebotsBeschreibung") or "").strip()
    if not description:
        return None

    published = offer.get("datumErsteVeroeffentlichung") or \
        (offer.get("veroeffentlichungszeitraum") or {}).get("von")

    return {
        "job_id": f"{SOURCE}_{reference_number}",
        "job_title": (detail.get("stellenangebotsTitel")
                      or offer.get("stellenangebotsTitel")
                      or offer.get("hauptberuf")),
        "company": detail.get("firma") or offer.get("firma"),
        "location": _location_of(detail) or _location_of(offer),
        # The API has no seniority field. Left None rather than guessed from the
        # title, which is what the scorer would do anyway, only worse.
        "level": None,
        "description": _facts_block(offer, detail) + description,
        "provider": SOURCE,
        "posted_at": published,
        "job_url": POSTING_URL.format(reference_number),
    }


def process_query(query: str, limit: Optional[int] = None, outcome=None) -> List[Dict[str, Any]]:
    """Search, dedup against the DB, fetch details, return records ready to save.

    Mirrors process_linkedin_query: the caller saves what comes back and records
    the saved count, so the counters partition `fetched` (see scrape_guard).
    """
    import supabase_utils  # local import: keeps this module importable without credentials
    from scraper import is_freelance_role, is_internship_role

    def _filtered(reason, count=1):
        if outcome is not None:
            outcome.record_filtered(reason, count)

    offers = search_jobs(query, outcome=outcome)
    if not offers:
        return []

    # Same posting can be returned under several queries within one run.
    unique_offers, seen = [], set()
    for offer in offers:
        reference_number = offer.get("referenznummer")
        if not reference_number:
            _filtered("missing_reference_number")
            continue
        if reference_number in seen:
            continue
        seen.add(reference_number)
        unique_offers.append(offer)

    try:
        existing_ids, _ = supabase_utils.get_existing_jobs_from_supabase()
    except Exception as e:
        logging.error(f"Could not read existing jobs; treating everything as new: {e}")
        existing_ids = set()

    candidates, already_in_db = [], 0
    for offer in unique_offers:
        if f"{SOURCE}_{offer['referenznummer']}" in existing_ids:
            already_in_db += 1
            continue
        candidates.append(offer)

    if outcome is not None:
        outcome.record_query(fetched=len(unique_offers), already_in_db=already_in_db)

    logging.info(f"Arbeitsagentur '{query}': {len(unique_offers)} unique, "
                 f"{already_in_db} already stored, {len(candidates)} to fetch.")

    if limit is not None and len(candidates) > limit:
        _filtered("over_per_query_limit", len(candidates) - limit)
        candidates = candidates[:limit]

    records = []
    for offer in candidates:
        # ARBEIT is a regular job; AUSBILDUNG and the rest are training places.
        if offer.get("stellenangebotsart") and offer["stellenangebotsart"] != "ARBEIT":
            _filtered("not_a_regular_job")
            continue

        _delay()
        detail = fetch_job_detail(offer["referenznummer"])
        if detail is None:
            _filtered("detail_fetch_failed")
            continue

        record = normalize(offer, detail)
        if record is None:
            _filtered("no_description")
            continue
        if is_internship_role(record.get("job_title"), record.get("level")):
            logging.info(f"Skipping internship/thesis job: {record.get('job_title')}")
            _filtered("internship")
            continue
        if is_freelance_role(record.get("job_title"), record.get("level")):
            logging.info(f"Skipping freelance/contract job: {record.get('job_title')}")
            _filtered("freelance")
            continue
        records.append(record)

    logging.info(f"Arbeitsagentur '{query}': {len(records)} new job(s) ready to save.")
    return records
