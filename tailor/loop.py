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

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tailor import interview as interview_mod
from tailor import judge as judge_mod
from tailor import settings, verify, writer
from tailor.documents import Application, render_cv
from tailor.facts import FactBase
from tailor.interview import Question
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
    # True when a round produced a draft the hiring manager never read. Tracked
    # separately from stopped_because because it changes what the output *is* -
    # an unreviewed first draft, not a finished argument - and that has to reach
    # the screen rather than sitting in a log line.
    judge_failed: bool = False
    score_before: Optional[int] = None
    score_after: Optional[int] = None
    score_note: str = ""
    # State a later continuation needs: which objections have already been
    # raised, which of them are structural, and what the final round left
    # unanswered. Without these, adding a round would re-raise everything the
    # earlier rounds already dealt with.
    seen_keys: List[str] = field(default_factory=list)
    structural_map: Dict[str, str] = field(default_factory=dict)
    pending_objections: List[str] = field(default_factory=list)
    # Set when the loop stopped mid-run to put questions back to the candidate.
    # The run is unfinished by design here: the draft is real, the objections
    # behind the questions are still open, and answering them is what the next
    # round needs. Kept distinct from stopped_because because the caller has to
    # *act* on it, not merely display it.
    awaiting_answers: bool = False
    questions: List[Question] = field(default_factory=list)
    facts_added: List[str] = field(default_factory=list)

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


@dataclass
class Continuation:
    """Everything needed to push an existing run a round or two further.

    Carrying `seen` across is the point. Without it a continuation re-reads every
    objection the earlier rounds already answered as brand new, so the loop would
    never stop early and the writer would be sent back to fix things it has
    already fixed.
    """

    application: Application
    pending_objections: List[str] = field(default_factory=list)
    seen: List[str] = field(default_factory=list)
    structural: Dict[str, str] = field(default_factory=dict)
    round_offset: int = 0


def run(
    job: Dict[str, Any],
    base: FactBase,
    personal: Dict[str, Any],
    max_rounds: Optional[int] = None,
    progress: Optional[Callable[[str], None]] = None,
    resume: Optional[Continuation] = None,
    probe: bool = False,
    ask: Optional[Callable[[List[Question]], Optional[Dict[str, str]]]] = None,
) -> Result:
    """Write, check, argue, revise - then score once, outside the loop.

    With `resume`, the loop picks up an existing draft instead of writing a new
    one: `max_rounds` then counts *additional* rounds rather than total. That
    keeps a second pass cheap, and more importantly keeps the document stable -
    regenerating from scratch throws away a draft you may already have read and
    replaces it with a different one for no reason.

    With `probe`, a question step runs between rounds. The rewrite step can only
    rearrange what the fact base already holds, so an objection asking for
    evidence that is not in there cannot be answered by any number of further
    rounds - the writer either ignores it or invents something and the verifier
    stops it. Putting those objections back to the candidate is the only step in
    the loop that adds information rather than moving it around, and it is aimed
    better than the opening interview because a hiring manager has now read an
    actual draft and said what is missing.

    Answering needs a human, which the caller has to arrange. Pass `ask` to be
    called with the questions and hand back `{question_id: answer}`; without one
    the loop stops at the question step, returns the questions with
    `awaiting_answers` set, and the caller resumes it afterwards with
    `Continuation`. Answers are written into `base` in memory - **the caller
    saves it**, matching `answers_to_facts`, so a crash mid-run cannot leave a
    half-written fact base on disk.
    """
    max_rounds = max_rounds or settings.MAX_ROUNDS
    say = progress or (lambda _msg: None)
    result = Result(application=None)
    seen: set = set(resume.seen) if resume else set()
    structural: Dict[str, str] = dict(resume.structural) if resume else {}
    offset = resume.round_offset if resume else 0
    pending: List[str] = list(resume.pending_objections) if resume else []

    app: Optional[Application] = resume.application if resume else None
    # A continuation starts from a draft that already exists, so hold onto it:
    # if the writer then fails, the run should hand back the document it began
    # with rather than nothing at all.
    result.application = app

    for step in range(1, max_rounds + 1):
        number = step + offset
        if app is None:
            say(f"Round {number}: writing the first draft")
            app, verification, repairs = _write_and_verify(
                lambda: writer.write(job, base, personal),
                lambda draft, problems: writer.revise(
                    job, base, personal, draft, [], verifier_problems=problems),
                base,
            )
        else:
            objections = pending
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
        pending = new

        if verdict is None:
            result.judge_failed = True
            reviewed = sum(1 for r in result.rounds if r.verdict is not None)
            result.stopped_because = (
                f"The review of round {number} could not be reached. "
                + (f"Rounds 1-{number - 1} were reviewed, but the draft below is the "
                   f"round-{number} rewrite, which no review has read."
                   if reviewed else
                   "This draft was never reviewed at all.")
                + " Add a round to have it read."
            )
            break
        if not new:
            result.stopped_because = (
                f"Round {number} raised nothing new — the objections had all been "
                "seen and answered already."
            )
            break

        if probe and step < max_rounds:
            # Only on rounds that have a rewrite left to spend the answers on. A
            # probe on the final round asks for evidence nothing will then use,
            # which wastes the one question budget the candidate will actually
            # sit through.
            say(f"Round {number}: working out what only you can answer")
            questions = _probe(job, base, new)
            if questions:
                answers = ask(questions) if ask else None
                if answers is None:
                    result.awaiting_answers = True
                    result.questions = questions
                    result.stopped_because = (
                        f"Paused after round {number}. {len(questions)} of the "
                        "objections need evidence your fact base does not hold — no "
                        "rewrite can answer those, only you can. Answer them (or say "
                        "no) and the next round uses what you add."
                    )
                    break
                say(f"Round {number}: turning your answers into facts")
                result.facts_added += interview_mod.answers_to_facts(
                    questions, answers, base)

        if step == max_rounds:
            result.stopped_because = (
                f"Stopped after round {number}. Objections still open are listed "
                "below — add another round to work through them."
            )

    result.structural = list(structural.values())
    # Carried so a later continuation knows what has already been raised and
    # what the last round left unanswered.
    result.seen_keys = sorted(seen)
    result.structural_map = structural
    result.pending_objections = pending
    return result


def _probe(job: Dict[str, Any], base: FactBase, objections: List[str]) -> List[Question]:
    """The questions worth interrupting a run for, or an empty list.

    Style flags are dropped before asking: a line that reads as machine-written
    is the writer's to rewrite, and no answer from the candidate bears on it.
    """
    substantive = [o for o in objections if not o.startswith("[style]")]
    if not substantive:
        return []
    probed = interview_mod.probe_objections(job, base, substantive)
    return list(probed.questions) if probed else []


def continuation_from(saved: Dict[str, Any]) -> Optional[Continuation]:
    """Rebuild the state needed to extend a run that was saved to disk."""
    payload = saved.get("application")
    if not payload:
        return None
    try:
        application = Application.model_validate(payload)
    except Exception:  # noqa: BLE001 - a stale document shape is not worth a crash
        logging.warning("Saved application could not be reloaded; cannot continue it.")
        return None
    rounds = saved.get("rounds") or []
    return Continuation(
        application=application,
        pending_objections=list(rounds[-1].get("new_objections") or []) if rounds else [],
        seen=list(saved.get("seen_keys") or []),
        structural=dict(saved.get("structural_map") or {}),
        round_offset=max((r.get("number") or 0) for r in rounds) if rounds else 0,
    )


_BASELINE_CACHE = os.path.join(settings.CACHE_DIR, "baseline_scores.json")


def _baseline_key(job: Dict[str, Any], original_cv: str) -> str:
    """Identity of a baseline: this posting, this CV text, this evidence.

    The evidence block belongs in the key. The scorer now reads the confirmed
    facts that are true but absent from the CV alongside it, so answering one
    interview question changes what the *unchanged* CV scores - and a baseline
    cached before that answer would be compared against a tailored CV scored
    after it. That is the same stale-baseline error holdout_compare exists to
    avoid, arriving through the cache instead of through the job row.
    """
    from scoring.score_jobs import EVIDENCE_NOT_ON_CV

    material = original_cv + " --- evidence --- " + EVIDENCE_NOT_ON_CV
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{job.get('job_id')}:{digest}"


def _load_baselines() -> Dict[str, int]:
    if not os.path.exists(_BASELINE_CACHE):
        return {}
    try:
        with open(_BASELINE_CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


def _cached_baseline(job: Dict[str, Any], original_cv: str) -> Optional[int]:
    return _load_baselines().get(_baseline_key(job, original_cv))


def _store_baseline(job: Dict[str, Any], original_cv: str, score: int) -> None:
    cache = _load_baselines()
    cache[_baseline_key(job, original_cv)] = score
    try:
        os.makedirs(settings.CACHE_DIR, exist_ok=True)
        with open(_BASELINE_CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1)
    except OSError as exc:
        logging.warning("Could not cache the baseline score: %s", exc)


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
            # The baseline is a property of (this posting, this unchanged CV), so
            # it cannot move between regenerations of the same job - but it is a
            # full scoring call, half the cost of this comparison. Cached against
            # a hash of the CV text, so editing the CV invalidates it and nothing
            # else does.
            original = format_resume_to_text(resume)
            before = _cached_baseline(job, original)
            if before is None:
                before_breakdown = get_resume_score_from_ai(original, job)
                before = int(before_breakdown.overall_score) if before_breakdown else None
                if before is not None:
                    _store_baseline(job, original, before)

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
