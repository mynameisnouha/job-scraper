"""Recruitee careers API: {slug}.recruitee.com/api/offers/"""
import sys

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

from sources.ats.base import FeedAdapter, join_places, to_record


class Recruitee(FeedAdapter):
    ats_type = "recruitee"
    module = sys.modules[__name__]

    def feed_url(self, slug):
        return f"https://{slug}.recruitee.com/api/offers/"

    def items(self, payload):
        return payload.get("offers") or []

    def title_of(self, raw):
        return raw.get("title")

    def company_name_of(self, raw):
        return raw.get("company_name")

    def location_of(self, raw):
        place = raw.get("location") or ", ".join(
            p for p in (raw.get("city"), raw.get("country")) if p)
        return join_places(place, remote=bool(raw.get("remote")))

    def normalize(self, raw, slug, company):
        body = (raw.get("description") or "") + (raw.get("requirements") or "")
        return to_record(self.ats_type, slug, raw.get("id"), self.title_of(raw), company,
                         self.location_of(raw), body, raw.get("published_at"),
                         raw.get("careers_url"), raw.get("careers_apply_url"))


ADAPTER = Recruitee()
