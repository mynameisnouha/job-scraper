import pytest

from apply import eligibility

FILLED = {"salary_expectation": "70000"}


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
    ("score50", {"resume_score": 50}, {}, True, False, "Score must be above 50"),
    ("no_score", {"resume_score": None}, {}, True, False, "Score must be above 50"),
    ("no_url", {"apply_url": None}, {}, True, False, "No employer application form known"),
    ("no_ats", {"ats_type": ""}, {}, True, False, "No employer application form known"),
    ("icims", {"ats_type": "icims"}, {}, True, False, "iCIMS is applied to by hand"),
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
     "No CV for this title: set CV_DEFAULT or CV_KEYWORD_ROUTES"),
]


@pytest.mark.parametrize("name,job_over,answers_over,cv_ok,ok,reason", CASES,
                         ids=[c[0] for c in CASES])
def test_check(name, job_over, answers_over, cv_ok, ok, reason):
    answers = {**FILLED, **answers_over}
    assert eligibility.check(base(**job_over), answers, lambda p: cv_ok) == (ok, reason)


def test_no_cv_route(monkeypatch):
    monkeypatch.setattr(eligibility, "choose_cv", lambda title: None)
    assert eligibility.check(base(), FILLED, lambda p: True) == (
        False, "No CV for this title: set CV_DEFAULT or CV_KEYWORD_ROUTES")


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
