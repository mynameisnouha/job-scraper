"""The fact base: everything true about you, in pieces small enough to cite.

This is the asset the rest of the package is built around. A CV is a lossy
summary of a person - it drops whatever did not fit on the page last time it was
edited - and the scorer marks you down for what it cannot see. Measured on this
corpus, the top-scoring unapplied postings dock points for "PyTorch not
mentioned on resume" against a candidate who fine-tuned a model with QLoRA. The
skill was there; the sentence was not.

So facts are stored separately from any CV, atomically, with their evidence
tier. Every CV this package writes is then a *selection* from the base rather
than a fresh act of authorship - which is what keeps generated CVs honest, and
what makes an answer you give once available to every application afterwards.

Facts are never deleted on regeneration. The base only grows.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from tailor import settings

# Where a fact came from. Kept because it changes how much the writer should
# lean on it: a CV line is already public, an interview answer has never been
# read by an employer and may need more context to land.
SOURCE_CV = "cv"
SOURCE_PROFILE = "profile"
SOURCE_INTERVIEW = "interview"
# Noticed by the job scorer while it worked through the queue, and NOT confirmed
# by the candidate. Everything from this source lands unconfirmed - see the
# `confirmed` field - because the scorer is reading a CV and a posting, not the
# candidate's memory. It is a lead, not evidence.
SOURCE_SCORER = "scorer"


def _today() -> str:
    """Today, the earliest start date, and the rule against inventing durations."""
    from scoring.availability import note

    return note()


class Fact(BaseModel):
    """One atomic, checkable thing that is true about the candidate."""

    id: str = Field(..., description="Stable short id, e.g. f017.")
    claim: str = Field(..., description="What is true, in one sentence, past tense, concrete.")
    context: str = Field(
        "", description="Where and when: employer or project, role, dates."
    )
    metrics: List[str] = Field(
        default_factory=list,
        description="Numbers attached to this fact, verbatim, e.g. '30% memory reduction'.",
    )
    skills: List[str] = Field(
        default_factory=list,
        description="Technologies or capabilities this fact evidences, lowercase.",
    )
    tier: int = Field(
        3,
        ge=1,
        le=5,
        description=(
            "1 = shipped to production with a metric. 2 = built and working, no metric. "
            "3 = coursework, personal project, or a bare skills-list entry. "
            "4 = true but not written on the CV anywhere. 5 = not done."
        ),
    )
    source: str = Field(SOURCE_CV, description="cv, profile, interview, or scorer.")
    confirmed: bool = Field(
        True,
        description=(
            "False for a lead nobody has verified yet. Unconfirmed facts are NOT "
            "citable: the writer never sees them and the verifier rejects any line "
            "that cites one. They exist to be asked about, and become citable only "
            "when the candidate answers in their own words."
        ),
    )
    seen_in: List[str] = Field(
        default_factory=list,
        description=(
            "Job ids where this came up. Only meaningful for scorer leads, where "
            "the count is the whole signal: one posting wanting Kubernetes is "
            "noise, fourteen is a gap worth an evening."
        ),
    )
    your_words: str = Field(
        "",
        description=(
            "The candidate's own phrasing, kept verbatim. The writer reuses this "
            "wherever it can, which is most of what stops the output sounding generic."
        ),
    )
    added_at: str = ""


class FactBase(BaseModel):
    facts: List[Fact] = Field(default_factory=list)

    def by_id(self, fact_id: str) -> Optional[Fact]:
        return next((f for f in self.facts if f.id == fact_id), None)

    def citable(self) -> List[Fact]:
        """The facts a CV may actually rest on.

        Unconfirmed leads are excluded here rather than filtered at each call
        site, because there is exactly one way this goes wrong and it is silent:
        a lead the scorer inferred gets cited, the verifier sees a real id, and
        the CV states something the candidate never said. Making the base itself
        withhold them means a new caller has to opt in to that, deliberately.
        """
        return [f for f in self.facts if f.confirmed]

    def unconfirmed(self) -> List[Fact]:
        """Leads waiting to be confirmed or dismissed, most-seen first."""
        return sorted((f for f in self.facts if not f.confirmed),
                      key=lambda f: (-len(f.seen_in), f.id))

    def ids(self) -> set:
        return {f.id for f in self.citable()}

    def next_id(self) -> str:
        used = {int(f.id[1:]) for f in self.facts if f.id[1:].isdigit()}
        return f"f{(max(used) + 1) if used else 1:03d}"

    def add(self, **kwargs: Any) -> Fact:
        fact = Fact(id=self.next_id(),
                    added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    **kwargs)
        self.facts.append(fact)
        return fact

    def confirm(self, fact_id: str, your_words: str = "", tier: Optional[int] = None) -> Optional[Fact]:
        """Promote a lead to a citable fact, in the candidate's words.

        `your_words` is what actually changes here. A lead is phrased by the
        scorer ("candidate likely has Airflow exposure not shown on the CV"); a
        fact is phrased by the person who did the work, and the writer leans on
        that phrasing to stop the output sounding generic.
        """
        fact = self.by_id(fact_id)
        if fact is None:
            return None
        fact.confirmed = True
        fact.source = SOURCE_INTERVIEW
        fact.tier = tier if tier is not None else 4
        if your_words:
            fact.your_words = your_words
        return fact

    def drop(self, fact_id: str) -> bool:
        """Remove a lead the candidate says is wrong.

        The base "only grows" rule is about facts. A lead is not a fact, and a
        wrong one that stays would be re-asked every time the panel is opened -
        which is how a helpful prompt turns into a nag.
        """
        fact = self.by_id(fact_id)
        if fact is None or fact.confirmed:
            return False
        self.facts = [f for f in self.facts if f.id != fact_id]
        return True

    def render(self, ids: Optional[List[str]] = None) -> str:
        """The confirmed base as text for a prompt, one line per fact.

        Deliberately compact and id-first: the writer has to cite ids, so the id
        has to be the most visible thing on every line.

        Unconfirmed leads are not in here. A writer shown a lead will use it -
        that is what a writer is for - and it would be citing the scorer's guess
        about the candidate back at an employer.
        """
        pool = self.citable()
        chosen = pool if ids is None else [f for f in pool if f.id in set(ids)]
        lines = []
        for f in sorted(chosen, key=lambda f: (f.tier, f.id)):
            bits = [f"[{f.id}] (tier {f.tier}) {f.claim}"]
            if f.context:
                bits.append(f"   where: {f.context}")
            if f.metrics:
                bits.append(f"   numbers: {'; '.join(f.metrics)}")
            if f.skills:
                bits.append(f"   evidences: {', '.join(f.skills)}")
            if f.your_words and f.your_words != f.claim:
                bits.append(f"   your words: \"{f.your_words}\"")
            lines.append("\n".join(bits))
        return "\n\n".join(lines)


    def evidence_not_on_cv(self) -> List[Fact]:
        """Confirmed facts the CV itself does not state.

        Tier 4 is the definition of that, and interview answers are tier 4 by
        construction - they came from a question the CV could not answer. Facts
        seeded from the CV are excluded because the scorer is already reading the
        CV; repeating them would spend tokens telling it what it can see.

        Tier 5 never appears here whatever its source: 5 means not done.
        """
        return [f for f in self.citable()
                if f.tier < 5 and (f.tier == 4 or f.source == SOURCE_INTERVIEW)]

    def render_for_scoring(self) -> str:
        """The evidence block the job scorer reads beside the CV, or empty.

        Kept separate from the CV text on purpose, and the separation is the
        whole point of the block. The scorer's job is partly to report what your
        CV fails to show - `fixable_before_applying`, and the honest gap between
        `p_first_round_interview.as_is` and `.after_fixes`. Pasting these facts
        into the CV text would make every one of those fields say the CV already
        shows it, which is both false and the end of the one signal that tells
        you what to go and add.

        Unconfirmed leads cannot reach here: `citable()` withholds them. That
        matters more on this path than anywhere else, because a lead came FROM
        the scorer - feeding it back would have the scorer read its own guess as
        evidence, stop reporting the gap, and raise the score with nothing having
        become true.
        """
        facts = self.evidence_not_on_cv()
        if not facts:
            return ""
        lines = []
        for f in sorted(facts, key=lambda f: (f.tier, f.id)):
            bits = f"[{f.id}] (tier {f.tier}) {f.claim}"
            if f.context:
                bits += f" — {f.context}"
            if f.metrics:
                bits += f" [{'; '.join(f.metrics)}]"
            if f.skills:
                bits += f" (evidences: {', '.join(f.skills)})"
            lines.append(bits)
        return "\n".join(lines)


def load() -> FactBase:
    if not os.path.exists(settings.FACTS_PATH):
        return FactBase()
    try:
        with open(settings.FACTS_PATH, "r", encoding="utf-8") as fh:
            return FactBase.model_validate(json.load(fh))
    except (ValueError, OSError) as exc:
        logging.error("Fact base unreadable (%s). Refusing to overwrite it.", exc)
        raise


def save(base: FactBase) -> None:
    """Write atomically. The fact base accumulates answers you gave once and
    would not enjoy giving again, so a half-written file is the one failure
    mode worth engineering against."""
    os.makedirs(os.path.dirname(settings.FACTS_PATH) or ".", exist_ok=True)
    tmp = settings.FACTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(base.model_dump(mode="json"), fh, ensure_ascii=False, indent=1)
    os.replace(tmp, settings.FACTS_PATH)


# --- Seeding ------------------------------------------------------------------

class _SeedFact(BaseModel):
    claim: str
    context: str = ""
    metrics: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    tier: int = 3
    your_words: str = ""


class _SeedOutput(BaseModel):
    facts: List[_SeedFact]


SEED_PROMPT = """You are breaking a CV into atomic facts so each one can be cited \
individually later.

Rules:

1. One fact per achievement, tool, or responsibility. If a CV bullet contains \
three separate accomplishments, that is three facts. If it names four \
technologies used for one thing, that is still one fact with four skills.

2. Copy numbers EXACTLY as written. "30% memory reduction" stays "30% memory \
reduction" - never round, rephrase or combine numbers. These get checked \
mechanically later, and a reworded number fails the check.

3. your_words must be the candidate's own phrasing from the CV, copied verbatim. \
It is used to preserve their voice, so a paraphrase defeats the purpose.

4. Tier honestly. Tier 1 requires production use AND a stated number. A bare \
entry in a skills list with no project behind it is tier 3, not tier 2.

5. Record implied tooling in `skills` when it is unambiguous - fine-tuning with \
QLoRA/peft means pytorch, deploying on ECS means aws and docker - but never \
invent an achievement that is not described. Adding a skill the work \
necessarily used is fair; adding work that did not happen is not.

6. Do not editorialise. No "successfully", no "significantly". State what happened.

7. Cover the WHOLE CV, not just the achievements. Emit a fact for each degree, \
each certification, each language with its level, and each entry in the skills \
list that no achievement already evidences (those are tier 3 - listed, but with \
no project behind them on the CV).

This last rule matters more than it looks. Anything absent from the fact base \
cannot appear on a generated CV, so a skipped degree or language is not a \
cosmetic omission - it silently deletes a qualification from every application \
written afterwards."""


def seed_from_cv(resume: Dict[str, Any], profile_notes: str = "") -> FactBase:
    """Build a fresh fact base from the stored CV plus the profile notes.

    Only ever called when the base is empty - re-seeding an existing base would
    duplicate every fact and break the ids that already-generated CVs cite.
    """
    from scoring.llm_client import primary_client

    prompt = (
        "## CV\n"
        + json.dumps(resume, ensure_ascii=False, indent=1)
        + "\n\n## PROFILE NOTES\n"
        + (profile_notes or "(none)")
        + "\n\nBreak this into atomic facts."
    )
    raw = primary_client.generate_content(
        # The date block belongs here too: seeding decides whether a role or a
        # thesis with a date range is finished or still running, and that is not
        # answerable without knowing today. A seeded fact is also the most
        # expensive one to get wrong — it is written once and cited by every CV
        # afterwards.
        prompt=prompt, system_prompt="## TODAY\n" + _today() + "\n\n" + SEED_PROMPT,
        response_format=_SeedOutput, temperature=0.0,
    )
    seeds = _SeedOutput.model_validate_json(raw)

    base = FactBase()
    for seed in seeds.facts:
        base.add(
            claim=seed.claim, context=seed.context, metrics=seed.metrics,
            skills=[s.lower() for s in seed.skills], tier=seed.tier,
            source=SOURCE_PROFILE if seed.tier == 4 else SOURCE_CV,
            your_words=seed.your_words,
        )
    return base


def ensure_seeded() -> FactBase:
    """Load the fact base, seeding it from the CV the first time."""
    base = load()
    if base.facts:
        return base

    from db import supabase_utils

    resume = supabase_utils.get_base_resume()
    if not resume:
        raise RuntimeError(
            "No base resume in Supabase - run `python -m resume.resume_parser` first."
        )

    profile_notes = ""
    profile_path = os.path.join(os.path.dirname(settings.FACTS_PATH), "candidate_profile.json")
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            profile_notes = "\n".join(
                str(v) for k, v in data.items() if k in ("short_profile", "profile_v2")
            )
        except (ValueError, OSError):
            logging.warning("candidate_profile.json unreadable; seeding from the CV alone.")

    base = seed_from_cv(resume, profile_notes)
    save(base)
    logging.info("Seeded fact base with %d facts.", len(base.facts))
    return base
