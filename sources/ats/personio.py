"""Personio XML feed: {slug}.jobs.personio.de/xml

The one that matters most for the German Mittelstand. Each <position> carries its
ad as <jobDescriptions> name/value pairs, with the HTML entity-escaped. Positions
whose descriptions are empty (common for executive roles) are dropped by
to_record, like any posting the scorer could not read.
"""
import sys
import xml.etree.ElementTree as ET

import requests  # noqa: F401  (reached through ADAPTER.module, which tests patch)

import config
from sources.ats.base import FeedAdapter, join_places, to_record


def _text(element, tag):
    found = element.find(tag)
    return found.text.strip() if found is not None and found.text else None


class Personio(FeedAdapter):
    ats_type = "personio"
    module = sys.modules[__name__]

    def feed_url(self, slug):
        return f"https://{slug}.jobs.personio.de/xml?language=de"

    def get(self, url):
        return self.module.requests.get(url, timeout=config.REQUEST_TIMEOUT)

    def parse(self, response):
        return ET.fromstring(response.content)

    def items(self, root):
        positions = []
        for position in root.findall("position"):
            offices = [_text(position, "office")]
            offices += [o.text.strip() for o in position.findall("additionalOffices/office") if o.text]
            positions.append({
                "id": _text(position, "id"),
                "name": _text(position, "name"),
                "offices": [o for o in offices if o],
                "descriptions": [(_text(jd, "name"), _text(jd, "value"))
                                 for jd in position.findall("jobDescriptions/jobDescription")],
                "created_at": _text(position, "createdAt"),
                "subcompany": _text(position, "subcompany"),
            })
        return positions

    def title_of(self, raw):
        return raw.get("name")

    def company_name_of(self, raw):
        return raw.get("subcompany")

    def location_of(self, raw):
        return join_places(*raw.get("offices") or [])

    def normalize(self, raw, slug, company):
        body = "".join(f"<h3>{name or ''}</h3>{value}"
                       for name, value in raw.get("descriptions") or [] if value)
        url = f"https://{slug}.jobs.personio.de/job/{raw.get('id')}"
        return to_record(self.ats_type, slug, raw.get("id"), self.title_of(raw), company,
                         self.location_of(raw), body or None, raw.get("created_at"), url)


ADAPTER = Personio()
