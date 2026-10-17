"""Greenhouse job board API: boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"""
import sys

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

from sources.ats.base import FeedAdapter, to_record


class Greenhouse(FeedAdapter):
    ats_type = "greenhouse"
    module = sys.modules[__name__]

    def feed_url(self, slug):
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def items(self, payload):
        return payload.get("jobs") or []

    def title_of(self, raw):
        return raw.get("title")

    def company_name_of(self, raw):
        return raw.get("company_name")

    def location_of(self, raw):
        return (raw.get("location") or {}).get("name")

    def normalize(self, raw, slug, company):
        return to_record(self.ats_type, slug, raw.get("id"), self.title_of(raw), company,
                         self.location_of(raw), raw.get("content"),
                         raw.get("first_published") or raw.get("updated_at"),
                         raw.get("absolute_url"))


ADAPTER = Greenhouse()
