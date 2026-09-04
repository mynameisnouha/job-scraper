"""Headless render tests for the Streamlit UI (no Supabase credentials needed).

These catch the errors that only show up when Streamlit actually executes the
script: bad indentation in a callback, a widget key collision, an index error on
a selectbox, a column referenced before it exists.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import supabase_utils

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

UI_APP_PATH = str(Path(__file__).parent.parent / "ui_app.py")


_TODAY = datetime.now(timezone.utc).isoformat()
_OLD = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

FAKE_TODAY_JOBS = [
    {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME", "resume_score": 82,
     "job_url": "https://example.test/j1", "scraped_at": _TODAY,
     "score_breakdown": {"recommendation": "apply_now", "one_line_verdict": "Strong fit."}},
    # Below the default min score of 70 — filtered out by default.
    {"job_id": "j2", "job_title": "Data Scientist", "company": "Beta", "resume_score": 41,
     "job_url": None, "scraped_at": _TODAY, "score_breakdown": {}},
    # High score but scraped 10 days ago — filtered out by "found today only".
    {"job_id": "j6", "job_title": "Vision Engineer", "company": "Zeta", "resume_score": 90,
     "job_url": None, "scraped_at": _OLD, "score_breakdown": {}},
]

FAKE_APPLIED_JOBS = [
    {"job_id": "j3", "job_title": "AI Engineer", "company": "Gamma", "resume_score": 77,
     "job_url": "https://example.test/j3", "application_date": "2026-08-01T10:00:00Z",
     "application_stage": "interview_1", "rejection_reason": None, "outcome_notes": None},
    # No application_stage yet — the pre-migration / just-applied case.
    {"job_id": "j4", "job_title": "NLP Engineer", "company": "Delta", "resume_score": 60,
     "job_url": None, "application_date": "2026-07-15T10:00:00Z",
     "application_stage": None, "rejection_reason": None, "outcome_notes": None},
    # Already rejected, with a reason — exercises the reason selectbox index lookup.
    {"job_id": "j5", "job_title": "MLOps Engineer", "company": "Eps", "resume_score": 55,
     "job_url": None, "application_date": "2026-07-01T10:00:00Z",
     "application_stage": "rejected", "rejection_reason": "german_level",
     "outcome_notes": "Needed C1."},
]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                        lambda limit: list(FAKE_TODAY_JOBS))
    monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                        lambda limit=999: list(FAKE_APPLIED_JOBS))
    return AppTest.from_file(UI_APP_PATH, default_timeout=60)


class TestJobsToApplyPage:
    def test_renders_without_exception(self, app):
        app.run()
        assert not app.exception

    def test_default_filters_hide_low_score_and_stale_jobs(self, app):
        app.run()
        text = " ".join(m.value for m in app.markdown)
        assert "ML Engineer" in text          # today + score 82
        assert "Data Scientist" not in text   # score 41, below default min of 70
        assert "Vision Engineer" not in text  # score 90 but scraped 10 days ago
        assert len(app.button) == 1

    def test_unchecking_today_only_reveals_older_jobs(self, app):
        app.run()
        app.checkbox[0].set_value(False).run()
        text = " ".join(m.value for m in app.markdown)
        assert "Vision Engineer" in text
        assert not app.exception

    def test_lowering_min_score_reveals_weaker_jobs(self, app):
        app.run()
        app.slider[0].set_value(0).run()
        text = " ".join(m.value for m in app.markdown)
        assert "Data Scientist" in text

    def test_no_jobs_match_filters_state(self, app):
        app.run()
        app.slider[0].set_value(100).run()
        assert not app.exception
        assert any("No jobs match these filters" in i.value for i in app.info)

    def test_empty_state(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: [])
        app.run()
        assert not app.exception
        assert any("No scored jobs" in i.value for i in app.info)


class TestApplicationsPage:
    def _open(self, app):
        app.run()
        app.sidebar.radio[0].set_value("Applications").run()
        assert not app.exception
        return app

    def test_renders_without_exception(self, app):
        self._open(app)
        assert not app.exception

    def test_stage_dropdown_reflects_current_stage(self, app):
        self._open(app)
        stages = [s.value for s in app.selectbox if s.key and s.key.startswith("stage_")]
        # j3 is interview_1; j4 has no stage so defaults to applied; j5 is rejected.
        assert stages == ["interview_1", "applied", "rejected"]

    def test_rejected_job_shows_its_reason(self, app):
        self._open(app)
        reasons = [s.value for s in app.selectbox if s.key and s.key.startswith("reason_")]
        assert reasons == ["german_level"]

    def test_notes_field_prefilled(self, app):
        self._open(app)
        notes = {t.key: t.value for t in app.text_input}
        assert notes["notes_j5"] == "Needed C1."
        assert notes["notes_j3"] == ""

    def test_saving_a_stage_calls_supabase(self, app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "update_application_stage",
                            lambda *a, **k: calls.append((a, k)) or True)
        self._open(app)
        app.selectbox("stage_j3").set_value("offer").run()
        app.button("save_j3").click().run()
        assert not app.exception
        assert calls and calls[0][0] == ("j3", "offer")

    def test_empty_state(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: [])
        self._open(app)
        assert not app.exception
        assert any("No applied jobs" in i.value for i in app.info)
