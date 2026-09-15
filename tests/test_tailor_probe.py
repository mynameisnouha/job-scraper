"""The question step between rounds: when it fires, and what it does with answers.

Every model call in the loop is stubbed. What is under test is the control flow -
whether the run pauses, whether it resumes without re-raising answered objections,
and whether a probe is skipped where its answers would have nowhere to go.
"""

import pytest

from tailor import loop as tailor_loop
from tailor.documents import Application, CoverLetter, Line, TailoredCV
from tailor.interview import Question
from tailor.judge import Objection, Verdict
from tailor.verify import VerifyResult

JOB = {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME",
       "description": "Airflow, Databricks, production models."}


def _app(text="Built the pipeline."):
    return Application(
        cv=TailoredCV(headline="ML Engineer", summary=Line(text=text, fact_ids=["f1"])),
        cover_letter=CoverLetter(subject="ML Engineer", greeting="Hallo,",
                                 paragraphs=[Line(text=text, fact_ids=["f1"])],
                                 closing="Beste Gruesse"),
    )


def _verdict(requirement="Airflow", objection="No evidence of scheduling work."):
    return Verdict(
        objections=[Objection(requirement=requirement, objection=objection,
                              severity="major", what_would_fix_it="Name the scheduler.")],
        standout="Clear pipeline work.", would_interview="maybe",
    )


@pytest.fixture
def stubbed(monkeypatch):
    """Writer, verifier and judge replaced; each test steers the probe itself."""
    calls = {"write": 0, "revise": 0, "probe": 0, "answers": []}

    def write(*_a, **_k):
        calls["write"] += 1
        return _app()

    def revise(job, base, personal, draft, objections, **_k):
        calls["revise"] += 1
        calls.setdefault("objections_seen", []).append(list(objections))
        return _app(f"revision {calls['revise']}")

    monkeypatch.setattr(tailor_loop.writer, "write", write)
    monkeypatch.setattr(tailor_loop.writer, "revise", revise)
    monkeypatch.setattr(tailor_loop.verify, "verify", lambda *_a, **_k: VerifyResult())
    monkeypatch.setattr(tailor_loop.judge_mod, "review",
                        lambda job, app: _verdict(f"req-{calls['revise']}"))
    return calls


def test_pauses_with_questions_when_nobody_can_be_asked(stubbed, monkeypatch):
    """No `ask` callback means the caller has to collect the answers, so stop."""
    monkeypatch.setattr(
        tailor_loop, "_probe",
        lambda *_a: [Question(id="q1", requirement="Airflow",
                              question="Which scheduler ran it?", why_it_matters="Names the tool.")],
    )
    result = tailor_loop.run(JOB, base=object(), personal={}, max_rounds=3, probe=True)

    assert result.awaiting_answers is True
    assert [q.id for q in result.questions] == ["q1"]
    assert len(result.rounds) == 1, "the run stops at the question, mid-loop"
    assert result.application is not None, "the draft written so far is still handed back"
    assert result.pending_objections, "the objections behind the questions stay open"


def test_answers_become_facts_and_the_run_carries_on(stubbed, monkeypatch):
    """With an `ask` callback the loop collects answers itself and keeps going."""
    monkeypatch.setattr(
        tailor_loop, "_probe",
        lambda *_a: [Question(id="q1", requirement="Airflow",
                              question="Which scheduler ran it?", why_it_matters="Names the tool.")],
    )
    monkeypatch.setattr(tailor_loop.interview_mod, "answers_to_facts",
                        lambda questions, answers, base: ["f9"])

    result = tailor_loop.run(JOB, base=object(), personal={}, max_rounds=2,
                             probe=True, ask=lambda qs: {"q1": "Databricks Workflows."})

    assert result.awaiting_answers is False
    assert result.facts_added == ["f9"]
    assert len(result.rounds) == 2, "the answer is spent on a further rewrite"


def test_no_probe_on_the_final_round(stubbed, monkeypatch):
    """Answers with no rewrite left to use them are not worth asking for."""
    monkeypatch.setattr(tailor_loop, "_probe",
                        lambda *_a: pytest.fail("probed on the last round"))
    result = tailor_loop.run(JOB, base=object(), personal={}, max_rounds=1, probe=True)

    assert result.awaiting_answers is False
    assert len(result.rounds) == 1


def test_probe_off_by_default(stubbed, monkeypatch):
    monkeypatch.setattr(tailor_loop, "_probe",
                        lambda *_a: pytest.fail("probed without being asked to"))
    result = tailor_loop.run(JOB, base=object(), personal={}, max_rounds=2)
    assert len(result.rounds) == 2


def test_resuming_after_answers_does_not_re_raise_what_was_answered(stubbed, monkeypatch):
    """A continuation starts from the paused draft, carrying the seen objections."""
    monkeypatch.setattr(
        tailor_loop, "_probe",
        lambda *_a: [Question(id="q1", requirement="Airflow",
                              question="Which scheduler ran it?", why_it_matters="Names the tool.")],
    )
    # The judge says the same thing before and after the pause, so a continuation
    # that forgot what it had seen would re-raise it and run a pointless round.
    monkeypatch.setattr(tailor_loop.judge_mod, "review", lambda job, app: _verdict())
    paused = tailor_loop.run(JOB, base=object(), personal={}, max_rounds=3, probe=True)
    assert paused.awaiting_answers
    resumed = tailor_loop.run(
        JOB, base=object(), personal={}, max_rounds=2,
        resume=tailor_loop.Continuation(
            application=paused.application,
            pending_objections=paused.pending_objections,
            seen=paused.seen_keys, structural=paused.structural_map,
            round_offset=paused.rounds[-1].number,
        ),
    )
    assert resumed.rounds[0].number == 2, "numbering continues rather than restarting"
    assert resumed.rounds[0].new_objections == []
    assert "nothing new" in resumed.stopped_because


def test_style_flags_are_not_put_to_the_candidate(monkeypatch):
    """A line that reads as machine-written is the writer's problem, not yours."""
    monkeypatch.setattr(tailor_loop.interview_mod, "probe_objections",
                        lambda *_a: pytest.fail("asked the candidate about a style flag"))
    assert tailor_loop._probe(JOB, object(), ['[style] This line reads as machine-written: "x"']) == []
