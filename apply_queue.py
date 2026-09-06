"""
The rank-ordered queue behind the Jobs to Apply page.

At 50-100 applications a week the queue is worked by hand, one job at a time,
in a fixed order. Everything that decides *which* job is in front of you — the
ordering, where the cursor sits after an action, what a skip means — lives here
rather than in the Streamlit layer, so it can be tested without a browser.

Streamlit-free on purpose, like calibration.py and job_view.py.
"""
from typing import Any, Dict, List, Optional

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
