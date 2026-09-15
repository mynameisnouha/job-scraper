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
from datetime import datetime, timezone
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

## Dates come from the timeline, never from you

Copy every role's dates verbatim from `experience_timeline` in the personal \
details block. Do not infer them, do not write "Present" for a role whose \
timeline gives an end date, and do not adjust a range to look more continuous. \
Nothing downstream checks these, so they are only as accurate as you are.

## Always include

Fill `education` and `languages` from the personal details block, every time, \
whatever the posting asks for. A CV missing its degree or its language levels \
reads as incomplete and gets marked down for a qualification the candidate \
actually holds - which is the exact failure this whole process exists to prevent.

That rule is about completeness of the *record*. It is not licence to include \
every fact, and the next section is the more important one.

## Fewer, better bullets

A fact is a unit of citation, not a unit of layout. The base is deliberately \
atomic - one achievement split across three or four facts - and writing one \
bullet per fact produces a CV that reads like a changelog. Do not do that.

- **Merge.** Facts describing one piece of work become ONE bullet citing all of \
them. `fact_ids` is a list precisely so this is possible.
- **Cut what is implied.** If a bullet follows from a stronger one, drop it. \
"Built a code review pipeline that flags quality issues on every pull request" \
already implies the API integration that fed it; a second bullet explaining the \
plumbing adds length, not evidence. Ask of each bullet: would a reader who \
believed the bullet above learn anything new here? If not, it goes.
- **Three bullets per role at most.** Two for anything older or further from \
this posting. A role's least relevant bullet is worth less than the white space \
it costs.
- **When two bullets compete, keep the one with a number.**
- **Relevance decides, not availability.** A fact that does not help this \
specific posting stays out, however true it is.

Skills: the ones this posting actually cares about, ordered by relevance. A \
twenty-item list reads as unfiltered and tells a reader nothing about what you \
are good at.

Judge the result by whether a busy reader gets your strongest evidence in the \
first fifteen seconds - not by how much of the fact base reached the page.

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
rhythm is the clearest sign of machine writing, and so is a set of bullets that \
all open with the same part of speech and run to the same length. Let some be \
short. Past tense for past work. No adjectives about the candidate: describe \
what they did and let the reader judge. Never use: leverage, spearheaded, \
passionate, results-driven, cutting-edge, seamlessly, proven track record, \
perfect fit, excited about the opportunity.

Say one thing per bullet. A bullet carrying three clauses joined by semicolons \
is two bullets that lost an argument, or one bullet with padding - either way, \
cut it back to the claim that matters.

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


def _availability_note() -> str:
    """Shared with the scorer, so both read the calendar the same way."""
    try:
        from scoring.score_jobs import availability_note

        return availability_note()
    except Exception:  # noqa: BLE001 - a missing profile must not stop a draft
        return datetime.now(timezone.utc).date().isoformat()


def _system(base: FactBase, personal: Dict[str, Any], extra: str = "") -> List[str]:
    """Instructions plus the candidate - everything that does not vary.

    The fact base is the expensive part of every writer call, several thousand
    tokens of it, and it is byte-identical on every round, every repair, and
    every job. Putting it in the system prompt lets it be cached: the first call
    pays for it and the rest read it at a fraction of the price. Anything that
    varies - the posting, the current draft, the objections - has to stay in the
    user message, because caching is a prefix match and one changed byte here
    would invalidate the whole thing.

    Returned as cache blocks, most stable first. The revise instructions get a
    block of their own AFTER the fact base rather than being spliced into the
    middle: a breakpoint hashes everything before it, so an insertion there
    made the draft and every revision distinct prefixes, and the fact base was
    re-sent at full price each time the loop switched between the two.
    """
    stable = (
        SYSTEM_PROMPT
        # Today's date and the gap to the earliest start, already worked out.
        # Without it the model reads availability against its own sense of the
        # present - somewhere near its training cutoff - and frames a five-month
        # wait as a year-plus one. Stable within a day, so the cached prefix
        # survives a working session.
        + "\n\n## TODAY\n"
        + _availability_note()
        + "\n\n## FACT BASE - the only material you may use\n\n"
        + base.render()
        + "\n\n## PERSONAL DETAILS (for headers and the letter's closing)\n"
        # sort_keys: dict order is insertion order, and the profile is loaded
        # from JSON, so it is stable in practice - but a reordered file would
        # silently cost the whole cache. Sorting makes the bytes deterministic.
        + json.dumps(personal, ensure_ascii=False, indent=1, sort_keys=True)
    )
    return [stable, extra] if extra else [stable]


def write(
    job: Dict[str, Any], base: FactBase, personal: Dict[str, Any]
) -> Optional[Application]:
    """First draft."""
    from scoring.llm_client import primary_client

    prompt = (
        _job_block(job)
        + "\n\nWrite the CV and the Anschreiben for this posting."
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt, system_prompt=_system(base, personal),
            cache_system=True,
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
        _job_block(job)
        + "\n\n## YOUR CURRENT DRAFT\n\n"
        + json.dumps(current.model_dump(mode="json"), ensure_ascii=False, indent=1)
        + "\n\n" + notes
        + "\n\nProduce the revised application."
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt, system_prompt=_system(base, personal, REVISE_PROMPT),
            cache_system=True,
            response_format=Application,
            temperature=settings.temperature_for(settings.WRITER_MODEL, 0.3),
            model_override=settings.WRITER_MODEL,
        )
        return Application.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Revising the application failed: %s", exc)
        return None
