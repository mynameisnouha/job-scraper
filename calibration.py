"""
Turns logged application outcomes into calibration metrics: is the LLM scorer's
confidence actually predictive of getting an interview?

Deliberately free of Streamlit so the logic is testable on its own. The UI in
ui_app.py renders what these functions return.

Key subtlety: a job still sitting at stage 'applied' is *censored*, not a
negative — you haven't heard back yet. Counting it as "no interview" would
understate the interview rate and poison the Brier score, so unresolved
applications are excluded from every metric here.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Stages that mean the application reached at least a first-round interview.
INTERVIEW_STAGES = {"interview_1", "interview_2", "interview_3", "offer"}
# Stages that mean the outcome is known one way or the other.
RESOLVED_STAGES = INTERVIEW_STAGES | {"rejected", "ghosted"}
# Postings that were pulled or turned out to be spam. These are *invalid*, not
# negative: the scorer never got a real verdict, so they're dropped from every
# metric — including the pending count — rather than counted against it.
EXCLUDED_STAGES = {"spam_or_removed"}

# Below this many resolved outcomes, calibration numbers are noise.
MIN_RESOLVED_FOR_METRICS = 15

# No reply after this long almost certainly means no reply is coming.
GHOSTED_AFTER_DAYS = 30

SCORE_BUCKETS = [
    ("<50", 0, 50),
    ("50-64", 50, 65),
    ("65-74", 65, 75),
    ("75-84", 75, 85),
    ("85+", 85, 101),
]


def is_resolved(job: Dict[str, Any]) -> bool:
    """True if we know how this application ended (not still waiting)."""
    return (job.get("application_stage") or "") in RESOLVED_STAGES


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def days_since_applied(job: Dict[str, Any], now: Optional[datetime] = None) -> Optional[int]:
    """Days since the application was sent, or None if the date is unusable."""
    applied_at = _parse_ts(job.get("application_date"))
    if applied_at is None:
        return None
    if applied_at.tzinfo is None:
        applied_at = applied_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - applied_at).days


def stale_pending(jobs: List[Dict[str, Any]], days: int = GHOSTED_AFTER_DAYS,
                  now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Applications still sitting at 'applied' with no reply for `days`.

    Most employers never send a rejection, so without this the pending pile
    grows forever and the resolved set — the only thing calibration can learn
    from — stays empty. These are *suggestions*, never auto-applied: a real
    reply can still arrive late, and a wrong 'ghosted' is a false negative in
    the training data.
    """
    out = []
    for job in jobs:
        if (job.get("application_stage") or "applied") != "applied":
            continue
        age = days_since_applied(job, now=now)
        if age is not None and age >= days:
            out.append(job)
    return sorted(out, key=lambda j: j.get("application_date") or "")


def is_excluded(job: Dict[str, Any]) -> bool:
    """True if the posting was spam or withdrawn, so it can't score the scorer."""
    return (job.get("application_stage") or "") in EXCLUDED_STAGES


def got_interview(job: Dict[str, Any]) -> bool:
    """True if the application reached at least a first-round interview."""
    return (job.get("application_stage") or "") in INTERVIEW_STAGES


def _normalize_probability(value: Any) -> Optional[float]:
    """
    Coerce a predicted probability to 0-1. The LLM has been seen to emit both
    0.15 and 15 for "15%", so anything above 1 is treated as a percentage.
    """
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if p != p:  # NaN
        return None
    if p > 1:
        p = p / 100.0
    return min(max(p, 0.0), 1.0)


def predicted_interview_probability(job: Dict[str, Any]) -> Optional[float]:
    """
    Pulls the scorer's own predicted first-round-interview probability out of
    score_breakdown.competitive_context, preferring the after_fixes estimate.
    Returns None when the scorer didn't produce one.
    """
    breakdown = job.get("score_breakdown") or {}
    if not isinstance(breakdown, dict):
        return None
    context = breakdown.get("competitive_context") or {}
    if not isinstance(context, dict):
        return None

    raw = context.get("p_first_round_interview")
    if isinstance(raw, dict):
        for key in ("after_fixes", "as_is"):
            p = _normalize_probability(raw.get(key))
            if p is not None:
                return p
        return None
    return _normalize_probability(raw)


def brier_score(pairs: List[tuple]) -> Optional[float]:
    """
    Mean squared error between predicted probability and actual 0/1 outcome.
    Lower is better; 0.25 is what you'd get by always guessing 50%.
    Returns None if there's nothing to score.
    """
    usable = [(p, a) for p, a in pairs if p is not None]
    if not usable:
        return None
    return sum((p - float(a)) ** 2 for p, a in usable) / len(usable)


def bucket_stats(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Interview rate per resume_score bucket, over resolved applications only.
    Buckets with no data are still returned (rate None) so the shape is stable.
    """
    resolved = [j for j in jobs if is_resolved(j)]
    rows = []
    for label, low, high in SCORE_BUCKETS:
        in_bucket = [j for j in resolved
                     if low <= (j.get("resume_score") or 0) < high]
        n = len(in_bucket)
        interviews = sum(1 for j in in_bucket if got_interview(j))
        rows.append({
            "bucket": label,
            "n": n,
            "interviews": interviews,
            "interview_rate": (interviews / n) if n else None,
        })
    return rows


def rejection_reason_counts(jobs: List[Dict[str, Any]]) -> Dict[str, int]:
    """How often each rejection reason came up, most common first."""
    counts: Dict[str, int] = {}
    for job in jobs:
        if (job.get("application_stage") or "") != "rejected":
            continue
        reason = (job.get("rejection_reason") or "").strip() or "unspecified"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def summarize(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Headline calibration figures. `enough_data` tells the UI whether to show
    the metrics or a "keep logging outcomes" message.
    """
    resolved = [j for j in jobs if is_resolved(j)]
    excluded = [j for j in jobs if is_excluded(j)]
    pending = [j for j in jobs if not is_resolved(j) and not is_excluded(j)]
    interviews = [j for j in resolved if got_interview(j)]
    offers = [j for j in resolved if (j.get("application_stage") or "") == "offer"]
    rejected = [j for j in resolved if (j.get("application_stage") or "") == "rejected"]
    ghosted = [j for j in resolved if (j.get("application_stage") or "") == "ghosted"]

    pairs = [(predicted_interview_probability(j), got_interview(j)) for j in resolved]
    predicted = [p for p, _ in pairs if p is not None]

    return {
        "total_applied": len(jobs),
        "pending": len(pending),
        "excluded": len(excluded),
        "resolved": len(resolved),
        "interviews": len(interviews),
        "offers": len(offers),
        "rejected": len(rejected),
        "ghosted": len(ghosted),
        "interview_rate": (len(interviews) / len(resolved)) if resolved else None,
        "brier": brier_score(pairs),
        "mean_predicted": (sum(predicted) / len(predicted)) if predicted else None,
        "with_prediction": len(predicted),
        "enough_data": len(resolved) >= MIN_RESOLVED_FOR_METRICS,
        "min_required": MIN_RESOLVED_FOR_METRICS,
    }
