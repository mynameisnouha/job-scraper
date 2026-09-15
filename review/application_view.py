"""
What one application looks like as a record: where it is in the pipeline, how
long it has been there, and the argument it was sent with.

Streamlit-free, like calibration.py — ui_app.py only renders what comes back.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from review import calibration
from review import job_view

STAGE_LABELS = {
    "applied": "Awaiting reply",
    "interview_1": "Interview 1",
    "interview_2": "Interview 2",
    "interview_3": "Interview 3",
    "offer": "Offer",
    "rejected": "Rejected",
    "ghosted": "Ghosted",
    "spam_or_removed": "Removed / spam",
}

# Which colour family a stage pill takes. "good" is progress, "bad" is a
# closed door, "wait" is nothing yet.
STAGE_TONE = {
    "applied": "wait",
    "interview_1": "good",
    "interview_2": "good",
    "interview_3": "good",
    "offer": "good",
    "rejected": "bad",
    "ghosted": "bad",
    "spam_or_removed": "neutral",
}

# Half the ghost threshold: long enough that silence starts to mean something,
# short enough that a chase still lands before the role is filled.
CHASE_AFTER_DAYS = calibration.GHOSTED_AFTER_DAYS // 2


def stage_of(job: Dict[str, Any]) -> str:
    return (job.get("application_stage") or "applied").strip() or "applied"


def stage_label(job: Dict[str, Any]) -> str:
    stage = stage_of(job)
    label = STAGE_LABELS.get(stage, stage)
    if stage == "rejected" and job.get("rejection_reason"):
        reason = str(job["rejection_reason"]).replace("_", " ")
        label = f"Rejected · {reason}"
    return label


def needs_chasing(job: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Still waiting, and waiting long enough that a nudge is due."""
    if calibration.is_resolved(job) or calibration.is_excluded(job):
        return False
    age = calibration.days_since_applied(job, now)
    return age is not None and age >= CHASE_AFTER_DAYS


def age_label(job: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, str]:
    """
    How long the application has been sitting, phrased for what to do about it.
    `tone` is "warn" when it has gone quiet long enough to chase.
    """
    age = calibration.days_since_applied(job, now)
    if age is None:
        return {"text": "", "tone": "neutral"}
    stage = stage_of(job)
    if stage in ("rejected", "ghosted"):
        return {"text": f"{STAGE_LABELS[stage].lower()} after {age} days", "tone": "neutral"}
    if stage in calibration.INTERVIEW_STAGES:
        return {"text": f"{age} days in", "tone": "neutral"}
    if age >= calibration.GHOSTED_AFTER_DAYS:
        return {"text": f"{age} days · probably ghosted", "tone": "warn"}
    if age >= CHASE_AFTER_DAYS:
        return {"text": f"{age} days · chase it", "tone": "warn"}
    return {"text": f"{age} days · normal", "tone": "neutral"}


def _short_date(value) -> str:
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "—"
    return stamp.strftime("%d %b").lstrip("0")


def timeline(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    The pipeline as a row of dots. Only the stages the record actually
    evidences are marked done — the table stores the current stage, not a
    history, so an Interview 2 implies Interview 1 but a rejection implies
    nothing about how far it got.

    Each node: label, date, state ("done" | "current" | "pending" | "bad").
    """
    stage = stage_of(job)
    applied = _short_date(job.get("application_date"))
    updated = _short_date(job.get("stage_updated_at"))
    nodes = [{"label": "Applied", "date": applied, "state": "done"}]

    if stage in ("rejected", "ghosted"):
        nodes.append({"label": STAGE_LABELS[stage], "date": updated, "state": "bad"})
        return nodes
    if stage == "spam_or_removed":
        nodes.append({"label": "Removed", "date": updated, "state": "bad"})
        return nodes

    reached = {"interview_1": 1, "interview_2": 2, "interview_3": 3, "offer": 3}.get(stage, 0)
    for n in (1, 2, 3):
        if n < reached or (n == reached and stage == "offer"):
            nodes.append({"label": f"Interview {n}", "date": "", "state": "done"})
        elif n == reached:
            nodes.append({"label": f"Interview {n}", "date": updated, "state": "current"})
        elif n == reached + 1:
            nodes.append({"label": f"Interview {n}", "date": "pending", "state": "pending"})
            break
        else:
            break
    if stage == "offer":
        nodes.append({"label": "Offer", "date": updated, "state": "current"})
    else:
        nodes.append({"label": "Outcome", "date": "—", "state": "pending"})
    return nodes


def led_with(job: Dict[str, Any]) -> str:
    """What the application argued from — the pros, as one line."""
    items = job_view.pros(job.get("score_breakdown") or {})[:2]
    return " · ".join(items)


def pushback(job: Dict[str, Any]) -> str:
    items = job_view.cons(job.get("score_breakdown") or {})[:2]
    return " · ".join(items)


def counts(jobs: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, int]:
    """The tab counts: open, needs chasing, resolved."""
    open_ = [j for j in jobs if not calibration.is_resolved(j) and not calibration.is_excluded(j)]
    return {
        "open": len(open_),
        "chase": sum(1 for j in open_ if needs_chasing(j, now)),
        "resolved": sum(1 for j in jobs if calibration.is_resolved(j)),
        "all": len(jobs),
    }


def filter_tab(jobs: List[Dict[str, Any]], tab: str,
               now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    if tab == "open":
        return [j for j in jobs if not calibration.is_resolved(j) and not calibration.is_excluded(j)]
    if tab == "chase":
        return [j for j in jobs if needs_chasing(j, now)]
    if tab == "resolved":
        return [j for j in jobs if calibration.is_resolved(j)]
    return list(jobs)
