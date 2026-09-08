"""Running the write / verify / judge cycle, and stopping at the right time.

Stopping is the part worth reading. The loop ends when a full round produces no
objection the previous rounds had not already raised - not when some quality
number stops climbing. Novelty runs out; a score never quite does, and chasing
one is how you end up with three extra rounds of a model agreeing with itself.

The scoring at the end deliberately sits OUTSIDE the loop, and reuses the job
queue's own scorer rather than a new one built for this package. Reusing it is
what makes the comparison mean anything: a purpose-built scorer would produce a
number comparable to nothing, whereas this one is the same judgement the whole
queue is ranked by.

Two rules keep that honest. The writer never sees the score - the moment a
document is optimised against it, it stops measuring and starts agreeing with
itself. And both CVs are scored in the same session, because the score already
stored on a job row is not a valid baseline: see holdout_compare.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tailor import judge as judge_mod
from tailor import settings, verify, writer
from tailor.documents import Application, render_cv
from tailor.facts import FactBase
from tailor.judge import Verdict
from tailor.verify import VerifyResult


@dataclass
class Round:
    number: int
    application: Application
    verification: VerifyResult
    verdict: Optional[Verdict]
    new_objections: List[str] = field(default_factory=list)
    repairs: int = 0


@dataclass
class Result:
    application: Optional[Application]
    rounds: List[Round] = field(default_factory=list)
    stopped_because: str = ""
    structural: List[str] = field(default_factory=list)
    score_before: Optional[int] = None
    score_after: Optional[int] = None
    score_note: str = ""

    @property
    def final_verdict(self) -> Optional[Verdict]:
        for rnd in reversed(self.rounds):
            if rnd.verdict is not None:
                return rnd.verdict
        return None


def _write_and_verify(
    make: Callable[[], Optional[Application]],
    repair: Callable[[Application, str], Optional[Application]],
    base: FactBase,
) -> tuple[Optional[Application], Optional[VerifyResult], int]:
    """Produce a document that passes the mechanical checks, or give up saying so.

    A verifier failure is not advice - it means a citation is missing or a number
    was invented - so the writer is sent back with the exact failures rather than
    the document being accepted with a warning.
    """
    app = make()
    if app is None:
        return None, None, 0

    result = verify.verify(app, base)
    repairs = 0
    while not result.ok and repairs < settings.MAX_REPAIR_ATTEMPTS:
        repairs += 1
        logging.info("Verifier rejected the draft (%d problems); repair %d.",
                     len(result.problems), repairs)
        repaired = repair(app, result.summary())
        if repaired is None:
            break
        app = repaired
        result = verify.verify(app, base)
    return app, result, repairs


def run(
    job: Dict[str, Any],
    base: FactBase,
    personal: Dict[str, Any],
    max_rounds: Optional[int] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Result:
    """Write, check, argue, revise - then score once, outside the loop."""
    max_rounds = max_rounds or settings.MAX_ROUNDS
    say = progress or (lambda _msg: None)
    result = Result(application=None)
    seen: set = set()
    structural: Dict[str, str] = {}

    app: Optional[Application] = None
    for number in range(1, max_rounds + 1):
        if number == 1:
            say(f"Round {number}: writing the first draft")
            app, verification, repairs = _write_and_verify(
                lambda: writer.write(job, base, personal),
                lambda draft, problems: writer.revise(
                    job, base, personal, draft, [], verifier_problems=problems),
                base,
            )
        else:
            objections = result.rounds[-1].new_objections
            say(f"Round {number}: revising against {len(objections)} objection(s)")
            current = app
            app, verification, repairs = _write_and_verify(
                lambda: writer.revise(job, base, personal, current, objections),
                lambda draft, problems: writer.revise(
                    job, base, personal, draft, objections, verifier_problems=problems),
                base,
            )

        if app is None or verification is None:
            result.stopped_because = "The writer failed to produce a usable draft."
            break

        say(f"Round {number}: the hiring manager is reading it")
        verdict = judge_mod.review(job, app)

        new: List[str] = []
        if verdict is not None:
            for objection in verdict.objections:
                if objection.structural:
                    structural.setdefault(
                        judge_mod.objection_key(objection),
                        f"{objection.requirement}: {objection.objection}",
                    )
            for objection in judge_mod.actionable(verdict):
                key = judge_mod.objection_key(objection)
                if key not in seen:
                    seen.add(key)
                    new.append(
                        f"[{objection.severity}] {objection.requirement} - "
                        f"{objection.objection} Fix: {objection.what_would_fix_it}"
                    )
            # AI-sounding lines are treated as objections too: they are quoted,
            # so they are as actionable as anything else the judge raises.
            for line in verdict.ai_tells:
                key = f"ai_tell|{line.strip().lower()[:40]}"
                if key not in seen:
                    seen.add(key)
                    new.append(f"[style] This line reads as machine-written: \"{line}\"")

        result.rounds.append(Round(number, app, verification, verdict, new, repairs))
        result.application = app

        if verdict is None:
            result.stopped_because = "The judge could not be reached; keeping the last draft."
            break
        if not new:
            result.stopped_because = (
                f"Round {number} raised nothing new — the objections had all been "
                "seen and answered already."
            )
            break
        if number == max_rounds:
            result.stopped_because = (
                f"Reached the {max_rounds}-round cap. Objections still open are "
                "listed below."
            )

    result.structural = list(structural.values())
    return result


def holdout_compare(
    job: Dict[str, Any], cv_text: str
) -> tuple[Optional[int], Optional[int], str]:
    """Score the original and the tailored CV against each other, in one session.

    Both are scored now, rather than comparing the new CV against the score
    already stored on the job row. That stored number is not a valid baseline:
    the scorer is a language model at non-zero temperature, its prompt has
    changed over the life of the corpus, and rows were scored across many
    separate runs. Measured on one posting here, the same unchanged CV scored 68
    when the row was written and 42 on a fresh call - so a tailored CV that had
    genuinely improved on the original looked like a 16-point regression purely
    from baseline drift. That is exactly the wrong conclusion to hand someone
    about their own CV.

    Scoring both in the same session costs one extra call and removes the
    confound. Only the difference is meaningful; neither number is an absolute.
    """
    try:
        from db import supabase_utils
        from scoring.score_jobs import format_resume_to_text, get_resume_score_from_ai

        after_breakdown = get_resume_score_from_ai(cv_text, job)
        after = int(after_breakdown.overall_score) if after_breakdown else None

        before = None
        resume = supabase_utils.get_base_resume()
        if resume:
            before_breakdown = get_resume_score_from_ai(format_resume_to_text(resume), job)
            before = int(before_breakdown.overall_score) if before_breakdown else None

        note = (
            "Both CVs were scored just now, in the same session, by the queue's own "
            "scorer. The score already on the job row is NOT a valid baseline - rows "
            "were scored across many runs and the scorer drifts between them - so only "
            "the difference here means anything. Writer and scorer are both language "
            "models, so read a rise as a check that nothing broke rather than as proof "
            "of a better outcome. Only replies from employers settle that."
        )
        return before, after, note
    except Exception as exc:  # noqa: BLE001
        logging.error("Holdout scoring failed: %s", exc)
        return None, None, f"Scoring failed: {exc}"
