"""What every career-page feed adapter shares.

An adapter turns one company's public ATS feed into the record every other source
produces (see sources/arbeitsagentur.normalize). The steps are kept apart so the
runner can filter cheaply before paying for anything:

    fetch(slug)             one request, every raw posting at the company
    title_of / location_of  read straight off the raw posting, for the filters
    enrich(raw, slug)       a second request where the list has no ad text
    normalize(raw, ...)     raw posting -> shared record, None without a description
"""

import html
import logging
from typing import Any, Dict, List, Optional

import config

PROVIDER = "ats"


def to_record(ats_type: str, slug: str, native_id: Any, title: Optional[str],
              company: Optional[str], location: Optional[str],
              description_html: Optional[str], posted_at: Optional[str],
              url: Optional[str], apply_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The shared record. None when there is no description or URL: a posting the
    scorer cannot read still costs a screening call."""
    from sources.scraper import convert_html_to_markdown  # heavy import, only when needed

    if not description_html or not description_html.strip() or not url:
        return None
    # Greenhouse escapes its HTML as entities; unescaping first is harmless elsewhere.
    text = convert_html_to_markdown(html.unescape(description_html))
    if not text or not text.strip():
        return None
    return {
        "job_id": f"{PROVIDER}_{ats_type}_{slug}_{native_id}",
        "job_title": title,
        "company": company,
        "location": location,
        "level": None,
        "description": text,
        "provider": PROVIDER,
        "posted_at": posted_at,
        "job_url": url,
        "apply_url": apply_url or url,
        "ats_type": ats_type,
    }


def join_places(*places: Optional[str], remote: bool = False) -> Optional[str]:
    """'; '-joined non-empty places, with Remote appended when flagged and missing."""
    text = "; ".join(p for p in places if p)
    if remote and "remote" not in text.lower():
        text = f"{text}; Remote" if text else "Remote"
    return text or None


class FeedAdapter:
    """One ATS. Subclasses set ats_type, module and the field accessors."""

    ats_type: str = ""
    module = None  # the adapter's own module, whose `requests` tests patch

    def feed_url(self, slug: str) -> str:
        raise NotImplementedError

    def items(self, payload: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def id_of(self, raw: Dict[str, Any]) -> Any:
        """The native id normalize puts in job_id, readable before any detail call."""
        return raw.get("id")

    def title_of(self, raw: Dict[str, Any]) -> Optional[str]:
        raise NotImplementedError

    def company_name_of(self, raw: Dict[str, Any]) -> Optional[str]:
        """The employer name the feed itself states, where it states one. Feed
        detection compares it with the company it was looking for."""
        return None

    def location_of(self, raw: Dict[str, Any]) -> Optional[str]:
        raise NotImplementedError

    def normalize(self, raw: Dict[str, Any], slug: str,
                  company: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def parse(self, response) -> Any:
        return response.json()

    def enrich(self, raw: Dict[str, Any], slug: str, outcome=None) -> Optional[Dict[str, Any]]:
        """Most feeds already carry the ad text; those return the posting as is."""
        return raw

    def get(self, url: str):
        return self.module.requests.get(url, timeout=config.REQUEST_TIMEOUT,
                                        headers={"Accept": "application/json"})

    def fetch(self, slug: str, outcome=None) -> List[Dict[str, Any]]:
        query = f"{self.ats_type}:{slug}"
        try:
            response = self.get(self.feed_url(slug))
        except self.module.requests.RequestException as e:
            logging.warning(f"{query}: request failed: {e}")
            if outcome is not None:
                outcome.record_attempt(query, error=type(e).__name__)
            return []
        size = len(response.content or b"")
        if response.status_code != 200:
            if outcome is not None:
                outcome.record_attempt(query, status_code=response.status_code, body_bytes=size)
            return []
        try:
            items = self.items(self.parse(response)) or []
        except Exception as e:  # a malformed body is one company's problem, not the run's
            logging.warning(f"{query}: could not parse the feed: {e}")
            if outcome is not None:
                outcome.record_attempt(query, status_code=response.status_code, body_bytes=size,
                                       error=f"parse: {type(e).__name__}")
            return []
        if outcome is not None:
            outcome.record_attempt(query, status_code=response.status_code, body_bytes=size,
                                   items_parsed=len(items))
        return items
