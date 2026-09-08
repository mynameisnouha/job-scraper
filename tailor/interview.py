"""Asking about the gaps, so the answer is available to every future application.

This is the step that produces most of the value, and it is the only one that
adds information rather than rearranging it. Everything else in the package can
only present what is already known; this asks.

Questions are capped and ordered by what the posting actually requires. Answers
become facts, permanently - so the base gets richer and the interviews get
shorter with every job worked through.
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

5. Split multi-part answers into separate facts."""


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
