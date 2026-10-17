"""Lever postings API: api.lever.co/v0/postings/{slug}?mode=json"""
import sys
from datetime import datetime, timezone

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

from sources.ats.base import FeedAdapter, join_places, to_record


class Lever(FeedAdapter):
    ats_type = "lever"
    module = sys.modules[__name__]

    def feed_url(self, slug):
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"

    def items(self, payload):
        return payload if isinstance(payload, list) else []

    def title_of(self, raw):
        return raw.get("text")

    def location_of(self, raw):
        categories = raw.get("categories") or {}
        places = categories.get("allLocations") or [categories.get("location")]
        return join_places(*places, remote=raw.get("workplaceType") == "remote")

    def normalize(self, raw, slug, company):
        created = raw.get("createdAt")
        posted = (datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
                  if isinstance(created, (int, float)) else None)
        body = "".join(raw.get(key) or "" for key in ("opening", "description", "descriptionBody"))
        body += "".join(f"<h3>{item.get('text') or ''}</h3>{item.get('content') or ''}"
                        for item in raw.get("lists") or [])
        body += raw.get("additional") or ""
        return to_record(self.ats_type, slug, raw.get("id"), self.title_of(raw), company,
                         self.location_of(raw), body, posted,
                         raw.get("hostedUrl"), raw.get("applyUrl"))


ADAPTER = Lever()
