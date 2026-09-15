"""The hiring manager for this posting, reading the application cold.

The judge emits **objections, not a score**, and that is the central design
decision in this package.

A score invites optimisation, and optimising a document against a language model
until the model likes it produces a document that language models like. Since
the queue's own scorer is also an LLM, closing that loop would raise the reported
score without touching the thing the score is a proxy for. Objections behave
differently: each one is specific and checkable, it can be answered or honestly
declined, and the list runs out. That gives the loop a natural end instead of an
asymptote to chase.

The judge also flags lines that read as machine-written - but only by quoting
them. It is not asked "is this AI-generated?", because models are unreliable at
that in both directions and a writer told to sound human adds performative
roughness rather than removing the tells. Quoted lines are actionable; a verdict
is not.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from tailor import settings
from tailor.documents import Application, render_cover_letter, render_cv

SYSTEM_PROMPT = """You are the hiring manager for this specific role - not a \
recruiter, not a CV coach. You have a stack of applications and limited patience. \
You are reading this one for the first time.

Report what is wrong with it, as objections. Do not give it a score, a rating, or \
a percentage.

What counts as a good objection:
 - it names a specific requirement of YOUR posting
 - it points at what the application does or does not say about it
 - it says what would fix it, or states plainly that nothing could

What does not:
 - general CV advice that would apply to any application
 - style preferences that do not change your decision
 - asking for something the candidate evidently does not have, twice

On the last point: if a candidate lacks a qualification, raise it ONCE as \
structural and move on. Repeating it across rounds is noise - the application \
cannot fix it and neither can they.

Be hard on:
 - claims with no evidence attached
 - vague ownership - "worked on", "involved in", "supported"
 - anything that reads as written by a language model. Quote the exact lines. \
Look for uniform sentence length, stacked adjectives, enthusiasm with no \
specifics, and the word "leverage".
 - a cover letter that restates the CV instead of arguing

Also say plainly, in `standout`, what is genuinely strong here. You are trying to \
make a decision, not to find fault.

Report `would_interview` honestly. It is recorded for the candidate's \
information; it is not a target and nobody is optimising it.

## Dates

Refer to any date by its calendar date. NEVER state how many months or years \
away it is, and never compute an elapsed duration yourself - not in an \
objection, not in passing. Where a gap matters, it is given to you above; quote \
that figure or say nothing about length.

This is not a style rule. Your sense of the current date is wrong and you will \
not notice: told plainly that today is in September 2026, a start date five \
months out was still reported as "about 18 months away", twice. The calendar \
above is authoritative and your own is not."""


class Objection(BaseModel):
    requirement: str = Field(..., description="The posting requirement at stake.")
    objection: str = Field(..., description="What is wrong, in one or two sentences.")
    severity: str = Field(..., description="'blocking', 'major' or 'minor'.")
    what_would_fix_it: str = Field(
        ...,
        description=(
            "The concrete change that would answer this, or 'nothing - the candidate "
            "does not have this' when the gap is real."
        ),
    )
    structural: bool = Field(
        False,
        description=(
            "True when no rewrite can fix it because the candidate lacks the "
            "qualification. Structural objections are reported once and not repeated."
        ),
    )


_PARAM_TAG = re.compile(r"^\s*<parameter\s+name=\"[^\"]*\">\s*", re.IGNORECASE)
_PARAM_CLOSE = re.compile(r"\s*</parameter>\s*$", re.IGNORECASE)


def _salvage_list(value: Any) -> Any:
    """Recover a list field the model handed back as a string.

    Opus intermittently emits its tool arguments wrapped in the markup it would
    use to *describe* a call - `<parameter name="objections">[...]</parameter>` -
    so the field arrives as a string and validation fails. Retrying does not
    reliably help: two attempts in a row failed the same way, which cost a whole
    run its review and reported an unexamined draft as one with no objections.

    Unwrapping is preferable to failing here. It is a narrow, shape-specific
    repair - if the salvaged text is not valid JSON it is handed back untouched
    and validation rejects it as before, so nothing is silently accepted.
    """
    if not isinstance(value, str):
        return value
    text = _PARAM_CLOSE.sub("", _PARAM_TAG.sub("", value)).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        return value
    return parsed


class Verdict(BaseModel):
    objections: List[Objection] = Field(default_factory=list)
    ai_tells: List[str] = Field(
        default_factory=list,
        description="Exact lines that read as machine-written. Quote them verbatim.",
    )
    standout: str = Field("", description="The strongest thing about this application.")
    would_interview: str = Field(
        "no", description="'yes', 'maybe' or 'no' - recorded, never optimised against."
    )

    @model_validator(mode="before")
    @classmethod
    def _repair(cls, data: Any) -> Any:
        """Put the model's output back where it belongs before validating it.

        Three malformations have shown up in practice, all from the same cause -
        the model filling the schema loosely - and all previously fatal, which
        cost the run its whole review:

        1. a list field arrives as a string wrapped in `<parameter name=...>`;
        2. `ai_tells` arrives holding objection *objects* rather than quoted
           lines. Seen on a German-language posting where the model wrote its
           objections in German and put a batch of them in the wrong field;
        3. an objection moved out of (2) is missing `severity` or
           `what_would_fix_it`, which are required.

        Misfiled objections are moved rather than dropped: they are real
        criticism the model produced, and discarding them would quietly weaken
        the review instead of failing loudly.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)

        objections = _salvage_list(data.get("objections") or [])
        objections = list(objections) if isinstance(objections, list) else []

        tells = _salvage_list(data.get("ai_tells") or [])
        quoted: List[Any] = []
        if isinstance(tells, list):
            for item in tells:
                if isinstance(item, dict):
                    if "objection" in item or "requirement" in item:
                        objections.append(item)
                    else:
                        # Some other shape entirely; keep whatever text it has
                        # rather than failing on it.
                        quoted.append(str(item.get("text") or item.get("line") or item))
                else:
                    quoted.append(item)
        else:
            quoted = tells

        for objection in objections:
            if isinstance(objection, dict):
                objection.setdefault("requirement", "unspecified")
                objection.setdefault("objection", "")
                objection.setdefault("severity", "minor")
                objection.setdefault("what_would_fix_it", "")

        data["objections"] = objections
        data["ai_tells"] = quoted
        return data


def _availability_note() -> str:
    """Shared with the scorer, so both read the calendar the same way."""
    try:
        from scoring.score_jobs import availability_note

        return availability_note()
    except Exception:  # noqa: BLE001 - a missing profile must not stop a review
        return datetime.now(timezone.utc).date().isoformat()


def review(job: Dict[str, Any], app: Application, attempts: int = 2) -> Optional[Verdict]:
    """Review one application. Retries, because a silent miss is expensive here.

    A failed judge call does not merely lose one review - the loop treats it as
    a reason to stop, so the run ends after a single unreviewed draft while still
    looking like a finished result. That happened once in testing: a transient
    failure produced a CV with zero objections, an unchanged score, and nothing
    on screen to say the hiring manager had never read it. One retry costs a call;
    the silent version costs a wasted application.

    The posting goes in the cached system prompt rather than the user message.
    It is identical across every round of a run, and it is the largest stable
    part of the call. It sits in its own cache block behind the rubric, so the
    rubric is read from cache across jobs and the posting across rounds.
    """
    from scoring.llm_client import primary_client

    system = [
        # The calendar leads, before the role instructions. Appended at the end
        # it was read, acknowledged and then overruled - the model wrote "10.09.2026
        # waere Heute" and went on to use its own sense of the present anyway.
        # The gap is supplied already computed so there is no sum to get wrong.
        "## TODAY\n" + _availability_note() + "\n\n" + SYSTEM_PROMPT,
        f"## YOUR POSTING\n\nTitle: {job.get('job_title')}\n"
        f"Company: {job.get('company')}\n\n{(job.get('description') or '')[:9000]}",
    ]
    prompt = (
        f"## THE APPLICATION\n\n### CV\n\n{render_cv(app.cv)}\n\n"
        f"### Cover letter\n\n{render_cover_letter(app.cover_letter)}\n\n"
        "What are your objections?"
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            raw = primary_client.generate_content(
                prompt=prompt, system_prompt=system, cache_system=True,
                response_format=Verdict,
                temperature=settings.temperature_for(settings.JUDGE_MODEL, 0.2),
                model_override=settings.JUDGE_MODEL,
            )
            verdict = Verdict.model_validate_json(raw)
            if is_vacuous(verdict):
                # Not an error, which is exactly the problem: every field falls to
                # its schema default, the object validates, and the loop reads it
                # as "the hiring manager had nothing to say". Observed in the wild
                # on a run that burned 2,800 output tokens and returned nothing.
                logging.warning(
                    "Judge returned an empty verdict on attempt %d/%d; retrying.",
                    attempt, attempts,
                )
                continue
            return verdict
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logging.warning("Judge review attempt %d/%d failed: %s", attempt, attempts, exc)

    logging.error("Judge review failed after %d attempts: %s", attempts, last_error)
    return None


def is_vacuous(verdict: Verdict) -> bool:
    """A verdict that parsed but says nothing.

    A hiring manager reading a real application produces *something* - an
    objection, a quoted line, or at minimum a note on what stood out. All three
    empty means the model returned an all-defaults object rather than a review,
    and treating that as "no objections" would report an unexamined draft as one
    that survived scrutiny.
    """
    return not verdict.objections and not verdict.ai_tells and not verdict.standout.strip()


def objection_key(objection: Objection) -> str:
    """A loose identity for an objection, so repeats across rounds can be spotted.

    Deliberately coarse - requirement plus the first few words. A judge rarely
    phrases the same complaint identically twice, and the loop needs to know
    whether a round produced anything *new*, not whether two strings match.
    """
    head = " ".join(objection.objection.lower().split()[:6])
    return f"{objection.requirement.strip().lower()}|{head}"


def actionable(verdict: Verdict) -> List[Objection]:
    """Objections a rewrite could actually answer.

    Structural ones are dropped: they are real, they are reported to the
    candidate, and asking the writer to fix a missing qualification only invites
    it to blur the wording until the complaint goes away.
    """
    return [o for o in verdict.objections if not o.structural]
