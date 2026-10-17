"""SmartRecruiters posting API.

The list (api.smartrecruiters.com/v1/companies/{slug}/postings) has no ad text, so
the description comes from one detail call per posting, made only after the cheap
title/location filters (see FeedAdapter.enrich). Large employers list thousands of
postings, so paging stops at config.ATS_SMARTRECRUITERS_MAX_PAGES.
"""
import logging
import sys

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

import config
from sources.ats.base import FeedAdapter, join_places, to_record

PAGE_SIZE = 100


class SmartRecruiters(FeedAdapter):
    ats_type = "smartrecruiters"
    module = sys.modules[__name__]

    def feed_url(self, slug, offset=0):
        return (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                f"?limit={PAGE_SIZE}&offset={offset}")

    def items(self, payload):
        return payload.get("content") or []

    def fetch(self, slug, outcome=None):
        page = super().fetch(slug, outcome=outcome)
        postings = list(page)
        max_pages = getattr(config, "ATS_SMARTRECRUITERS_MAX_PAGES", 10)
        offset = PAGE_SIZE
        while len(page) == PAGE_SIZE and offset < max_pages * PAGE_SIZE:
            try:
                response = self.get(self.feed_url(slug, offset))
                page = self.items(response.json()) if response.status_code == 200 else []
            except Exception as e:
                logging.warning(f"smartrecruiters:{slug} page at offset {offset} failed: {e}")
                page = []
            postings.extend(page)
            offset += PAGE_SIZE
        return postings

    def title_of(self, raw):
        return raw.get("name")

    def company_name_of(self, raw):
        return (raw.get("company") or {}).get("name")

    def location_of(self, raw):
        location = raw.get("location") or {}
        place = location.get("fullLocation") or ", ".join(
            p for p in (location.get("city"), (location.get("country") or "").upper()) if p)
        return join_places(place, remote=bool(location.get("remote")))

    def enrich(self, raw, slug, outcome=None):
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{raw.get('id')}"
        try:
            response = self.get(url)
            if response.status_code != 200:
                return None
            return response.json()
        except (self.module.requests.RequestException, ValueError) as e:
            logging.warning(f"smartrecruiters:{slug} detail {raw.get('id')} failed: {e}")
            return None

    def normalize(self, raw, slug, company):
        sections = (raw.get("jobAd") or {}).get("sections") or {}
        body = "".join(f"<h3>{s.get('title') or ''}</h3>{s.get('text') or ''}"
                       for s in sections.values() if isinstance(s, dict) and s.get("text"))
        return to_record(self.ats_type, slug, raw.get("id"), self.title_of(raw), company,
                         self.location_of(raw), body or None, raw.get("releasedDate"),
                         raw.get("postingUrl") or raw.get("applyUrl"), raw.get("applyUrl"))


ADAPTER = SmartRecruiters()
