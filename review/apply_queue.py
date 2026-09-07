"""
The rank-ordered queue behind the Jobs to Apply page.

At 50-100 applications a week the queue is worked by hand, one job at a time,
in a fixed order. Everything that decides *which* job is in front of you — the
ordering, where the cursor sits after an action, what a skip means — lives here
rather than in the Streamlit layer, so it can be tested without a browser.

Streamlit-free on purpose, like calibration.py and job_view.py.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Why a job left the queue without an application. These are labels, not
# bookkeeping: "I skipped every job needing C1 German" is a finding, and it only
# exists if the reason was recorded at the moment of the decision.
SKIP_REASONS = [
    "not_interested",
    "wrong_seniority",
    "location",
    "german_level",
    "visa_sponsorship",
    "salary_too_low",
    "already_applied_elsewhere",
    "duplicate_posting",
    "other",
]

SKIP_REASON_LABELS = {
    "not_interested": "Not interested in the role/company",
    "wrong_seniority": "Wrong seniority",
    "location": "Location / on-site requirement",
    "german_level": "German level too high",
    "visa_sponsorship": "No visa sponsorship",
    "salary_too_low": "Salary too low",
    "already_applied_elsewhere": "Already applied elsewhere",
    "duplicate_posting": "Duplicate posting",
    "other": "Other",
}

# Keyboard shortcuts for the focused job. The label is also the button label —
# the UI matches on it to bind the key, so the two can never drift apart.
SHORTCUTS = [
    ("a", "Mark applied"),
    ("s", "Skip"),
    ("o", "Open posting"),
    ("p", "Pack"),
    ("j", "Next"),
    ("k", "Previous"),
]

SORT_MODES = ["score", "effort"]

# How far back the queue reaches, newest-first. A posting's value decays fast —
# the first applicants are read first — so "when was this found" is a filter, not
# a decoration. None means no lower bound.
DATE_WINDOWS: List[Tuple[str, Optional[timedelta]]] = [
    ("24h", timedelta(hours=24)),
    ("3d", timedelta(days=3)),
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
    ("all", None),
]

DATE_WINDOW_LABELS = {
    "24h": "Last 24 hours",
    "3d": "Last 3 days",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "all": "Any time",
}

DATE_WINDOW_KEYS = [key for key, _ in DATE_WINDOWS]


def scraped_at(job: Dict[str, Any]) -> Optional[datetime]:
    """The moment this posting was found, as an aware datetime, or None."""
    raw = job.get("scraped_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def job_age(job: Dict[str, Any], now: Optional[datetime] = None) -> Optional[timedelta]:
    """How long ago the posting was found. None when there is no usable stamp."""
    found = scraped_at(job)
    if found is None:
        return None
    now = now or datetime.now(timezone.utc)
    return now - found


def within_window(job: Dict[str, Any], window: str,
                  now: Optional[datetime] = None) -> bool:
    """
    Is this job inside the chosen date window?

    A job with no timestamp is kept only by "Any time" — dropping it silently
    from every other window would hide real postings, but so would pretending an
    unknown date is a fresh one.
    """
    span = dict(DATE_WINDOWS).get(window)
    if span is None:
        return True
    age = job_age(job, now)
    if age is None:
        return False
    # A negative age is clock skew between the scraper and here — that job is
    # brand new, not out of range.
    return age <= span


def format_found(job: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    """
    When the posting was found, phrased for how it will be used.

    Under a day old the clock time is what matters — "found 09:12, three hours
    ago" tells you whether you are early to it — so the hour is shown alongside
    the elapsed time. Past a day the hour is noise and only the date is kept.
    """
    found = scraped_at(job)
    if found is None:
        return None
    age = job_age(job, now)
    local = found.astimezone()
    if age is None or age >= timedelta(hours=24) or age < timedelta(0):
        return local.strftime("%Y-%m-%d")
    minutes = int(age.total_seconds() // 60)
    if minutes < 1:
        elapsed = "just now"
    elif minutes < 60:
        elapsed = f"{minutes}m ago"
    else:
        elapsed = f"{minutes // 60}h ago"
    return f"{local.strftime('%H:%M')} · {elapsed}"


def effort_hours(job: Dict[str, Any]) -> Optional[float]:
    """Estimated hours to complete this application, if the scorer gave one."""
    breakdown = job.get("score_breakdown") or {}
    value = breakdown.get("application_effort_hours")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sort_jobs(jobs: List[Dict[str, Any]], mode: str = "score") -> List[Dict[str, Any]]:
    """
    Fixed rank order for the queue.

    "score" is the default: highest first. "effort" sorts by the estimated hours
    ascending so a short evening can be spent on the applications that actually
    fit in it — but score still breaks ties, and jobs with no estimate sort last
    rather than pretending to be free.
    """
    if mode == "effort":
        def key(job):
            hours = effort_hours(job)
            return (hours is None, hours if hours is not None else 0.0,
                    -(job.get("resume_score") or 0))
        return sorted(jobs, key=key)
    return sorted(jobs, key=lambda j: j.get("resume_score") or 0, reverse=True)


def clamp_cursor(index: int, length: int) -> int:
    """Keep the cursor inside the queue. An empty queue parks it at 0."""
    if length <= 0:
        return 0
    if index < 0:
        return 0
    if index >= length:
        return length - 1
    return index


def index_of(jobs: List[Dict[str, Any]], job_id: Optional[str]) -> Optional[int]:
    """Where a given job sits in the current order, or None if it's gone."""
    if not job_id:
        return None
    for i, job in enumerate(jobs):
        if job.get("job_id") == job_id:
            return i
    return None


def resume_cursor(jobs: List[Dict[str, Any]], last_job_id: Optional[str],
                  fallback: int = 0) -> int:
    """
    Where to pick up again.

    The queue refreshes underneath you — four scrape runs a day, and every
    apply/skip removes a row — so a bare index would silently point at a
    different job than the one you were last on. Anchor on the job id when it is
    still there, and only then fall back to the remembered position.
    """
    found = index_of(jobs, last_job_id)
    if found is not None:
        return found
    return clamp_cursor(fallback, len(jobs))
