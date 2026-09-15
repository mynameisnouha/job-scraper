"""Asking about the gaps, so the answer is available to every future application.

This is the step that produces most of the value, and it is the only one that
adds information rather than rearranging it. Everything else in the package can
only present what is already known; this asks.

Questions are capped and ordered by what the posting actually requires. Answers
become facts, permanently - so the base gets richer and the interviews get
shorter with every job worked through.

There are two of these. `find_gaps` reads the posting cold, before anything is
written. `probe_objections` runs between rounds of the loop, off what the hiring
manager objected to after reading a real draft - a much narrower target, and the
reason it asks fewer questions.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from tailor import settings
from tailor.facts import SOURCE_INTERVIEW, FactBase

SYSTEM_PROMPT = """You are finding out whether a candidate has experience the job \
posting requires but their fact base does not record.

You are NOT writing a CV and NOT assessing the candidate. You are closing \
information gaps.

Rules:

1. Ask only about requirements the posting genuinely makes where the fact base \
is silent or unclear. If the base already covers something, do not ask about it.

2. Prefer questions where a "yes" is plausible given what the base already \
shows. Someone who fine-tuned a model with QLoRA almost certainly used PyTorch, \
so asking whether they did is worth a question. Asking a candidate with no \
infrastructure work whether they run Kubernetes clusters is not - it wastes one \
of very few questions on a near-certain no.

3. Be specific enough to answer in one sentence. "Do you have experience with \
orchestration?" is unanswerable. "Did you schedule the Databricks pipeline with \
Airflow, Databricks Workflows, or something else?" can be answered instantly.

4. Name the evidence you are looking for. A question should tell the candidate \
what would make the answer usable: where they did it, roughly when, and any \
number attached.

5. Never imply the candidate should claim something. If the honest answer is no, \
the question must make "no" an easy and expected reply.

6. At most {max_questions} questions. Most important first."""


class Question(BaseModel):
    id: str = Field(..., description="q1, q2, ...")
    requirement: str = Field(..., description="The posting requirement this is about.")
    question: str = Field(..., description="One direct question, answerable in a sentence.")
    why_it_matters: str = Field(
        ..., description="What this unlocks on the CV if the answer is yes. Max 20 words."
    )
    likely: str = Field(
        "unknown",
        description=(
            "Your read on whether the candidate probably has this, from the fact "
            "base: 'probably', 'unclear', or 'probably not'."
        ),
    )


class Interview(BaseModel):
    questions: List[Question] = Field(default_factory=list)
    already_covered: List[str] = Field(
        default_factory=list,
        description="Requirements the fact base already answers. Max 8, short phrases.",
    )


def find_gaps(job: Dict[str, Any], base: FactBase) -> Optional[Interview]:
    """Which of this posting's requirements the fact base cannot currently answer."""
    from scoring.llm_client import primary_client

    prompt = (
        f"## JOB POSTING\n\nTitle: {job.get('job_title')}\n"
        f"Company: {job.get('company')}\n\n"
        f"{(job.get('description') or '')[:9000]}\n\n"
        f"## FACT BASE (everything currently known about the candidate)\n\n"
        f"{base.render()}\n\n"
        "Which requirements of this posting does the fact base not answer?"
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT.format(max_questions=settings.MAX_QUESTIONS),
            response_format=Interview,
            temperature=0.0,
        )
        interview = Interview.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Gap interview failed: %s", exc)
        return None

    interview.questions = interview.questions[: settings.MAX_QUESTIONS]
    return interview


class _AnswerFact(BaseModel):
    claim: str
    context: str = ""
    metrics: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    tier: int = 4
    your_words: str = ""


class _AnswerOutput(BaseModel):
    facts: List[_AnswerFact] = Field(
        default_factory=list,
        description="Empty when the answer was a no, or carried nothing checkable.",
    )


ANSWER_PROMPT = """You are turning a candidate's answers into atomic facts for \
their fact base.

Rules:

1. A "no", "never done that" or empty answer produces NO facts. Do not soften a \
no into a partial yes. This is the most important rule here: the fact base is \
the thing that keeps every generated CV honest, and one invented fact \
contaminates every future application.

2. Do not upgrade what you are told. If they say they used a tool once in a \
university project, that is tier 3, not tier 2. If no number is given, metrics \
stays empty.

3. Default tier is 4 - true, but not currently on the CV - because that is what \
these answers are by definition. Use a lower tier only if the answer makes clear \
it is already written on their CV.

4. your_words must copy the candidate's own phrasing verbatim. Their voice is \
the point.

5. Split multi-part answers into separate facts.

6. Preserve status. Work described as ongoing stays ongoing - never write \
"completed" or "delivered" for something still in progress, and treat a date \
range ending in the future as in progress. Writing a thesis that runs to next \
February as "completed" is the small, plausible overstatement this whole system \
exists to stop, and it is the kind an interviewer catches in one question.

7. Put the employer, institution and dates in `context`, not inside the claim. \
The claim is what was done; the context is where and when."""


def text_to_facts(text: str, base: FactBase) -> List[str]:
    """Turn something you just typed about yourself into facts. Returns new ids.

    The gap interview only asks about what a *posting* demands, which leaves a
    blind spot: work that is real and relevant but that no single posting happens
    to name. A Master's thesis is the case that exposed it - absent from the
    parsed CV, mentioned in the profile notes only as an availability
    constraint, and therefore uncitable, so no generated CV could mention it.

    Nothing here is posting-specific, so what you add is available to every
    application from then on.
    """
    from scoring.llm_client import primary_client

    if not text.strip():
        return []

    try:
        raw = primary_client.generate_content(
            prompt=text.strip() + "\n\nTurn this into facts.",
            system_prompt=ANSWER_PROMPT,
            response_format=_AnswerOutput,
            temperature=0.0,
        )
        parsed = _AnswerOutput.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Converting free text to facts failed: %s", exc)
        return []

    new_ids = []
    for item in parsed.facts:
        fact = base.add(
            claim=item.claim, context=item.context, metrics=item.metrics,
            skills=[s.lower() for s in item.skills], tier=item.tier,
            source=SOURCE_INTERVIEW, your_words=item.your_words,
        )
        new_ids.append(fact.id)
    return new_ids


def answers_to_facts(
    questions: List[Question], answers: Dict[str, str], base: FactBase
) -> List[str]:
    """Convert answers into facts, appended to the base. Returns the new ids.

    The base is saved by the caller so a failed conversion cannot half-write it.
    """
    from scoring.llm_client import primary_client

    filled = [
        (q, answers.get(q.id, "").strip())
        for q in questions
        if answers.get(q.id, "").strip()
    ]
    if not filled:
        return []

    payload = "\n\n".join(
        f"Q ({q.requirement}): {q.question}\nA: {answer}" for q, answer in filled
    )
    try:
        raw = primary_client.generate_content(
            prompt=payload + "\n\nTurn these answers into facts.",
            system_prompt=ANSWER_PROMPT,
            response_format=_AnswerOutput,
            temperature=0.0,
        )
        parsed = _AnswerOutput.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("Converting answers to facts failed: %s", exc)
        return []

    new_ids = []
    for item in parsed.facts:
        fact = base.add(
            claim=item.claim, context=item.context, metrics=item.metrics,
            skills=[s.lower() for s in item.skills], tier=item.tier,
            source=SOURCE_INTERVIEW, your_words=item.your_words,
        )
        new_ids.append(fact.id)
    return new_ids


PROBE_PROMPT = """A hiring manager has just read a draft application and raised \
the objections below. You are deciding which of them are questions for the \
candidate rather than instructions for the writer.

Most objections are the writer's problem: wrong emphasis, a buried point, a \
claim stated weakly, a letter that restates the CV. Rewriting fixes those and \
you must NOT ask about them.

A few are not fixable by rewriting at all, because the evidence the objection \
asks for is simply not in the fact base. Those are yours. Ask about them.

Ask only where ALL of these hold:
 - the objection asks for evidence (a number, a scale, a tool, an outcome, who \
owned what) that the fact base does not contain
 - a truthful one-sentence answer would let the next draft answer the objection
 - a "yes" is plausible given what the base already shows

Do not ask:
 - about anything the fact base already answers, even weakly - say so in \
`already_covered` instead
 - about a qualification the candidate evidently does not have. The objection \
stands and the candidate should be told, not interrogated
 - for a rewrite in the candidate's words. You are collecting facts, not copy

Question style:
 - answerable in one sentence, specific enough to answer instantly
 - name the evidence wanted: where, roughly when, and any number attached
 - make "no" an easy and expected reply. Never imply what they should claim

At most {max_questions} questions, most important first. Returning zero is a \
perfectly good answer and the right one when every objection is the writer's to \
fix."""


def probe_objections(
    job: Dict[str, Any], base: FactBase, objections: List[str]
) -> Optional[Interview]:
    """Turn a round's objections into questions only the candidate can answer.

    The loop's rewrite step can only rearrange what is already known, so an
    objection that asks for evidence the fact base does not hold survives every
    round: the writer cannot answer it without inventing something, and the
    verifier stops it if it tries. Those objections are the ones worth putting
    back to the candidate mid-run rather than at the end, because an answer given
    now reaches the next rewrite instead of the next job.

    Kept separate from `find_gaps`, which reads the posting cold. This reads what
    a hiring manager actually objected to after seeing a draft, which is a much
    narrower and better-aimed target - and the reason the question cap here is
    lower.
    """
    from scoring.llm_client import primary_client

    if not objections:
        return None

    prompt = (
        f"## JOB POSTING\n\nTitle: {job.get('job_title')}\n"
        f"Company: {job.get('company')}\n\n"
        f"{(job.get('description') or '')[:6000]}\n\n"
        f"## FACT BASE (everything currently known about the candidate)\n\n"
        f"{base.render()}\n\n"
        "## THE HIRING MANAGER'S OBJECTIONS TO THE CURRENT DRAFT\n\n"
        + "\n".join(f"- {o}" for o in objections)
        + "\n\nWhich of these can only be answered by asking the candidate?"
    )
    try:
        raw = primary_client.generate_content(
            prompt=prompt,
            system_prompt=PROBE_PROMPT.format(
                max_questions=settings.MAX_PROBE_QUESTIONS),
            response_format=Interview,
            temperature=0.0,
        )
        interview = Interview.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - a failed probe must not end the run
        logging.error("Mid-round probe failed: %s", exc)
        return None

    interview.questions = interview.questions[: settings.MAX_PROBE_QUESTIONS]
    return interview
