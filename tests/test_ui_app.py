"""Headless render tests for the Streamlit UI (no Supabase credentials needed).

These catch the errors that only show up when Streamlit actually executes the
script: bad indentation in a callback, a widget key collision, an index error on
a selectbox, a column referenced before it exists.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import calibration
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

    def test_search_narrows_by_title(self, app):
        app.run()
        app.slider[0].set_value(0).run()
        app.text_input("search_jobs").set_value("data scien").run()
        text = " ".join(m.value for m in app.markdown)
        assert "Data Scientist" in text
        assert "ML Engineer" not in text

    def test_search_matches_company_case_insensitively(self, app):
        app.run()
        app.text_input("search_jobs").set_value("acme").run()
        text = " ".join(m.value for m in app.markdown)
        assert "ML Engineer" in text

    def test_search_with_no_hits_shows_hint(self, app):
        app.run()
        app.text_input("search_jobs").set_value("zzzz-no-such-job").run()
        assert not app.exception
        assert any("No jobs match these filters" in i.value for i in app.info)

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

    def test_search_narrows_applications(self, app):
        self._open(app)
        app.text_input("search_applied").set_value("gamma").run()
        text = " ".join(m.value for m in app.markdown)
        assert "AI Engineer" in text
        assert "NLP Engineer" not in text

    def test_search_with_no_hits_shows_hint(self, app):
        self._open(app)
        app.text_input("search_applied").set_value("zzzz-no-such-job").run()
        assert not app.exception
        assert any("No applications match that search" in i.value for i in app.info)

    def test_empty_state(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: [])
        self._open(app)
        assert not app.exception
        assert any("No applied jobs" in i.value for i in app.info)


def _resolved(n, stage="rejected", score=80, p=None):
    out = []
    for i in range(n):
        breakdown = {"competitive_context": {"p_first_round_interview": {"after_fixes": p}}} if p else {}
        out.append({"job_id": f"r{i}", "job_title": f"Role {i}", "company": "Co",
                    "resume_score": score, "application_stage": stage,
                    "application_date": _TODAY, "stage_updated_at": _TODAY,
                    "rejection_reason": "german_level", "outcome_notes": None,
                    "score_breakdown": breakdown})
    return out


class TestCalibrationPage:
    def _open(self, app):
        app.run()
        app.sidebar.radio[0].set_value("Calibration").run()
        assert not app.exception
        return app

    def test_renders_with_the_default_fixture(self, app):
        self._open(app)
        assert not app.exception

    def test_empty_state(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: [])
        self._open(app)
        assert any("No applied jobs yet" in i.value for i in app.info)

    def test_warns_when_below_threshold(self, app):
        """Small samples must be labelled as noise, not presented as signal."""
        self._open(app)
        assert any("Not enough resolved outcomes" in w.value for w in app.warning)

    def test_no_warning_once_threshold_met(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                            lambda limit=999: _resolved(calibration.MIN_RESOLVED_FOR_METRICS))
        self._open(app)
        assert not any("Not enough resolved outcomes" in w.value for w in app.warning)

    def test_pending_applications_excluded_from_rate(self, app, monkeypatch):
        jobs = _resolved(1, stage="interview_1") + _resolved(1, stage="rejected")
        jobs += [{"job_id": "p1", "job_title": "Pending", "company": "Co", "resume_score": 80,
                  "application_stage": "applied", "application_date": _TODAY,
                  "stage_updated_at": _TODAY, "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: jobs)
        self._open(app)
        values = {m.label: m.value for m in app.metric}
        assert values["Applied"] == "3"
        assert values["Awaiting reply"] == "1"
        assert values["Resolved"] == "2"
        assert values["Interview rate"] == "50%"

    def test_overconfidence_is_called_out(self, app, monkeypatch):
        # Scorer predicted 80%, every application was rejected.
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                            lambda limit=999: _resolved(20, stage="rejected", p=0.8))
        self._open(app)
        text = " ".join(c.value for c in app.caption)
        assert "overconfident" in text

    def test_no_predictions_available(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                            lambda limit=999: _resolved(20, stage="rejected", p=None))
        self._open(app)
        assert any("nothing to calibrate against" in i.value for i in app.info)
