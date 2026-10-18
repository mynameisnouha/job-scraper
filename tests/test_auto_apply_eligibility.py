import pytest

from apply import eligibility

FILLED = {"salary_expectation": "70000"}
NO_CV = "No CV yet: upload one on the Set up page"


@pytest.fixture(autouse=True)
def _cv(monkeypatch):
    monkeypatch.setattr(eligibility, "choose_cv", lambda title: "cv/general.pdf")
    monkeypatch.setattr(eligibility, "unfilled_answers", lambda a: a.get("_unfilled", []))


def base(**over):
    job = {"job_id": "j1", "job_title": "Data Engineer", "resume_score": 51,
           "apply_url": "https://jobs.lever.co/acme/1", "ats_type": "lever",
           "status": "new", "dismissed_at": None, "auto_apply_status": None}
    job.update(over)
    return job


CASES = [
    ("base", {}, {}, True, True, None),
    ("migration", {"auto_apply_missing": True}, {}, True, False,
     "Run supabase_setup/add_auto_apply.sql first"),
    # Any job the owner picks: score, ATS and a known employer form are no longer gates.
    ("low_score", {"resume_score": 12}, {}, True, True, None),
    ("no_score", {"resume_score": None}, {}, True, True, None),
    ("posting_only", {"apply_url": None, "ats_type": None,
                      "job_url": "https://www.stepstone.de/stellenangebote--x-123-inline.html"},
     {}, True, True, None),
    ("no_ats", {"ats_type": ""}, {}, True, True, None),
    ("icims", {"ats_type": "icims"}, {}, True, True, None),
    ("no_link_at_all", {"apply_url": None, "job_url": None}, {}, True, False,
     "No link to apply from"),
    ("status", {"status": "applied"}, {}, True, False, "Already applied or skipped"),
    ("dismissed", {"dismissed_at": "2026-10-01"}, {}, True, False, "Already applied or skipped"),
    ("queued", {"auto_apply_status": "queued"}, {}, True, False, "Already queued or running"),
    ("running", {"auto_apply_status": "running"}, {}, True, False, "Already queued or running"),
    ("applied", {"auto_apply_status": "applied"}, {}, True, False, "Already handled: applied"),
    ("dead", {"auto_apply_status": "dead"}, {}, True, False, "Already handled: dead"),
    ("email", {"auto_apply_status": "email"}, {}, True, False, "Already handled: email"),
    ("unknown_state", {"auto_apply_status": "weird"}, {}, True, False, "Already handled: weird"),
    ("failed_requeue", {"auto_apply_status": "failed"}, {}, True, True, None),
    ("verify_requeue", {"auto_apply_status": "verify"}, {}, True, True, None),
    ("placeholder", {}, {"_unfilled": ["Salary", "Notice"]}, True, False,
     "Fill in application_answers.json: Salary"),
    ("no_cv_file", {}, {}, False, False,
     NO_CV),
]


@pytest.mark.parametrize("name,job_over,answers_over,cv_ok,ok,reason", CASES,
                         ids=[c[0] for c in CASES])
def test_check(name, job_over, answers_over, cv_ok, ok, reason):
    answers = {**FILLED, **answers_over}
    assert eligibility.check(base(**job_over), answers, lambda p: cv_ok) == (ok, reason)


def test_no_cv_route(monkeypatch):
    monkeypatch.setattr(eligibility, "choose_cv", lambda title: None)
    assert eligibility.check(base(), FILLED, lambda p: True) == (
        False, NO_CV)


def test_uploaded_cv_is_used_when_no_route_matches(monkeypatch):
    monkeypatch.setattr(eligibility, "choose_cv", lambda title: None)
    monkeypatch.setattr(eligibility.documents, "generic_cv", lambda: "cv/generic_cv.pdf")
    assert eligibility.check(base(), FILLED, lambda p: p == "cv/generic_cv.pdf") == (True, None)


def test_real_unfilled_answers(monkeypatch):
    from review.application_pack import unfilled_answers
    monkeypatch.setattr(eligibility, "unfilled_answers", unfilled_answers)
    ok, reason = eligibility.check(base(), {"gehaltsvorstellung": "<e.g. 55.000 EUR>"},
                                   lambda p: True)
    assert not ok
    assert reason.startswith("Fill in application_answers.json: ")


def test_missing_status_key_is_eligible():
    """Queue rows never select `status` (the query filters status='new')."""
    job = {"job_id": "j1", "job_title": "Data Engineer", "company": "Acme", "resume_score": 60,
           "job_url": "https://x/j", "provider": "p", "posted_at": None, "scraped_at": None,
           "score_breakdown": {}, "why_me_pitch": "w", "dismissed_at": None,
           "program_type": None, "apply_url": "https://jobs.lever.co/acme/1",
           "ats_type": "lever", "linkedin_apply_mode": None, "auto_apply_status": None,
           "auto_apply_reason": None, "auto_apply_cost_usd": None}
    assert "status" not in job
    assert eligibility.check(job, FILLED, lambda p: True) == (True, None)
