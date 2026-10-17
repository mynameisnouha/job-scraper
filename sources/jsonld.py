"""The schema.org JobPosting block that job boards embed for search engines.

Xing and StepStone both put the ad's title, text, date, employer and address in
one `<script type="application/ld+json">` block. Reading it is steadier than the
visible markup, which is restyled far more often than the SEO block.
"""

import json
import re
from typing import Any, Dict, List, Optional

_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def job_posting(page: str) -> Optional[Dict[str, Any]]:
    for block in _LD_JSON.findall(page or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def addresses(posting: Dict[str, Any]) -> List[Dict[str, Any]]:
    """jobLocation is a Place on some boards and a list of Places on others."""
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    return [p.get("address") or {} for p in places if isinstance(p, dict)]


def locality(posting: Dict[str, Any]) -> Optional[str]:
    names = [a.get("addressLocality") for a in addresses(posting) if a.get("addressLocality")]
    return ", ".join(dict.fromkeys(names)) or None


def country(posting: Dict[str, Any]) -> Optional[str]:
    return next((a.get("addressCountry") for a in addresses(posting) if a.get("addressCountry")), None)
