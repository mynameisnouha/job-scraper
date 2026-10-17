"""Which applicant tracking system (ATS) a URL belongs to.

One table, used everywhere a URL is classified: the career-page feeds, the
scrapers that find an employer link, and the backfill. Knowing the ATS is what
lets the queue group applications by form type, and what decides which playbook
an apply worker loads later.

Host patterns started from the ai-application project's batch-apply.sh and were
extended with the ATSs the feeds cover.
"""

from typing import Optional
from urllib.parse import parse_qs, urlsplit

ATS_TYPES = (
    "workday", "greenhouse", "ashby", "lever", "successfactors", "icims",
    "teamtailor", "join", "personio", "smartrecruiters", "recruitee", "workable",
    "other",
)

# Ordered (host fragment, ats_type). A fragment matches when the hostname ends
# with it or contains it as a dotted label, so "acme.jobs.personio.de" and
# "career5.successfactors.eu" both match without a regex per ATS.
_HOST_PATTERNS = (
    ("myworkdayjobs.com", "workday"),
    ("myworkdaysite.com", "workday"),
    ("greenhouse.io", "greenhouse"),
    ("ashbyhq.com", "ashby"),
    ("lever.co", "lever"),
    ("successfactors.", "successfactors"),
    ("icims.com", "icims"),
    ("teamtailor.com", "teamtailor"),
    ("join.com", "join"),
    ("personio.de", "personio"),
    ("personio.com", "personio"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("recruitee.com", "recruitee"),
    ("workable.com", "workable"),
)

# Where an ad is published, not where anyone applies.
_JOB_BOARD_HOSTS = (
    "linkedin.com", "stepstone.de", "xing.com", "indeed.com", "indeed.de",
    "arbeitsagentur.de",
)


def _hostname(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower()


def _matches(host: str, fragment: str) -> bool:
    if fragment.endswith("."):
        return host.startswith(fragment) or f".{fragment}" in f".{host}"
    return host == fragment or host.endswith(f".{fragment}")


def is_job_board(url: Optional[str]) -> bool:
    host = _hostname(url)
    return bool(host) and any(_matches(host, board) for board in _JOB_BOARD_HOSTS)


def detect(url: Optional[str]) -> Optional[str]:
    """The ATS type for `url`, "other" for an employer's own site, None for a job
    board or anything that is not an http(s) URL."""
    host = _hostname(url)
    if not host or is_job_board(url):
        return None
    for fragment, ats_type in _HOST_PATTERNS:
        if _matches(host, fragment):
            return ats_type
    return "other"


# Hosts whose first path segment is the company's feed name.
_PATH_SLUG_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "careers.smartrecruiters.com": "smartrecruiters",
    "apply.workable.com": "workable",
}
# Hosts where the feed name is the subdomain: {slug}.<suffix>.
_SUBDOMAIN_SLUG_SUFFIXES = {
    ".recruitee.com": "recruitee",
    ".jobs.personio.de": "personio",
    ".jobs.personio.com": "personio",
}
_NOT_A_SLUG = {"www", "app", "api", "careers", "jobs", "embed", "j", ""}


def slug_from_url(url: Optional[str]) -> Optional[tuple]:
    """(ats_type, feed name) when an apply link names the company's feed exactly,
    e.g. jobs.lever.co/<name>/... or <name>.jobs.personio.de. Only for the ATSs
    there is a feed adapter for. None otherwise."""
    host = _hostname(url)
    if not host:
        return None
    parts = urlsplit(url.strip())
    segments = [s for s in parts.path.split("/") if s]
    if host in _PATH_SLUG_HOSTS:
        ats_type = _PATH_SLUG_HOSTS[host]
        if ats_type == "greenhouse" and segments[:1] == ["embed"]:
            found = parse_qs(parts.query).get("for") or [""]
            slug = found[0]
        else:
            slug = segments[0] if segments else ""
        return (ats_type, slug) if slug.lower() not in _NOT_A_SLUG else None
    for suffix, ats_type in _SUBDOMAIN_SLUG_SUFFIXES.items():
        if host.endswith(suffix):
            slug = host[:-len(suffix)]
            return (ats_type, slug) if slug and "." not in slug and slug not in _NOT_A_SLUG else None
    return None
