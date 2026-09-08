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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

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
information; it is not a target and nobody is optimising it."""


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


def review(job: Dict[str, Any], app: Application) -> Optional[Verdict]:
    from scoring.llm_client import primary_client

    prompt = (
        f"## YOUR POSTING\n\nTitle: {job.get('job_title')}\n"
        f"Company: {job.get('company')}\n\n{(job.get('description') or '')[:9000]}\n\n"
        f"## THE APPLICATION\n\n### CV\n\n{render_cv(app.cv)}\n\n"
        f"### Cover letter\n\n{render_cover_letter(app.cover_letter)}\n\n"
        "What are your objections?"
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt, system_prompt=SYSTEM_PROMPT,
            response_format=Verdict,
            temperature=settings.temperature_for(settings.JUDGE_MODEL, 0.2),
            model_override=settings.JUDGE_MODEL,
        )
        return Verdict.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Judge review failed: %s", exc)
        return None


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
