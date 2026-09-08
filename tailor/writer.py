"""Composing the CV and cover letter from the fact base.

The writer selects, orders and phrases. It does not author claims - every line
it produces carries the fact ids it rests on, and verify.py rejects the document
if any of them is missing or invented.

That constraint does double duty. It is the honesty guarantee, and it is also
most of what keeps the output from reading as machine-written: a document
assembled from specific facts, reusing the candidate's own phrasing, has nowhere
to put "results-driven professional with a proven track record".
"""

import json
import logging
from typing import Any, Dict, List, Optional

from tailor import settings
from tailor.documents import Application
from tailor.facts import FactBase

SYSTEM_PROMPT = """You write a CV and a German-market cover letter (Anschreiben) \
for one specific job, using ONLY a supplied fact base.

## The hard rule

Every claim-bearing line you write carries the ids of the facts it rests on. You \
may select facts, order them, and phrase them. You may NOT state anything that \
is not in the fact base. If the posting wants something the base does not \
contain, leave it out - do not hint, imply, or write around it. An unsupported \
line is rejected mechanically, so inventing costs you a round and gains nothing.

Numbers are checked against the cited facts character by character. Copy them \
exactly or omit them.

The one exception: lines that state availability, notice period, location or \
contact details are not claims about competence and come from the personal \
details block rather than the fact base. Cite those as `personal`.

## Always include

Fill `education` and `languages` from the personal details block, every time, \
whatever the posting asks for. A CV missing its degree or its language levels \
reads as incomplete and gets marked down for a qualification the candidate \
actually holds - which is the exact failure this whole process exists to \
prevent. The same goes for `skills`: include everything the fact base supports \
that is plausibly relevant, ordered by relevance to this posting. Ordering is \
your lever, not omission.

## Ordering is where you actually win

The top third of the first page decides whether the rest is read. Put the facts \
that answer this posting's stated must-haves there, in the posting's own \
vocabulary where the fact genuinely supports it - if they say "LLM inference \
optimisation" and the fact says "4-bit quantisation, 25% faster inference", use \
their framing for the same work. That is translation, not invention. Reordering \
and re-framing true facts is the entire job.

Prefer tier 1 and 2 facts for the most prominent positions. A tier 4 fact - true \
but never before written down - is completely valid to use and is often the \
highest-value thing available, because it is what the posting asks for and the \
old CV did not say.

## Voice

Reuse the candidate's own phrasing from `your words` wherever it fits. This is \
their CV, not yours.

Write plainly. Short concrete sentences. Vary their length - uniform sentence \
rhythm is the clearest sign of machine writing. Past tense for past work. No \
adjectives about the candidate: describe what they did and let the reader judge. \
Never use: leverage, spearheaded, passionate, results-driven, cutting-edge, \
seamlessly, proven track record, perfect fit, excited about the opportunity.

Do not claim enthusiasm for the company. Say something specific and checkable \
about why the work matches, or say nothing.

## The Anschreiben

Three to four short paragraphs, German business convention. Open with the role \
and one concrete reason you can do it - never "I am writing to apply". Middle \
paragraphs map your strongest evidence onto their top requirements. Close on \
availability and a plain sign-off. If the posting is in German, write the letter \
in German; otherwise English.

Do not restate the CV. The letter argues; the CV evidences."""

REVISE_PROMPT = """You are revising an application you already wrote, to answer a \
hiring manager's objections.

The same hard rule applies: every line cites facts from the base, nothing is \
invented, numbers are copied exactly.

For each objection, do exactly one of:
 - fix it, if the fact base supports a fix, or
 - leave it, if the base does not support one.

Leaving an objection unanswered is the correct response when the candidate \
genuinely lacks what is being asked for. Do NOT paper over a real gap with \
vaguer wording - that is the failure this process exists to prevent, and the \
next round will catch it anyway. Fix what is fixable and let the rest stand."""


def _job_block(job: Dict[str, Any]) -> str:
    return (
        f"## THE POSTING\n\nTitle: {job.get('job_title')}\n"
        f"Company: {job.get('company')}\n"
        f"Location: {job.get('location') or 'not stated'}\n\n"
        f"{(job.get('description') or '')[:9000]}"
    )


def _candidate_block(base: FactBase, personal: Dict[str, Any]) -> str:
    return (
        "## FACT BASE - the only material you may use\n\n"
        + base.render()
        + "\n\n## PERSONAL DETAILS (for headers and the letter's closing)\n"
        + json.dumps(personal, ensure_ascii=False, indent=1)
    )


def write(
    job: Dict[str, Any], base: FactBase, personal: Dict[str, Any]
) -> Optional[Application]:
    """First draft."""
    from scoring.llm_client import primary_client

    prompt = (
        _job_block(job) + "\n\n" + _candidate_block(base, personal)
        + "\n\nWrite the CV and the Anschreiben for this posting."
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt, system_prompt=SYSTEM_PROMPT,
            response_format=Application,
            temperature=settings.temperature_for(settings.WRITER_MODEL, 0.3),
            model_override=settings.WRITER_MODEL,
        )
        return Application.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Writing the application failed: %s", exc)
        return None


def revise(
    job: Dict[str, Any],
    base: FactBase,
    personal: Dict[str, Any],
    current: Application,
    objections: List[str],
    verifier_problems: Optional[str] = None,
) -> Optional[Application]:
    """Rewrite to address objections and any verifier failures."""
    from scoring.llm_client import primary_client

    notes = "## OBJECTIONS TO ANSWER\n\n" + "\n".join(f"- {o}" for o in objections)
    if verifier_problems:
        notes += (
            "\n\n## MECHANICAL CHECKS THAT FAILED - these must be fixed, they are "
            "not opinions\n\n" + verifier_problems
        )

    prompt = (
        _job_block(job) + "\n\n" + _candidate_block(base, personal)
        + "\n\n## YOUR CURRENT DRAFT\n\n"
        + json.dumps(current.model_dump(mode="json"), ensure_ascii=False, indent=1)
        + "\n\n" + notes
        + "\n\nProduce the revised application."
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt, system_prompt=SYSTEM_PROMPT + "\n\n" + REVISE_PROMPT,
            response_format=Application,
            temperature=settings.temperature_for(settings.WRITER_MODEL, 0.3),
            model_override=settings.WRITER_MODEL,
        )
        return Application.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Revising the application failed: %s", exc)
        return None
