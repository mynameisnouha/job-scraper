"""Ashby job board API: api.ashbyhq.com/posting-api/job-board/{slug}"""
import sys

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

from sources.ats.base import FeedAdapter, join_places, to_record


class Ashby(FeedAdapter):
    ats_type = "ashby"
    module = sys.modules[__name__]

    def feed_url(self, slug):
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"

    def items(self, payload):
        return [job for job in payload.get("jobs") or [] if job.get("isListed", True)]

    def title_of(self, raw):
        return raw.get("title")

    def location_of(self, raw):
        secondary = [s.get("location") for s in raw.get("secondaryLocations") or []]
        return join_places(raw.get("location"), *secondary, remote=bool(raw.get("isRemote")))

    def normalize(self, raw, slug, company):
        return to_record(self.ats_type, slug, raw.get("id"), self.title_of(raw), company,
                         self.location_of(raw), raw.get("descriptionHtml"),
                         raw.get("publishedAt"), raw.get("jobUrl"), raw.get("applyUrl"))


ADAPTER = Ashby()
