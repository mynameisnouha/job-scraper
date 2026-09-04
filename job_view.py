"""
Turns a job's score_breakdown into the few lines actually worth reading before
deciding whether to apply.

The breakdown holds 34 fields; showing them all is the same as showing none.
These functions pick the shortest set that answers "should I apply, what do I
lead with, and what will they push back on" — and drop everything else.

Streamlit-free so the selection logic can be tested directly.
"""
from typing import Any, Dict, List, Optional, Tuple

MAX_ITEMS = 4
# The scorer writes full sentences; a scannable card needs a phrase.
MAX_ITEM_CHARS = 100


def shorten(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    """Trim to a readable phrase, cutting at a word boundary."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return cut + "…"


def _clean(values) -> List[str]:
    """Flatten to non-empty strings, de-duplicated, order preserved."""
    out, seen = [], set()
    for v in values or []:
        if isinstance(v, dict):
            v = v.get("gap") or v.get("name") or ""
        text = str(v).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(shorten(text))
    return out


def pros(breakdown: Dict[str, Any]) -> List[str]:
    """
    What to lead with. Differentiators first — those are what the median
    applicant lacks, so they earn the interview; matching skills only fill space
    after that.
    """
    b = breakdown or {}
    return _clean(list(b.get("differentiators") or []) +
                  list(b.get("key_matching_skills") or []))[:MAX_ITEMS]


def cons(breakdown: Dict[str, Any]) -> List[str]:
    """
    What they'll push back on. Ordered by how hard it is to argue away:
    explicit disqualifiers, then gaps that can't be closed before the deadline,
    then ordinary missing skills.
    """
    b = breakdown or {}
    return _clean(list(b.get("disqualifier_matches") or []) +
                  list(b.get("structural_gaps") or []) +
                  list(b.get("key_gaps") or []))[:MAX_ITEMS]


def quick_wins(breakdown: Dict[str, Any]) -> List[str]:
    """
    Gaps that are only gaps because the CV doesn't show them — fixable in an
    evening. Rendered as "gap → fix" so it reads as a task, not a complaint.
    """
    out = []
    for item in (breakdown or {}).get("fixable_before_applying") or []:
        if not isinstance(item, dict):
            text = str(item).strip()
            if text:
                out.append(text)
            continue
        gap = str(item.get("gap") or "").strip()
        fix = str(item.get("fix") or "").strip()
        if gap and fix:
            out.append(f"{shorten(gap, 60)} → {shorten(fix, 90)}")
        elif gap or fix:
            out.append(shorten(gap or fix))
    return out[:MAX_ITEMS]


def summary(breakdown: Dict[str, Any]) -> str:
    """The one-sentence bottom line, falling back to the longer reasoning."""
    b = breakdown or {}
    verdict = str(b.get("one_line_verdict") or "").strip()
    if verdict:
        return verdict
    reasoning = str(b.get("reasoning") or "").strip()
    if len(reasoning) > 240:
        return reasoning[:237].rstrip() + "…"
    return reasoning


def interview_odds(breakdown: Dict[str, Any]) -> Optional[float]:
    """Best available estimate of landing a first-round interview, 0-1."""
    context = (breakdown or {}).get("competitive_context") or {}
    if not isinstance(context, dict):
        return None
    raw = context.get("p_first_round_interview")
    if not isinstance(raw, dict):
        raw = {"after_fixes": raw}
    for key in ("after_fixes", "as_is"):
        value = raw.get(key)
        try:
            p = float(value)
        except (TypeError, ValueError):
            continue
        if p > 1:
            p = p / 100.0
        return min(max(p, 0.0), 1.0)
    return None


def quick_facts(breakdown: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    The handful of constraints that decide whether applying is worth the hour.
    Only facts the scorer actually established are returned — a row of "unclear"
    is noise.
    """
    b = breakdown or {}
    facts: List[Tuple[str, str]] = []

    odds = interview_odds(b)
    if odds is not None:
        facts.append(("Interview odds", f"{odds * 100:.0f}%"))

    effort = b.get("application_effort_hours")
    if effort:
        try:
            facts.append(("Effort", f"{float(effort):.1f}h"))
        except (TypeError, ValueError):
            pass

    german = str(b.get("german_required") or "").strip()
    if german and german not in ("unclear", "none"):
        facts.append(("German", german))

    # The scorer fills salary_band with prose like "Not stated — cannot assess
    # Blue Card threshold" when there's no figure. A fact with no number in it
    # isn't a fact worth a slot on the card.
    salary = str(b.get("salary_band") or "").strip()
    if salary and any(ch.isdigit() for ch in salary):
        facts.append(("Salary", shorten(salary, 40)))

    if b.get("is_agency_or_staffing_firm"):
        facts.append(("Source", "Recruiting agency"))

    return facts
