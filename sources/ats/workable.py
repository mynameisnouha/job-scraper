"""Workable widget API: apply.workable.com/api/v1/widget/accounts/{slug}?details=true

An unknown slug answers 200 with an empty job list rather than 404, so "this company
has a feed" must mean at least one posting (see maintenance/detect_ats.py)."""
import sys

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

from sources.ats.base import FeedAdapter, join_places, to_record


class Workable(FeedAdapter):
    ats_type = "workable"
    module = sys.modules[__name__]

    def feed_url(self, slug):
        return f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"

    def items(self, payload):
        return payload.get("jobs") or []

    def id_of(self, raw):
        return raw.get("shortcode")

    def title_of(self, raw):
        return raw.get("title")

    def location_of(self, raw):
        places = [", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
                  for loc in raw.get("locations") or [] if not loc.get("hidden")]
        if not any(places):
            places = [", ".join(p for p in (raw.get("city"), raw.get("country")) if p)]
        return join_places(*places, remote=bool(raw.get("telecommuting")))

    def normalize(self, raw, slug, company):
        return to_record(self.ats_type, slug, self.id_of(raw), self.title_of(raw), company,
                         self.location_of(raw), raw.get("description"),
                         raw.get("published_on") or raw.get("created_at"),
                         raw.get("url") or raw.get("shortlink"), raw.get("application_url"))


ADAPTER = Workable()
