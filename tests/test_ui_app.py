"""Headless render tests for the Streamlit UI (no Supabase credentials needed).

These catch the errors that only show up when Streamlit actually executes the
script: bad indentation in a callback, a widget key collision, an index error on
a selectbox, a column referenced before it exists.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from review import apply_queue
from review import calibration
from db import supabase_utils

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

UI_APP_PATH = str(Path(__file__).parent.parent / "ui_app.py")


_TODAY = datetime.now(timezone.utc).isoformat()
_OLD = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

FAKE_TODAY_JOBS = [
    {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME", "resume_score": 82,
     "job_url": "https://example.test/j1", "scraped_at": _TODAY,
     "why_me_pitch": "I shipped a fine-tuned LLM to production.",
     "score_breakdown": {
         "recommendation": "apply_now", "one_line_verdict": "Strong fit.",
         "differentiators": ["QLoRA fine-tuning in production"],
         "key_gaps": ["Kubernetes"],
         "fixable_before_applying": [{"gap": "GCP not shown", "fix": "Add a bullet"}],
         "competitive_context": {"p_first_round_interview": {"after_fixes": 0.3}},
         "application_effort_hours": 1.5,
     }},
    # Below the default min score of 70 — filtered out by default.
    {"job_id": "j2", "job_title": "Data Scientist", "company": "Beta", "resume_score": 41,
     "job_url": None, "scraped_at": _TODAY, "score_breakdown": {}},
    # High score but scraped 10 days ago — filtered out by "found today only".
    {"job_id": "j6", "job_title": "Vision Engineer", "company": "Zeta", "resume_score": 90,
     "job_url": None, "scraped_at": _OLD, "score_breakdown": {}},
]

def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# Dates are relative so the fixture doesn't drift into "stale" as time passes —
# with fixed dates these applications would eventually trip the ghost prompt and
# break unrelated tests.
_STAGE_UPDATED = _days_ago(3)

FAKE_APPLIED_JOBS = [
    {"job_id": "j3", "job_title": "AI Engineer", "company": "Gamma", "resume_score": 77,
     "job_url": "https://example.test/j3", "application_date": _days_ago(10),
     "stage_updated_at": _STAGE_UPDATED,
     "application_stage": "interview_1", "rejection_reason": None, "outcome_notes": None},
    # No application_stage yet — the pre-migration / just-applied case.
    {"job_id": "j4", "job_title": "NLP Engineer", "company": "Delta", "resume_score": 60,
     "job_url": None, "application_date": _days_ago(5),
     "application_stage": None, "rejection_reason": None, "outcome_notes": None},
    # Already rejected, with a reason — exercises the reason selectbox index lookup.
    {"job_id": "j5", "job_title": "MLOps Engineer", "company": "Eps", "resume_score": 55,
     "job_url": None, "application_date": _days_ago(20),
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


@pytest.fixture
def list_app(app):
    """The full-list view. Focus mode is the default, so switch it off first."""
    app.run()
    app.checkbox(key="focus_mode").set_value(False)
    return app


class TestJobsToApplyPage:
    def test_renders_without_exception(self, list_app):
        list_app.run()
        assert not list_app.exception

    def test_default_filters_hide_low_score_and_stale_jobs(self, list_app):
        list_app.run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "ML Engineer" in text          # today + score 82
        assert "Data Scientist" not in text   # score 41, below default min of 70
        assert "Vision Engineer" not in text  # score 90 but scraped 10 days ago,
                                              # outside the default 24h window
        # One card survives, carrying its five actions.
        assert sorted(b.key for b in list_app.button) == [
            "apply_j1", "closed_j1", "details_j1", "pack_j1", "skip_j1",
        ]

    def test_widening_the_date_window_reveals_older_jobs(self, list_app):
        list_app.run()
        list_app.selectbox(key="date_window").set_value("30d").run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "Vision Engineer" in text
        assert not list_app.exception

    def test_card_shows_when_the_job_was_found(self, list_app):
        """Under a day old, the clock time is shown alongside the elapsed time."""
        list_app.run()
        captions = " ".join(c.value for c in list_app.caption)
        assert re.search(r"Found \d{2}:\d{2} · (just now|\d+[mh] ago)", captions)

    def test_lowering_min_score_reveals_weaker_jobs(self, list_app):
        list_app.run()
        list_app.slider[0].set_value(0).run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "Data Scientist" in text

    def test_overview_card_shows_only_the_headline(self, list_app):
        """The list stays scannable: verdict and facts, detail stays behind Details."""
        list_app.run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "ML Engineer" in text
        assert "Strong fit." in text
        assert "Lead with" not in text
        assert "QLoRA fine-tuning in production" not in text
        captions = " ".join(c.value for c in list_app.caption)
        assert "Interview odds" in captions and "30%" in captions

    def test_card_shows_the_recommendation_as_a_badge(self, list_app):
        list_app.run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "Apply now" in text

    def test_details_button_opens_the_dialog_with_the_full_breakdown(self, list_app):
        list_app.run()
        list_app.button("details_j1").click().run()
        assert not list_app.exception
        text = " ".join(m.value for m in list_app.markdown)
        assert "Lead with" in text
        assert "QLoRA fine-tuning in production" in text
        assert "They'll push back on" in text
        assert "Kubernetes" in text
        assert "Before applying" in text
        assert "GCP not shown → Add a bullet" in text
        assert "I shipped a fine-tuned LLM to production." in text

    def test_dialog_stays_closed_until_clicked(self, list_app):
        list_app.run()
        assert "Open posting" not in " ".join(m.value for m in list_app.markdown)

    def test_mark_applied_from_inside_the_dialog(self, list_app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "mark_job_applied",
                            lambda jid: calls.append(jid) or True)
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: True)
        list_app.run()
        list_app.button("details_j1").click().run()
        list_app.button("dlg_apply_j1").click().run()
        assert calls == ["j1"]

    def test_dialog_stays_open_across_reruns(self, list_app):
        """
        Regression: opening the dialog inline from the button branch meant any
        widget click inside it reran the script and the dialog vanished — so its
        own Mark applied button could never fire.
        """
        list_app.run()
        list_app.button("details_j1").click().run()
        assert "Open posting" in " ".join(m.value for m in list_app.markdown)
        # An unrelated interaction elsewhere on the page.
        list_app.text_input("search_jobs").set_value("ML").run()
        assert "Open posting" in " ".join(m.value for m in list_app.markdown)

    def test_filtering_a_job_out_closes_its_dialog(self, list_app):
        list_app.run()
        list_app.button("details_j1").click().run()
        list_app.text_input("search_jobs").set_value("zzz-no-match").run()
        assert not list_app.exception
        assert "Open posting" not in " ".join(m.value for m in list_app.markdown)

    def test_details_dialog_survives_an_empty_breakdown(self, list_app, monkeypatch):
        bare = [{"job_id": "b1", "job_title": "Bare Job", "company": "Co",
                 "resume_score": 80, "job_url": None, "scraped_at": _TODAY,
                 "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: bare)
        list_app.run()
        list_app.button("details_b1").click().run()
        assert not list_app.exception

    def test_card_survives_a_breakdown_with_nothing_in_it(self, list_app, monkeypatch):
        """Screened-out jobs carry a 5-key breakdown — the card must still render."""
        bare = [{"job_id": "b1", "job_title": "Bare Job", "company": "Co",
                 "resume_score": 80, "job_url": None, "scraped_at": _TODAY,
                 "score_breakdown": {"overall_score": 80, "screen_only": True}}]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: bare)
        list_app.run()
        assert not list_app.exception
        assert any("Bare Job" in m.value for m in list_app.markdown)

    def test_no_longer_accepting_closes_the_job(self, list_app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "mark_job_closed",
                            lambda jid: calls.append(jid) or True)
        list_app.run()
        list_app.button("closed_j1").click().run()
        assert calls == ["j1"]
        assert not list_app.exception

    def test_closing_does_not_record_an_application(self, list_app, monkeypatch):
        """A closed posting you never applied to must not enter the outcome data."""
        applied = []
        monkeypatch.setattr(supabase_utils, "mark_job_closed", lambda jid: True)
        monkeypatch.setattr(supabase_utils, "mark_job_applied",
                            lambda jid: applied.append(jid) or True)
        list_app.run()
        list_app.button("closed_j1").click().run()
        assert applied == []

    def test_search_narrows_by_title(self, list_app):
        list_app.run()
        list_app.slider[0].set_value(0).run()
        list_app.text_input("search_jobs").set_value("data scien").run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "Data Scientist" in text
        assert "ML Engineer" not in text

    def test_search_matches_company_case_insensitively(self, list_app):
        list_app.run()
        list_app.text_input("search_jobs").set_value("acme").run()
        text = " ".join(m.value for m in list_app.markdown)
        assert "ML Engineer" in text

    def test_search_with_no_hits_shows_hint(self, list_app):
        list_app.run()
        list_app.text_input("search_jobs").set_value("zzzz-no-such-job").run()
        assert not list_app.exception
        assert any("No jobs match these filters" in i.value for i in list_app.info)

    def test_no_jobs_match_filters_state(self, list_app):
        list_app.run()
        list_app.slider[0].set_value(100).run()
        assert not list_app.exception
        assert any("No jobs match these filters" in i.value for i in list_app.info)

    def test_empty_state(self, list_app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: [])
        list_app.run()
        assert not list_app.exception
        assert any("No scored jobs" in i.value for i in list_app.info)



class TestFocusQueue:
    """One job at a time, keyboard-driven — the default view."""

    def test_focus_mode_shows_one_job_with_its_full_breakdown(self, app):
        app.run()
        assert not app.exception
        text = " ".join(m.value for m in app.markdown)
        # Only the top-ranked job, but everything about it — no Details round trip.
        assert "ML Engineer" in text
        assert "Vision Engineer" not in text
        assert "Lead with" in text
        assert "QLoRA fine-tuning in production" in text
        assert "I shipped a fine-tuned LLM to production." in text

    def test_position_is_shown_so_you_know_where_you_are(self, app):
        app.run()
        captions = " ".join(c.value for c in app.caption)
        assert "**1 of 1** in the queue" in captions

    def test_every_shortcut_has_a_control_labelled_with_its_key(self, app):
        """
        The label is the binding: the browser-side listener clicks whatever
        control starts with "[x]". A shortcut with no matching label is dead.
        """
        app.run()
        labels = [b.label for b in app.button] + [b.label for b in app.get("link_button")]
        for key, _ in apply_queue.SHORTCUTS:
            assert any(label.startswith(f"[{key}]") for label in labels), key

    def test_apply_marks_the_focused_job(self, app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "mark_job_applied",
                            lambda jid: calls.append(jid) or True)
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: True)
        app.run()
        app.button("focus_apply_j1").click().run()
        assert calls == ["j1"]

    def test_skip_dismisses_with_the_selected_reason(self, app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "dismiss_job",
                            lambda jid, reason=None: calls.append((jid, reason)) or True)
        app.run()
        app.selectbox("skip_reason").set_value("german_level").run()
        app.button("focus_skip_j1").click().run()
        assert calls == [("j1", "german_level")]
        assert not app.exception

    def test_skip_defaults_to_a_reason_rather_than_recording_none(self, app, monkeypatch):
        """A skip with no label teaches nothing later, so there is always one."""
        calls = []
        monkeypatch.setattr(supabase_utils, "dismiss_job",
                            lambda jid, reason=None: calls.append((jid, reason)) or True)
        app.run()
        app.button("focus_skip_j1").click().run()
        assert calls == [("j1", "not_interested")]

    def test_a_failed_skip_says_so_instead_of_silently_dropping_it(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "dismiss_job", lambda jid, reason=None: False)
        app.run()
        app.button("focus_skip_j1").click().run()
        assert any("add_dismissal.sql" in e.value for e in app.error)

    def test_a_skip_can_be_undone(self, app, monkeypatch):
        restored = []
        monkeypatch.setattr(supabase_utils, "dismiss_job", lambda jid, reason=None: True)
        monkeypatch.setattr(supabase_utils, "undismiss_job",
                            lambda jid: restored.append(jid) or True)
        app.run()
        app.button("focus_skip_j1").click().run()
        app.button("undo_skip").click().run()
        assert restored == ["j1"]

    def test_navigation_moves_through_the_queue_in_rank_order(self, app):
        app.run()
        app.checkbox(key="focus_mode")  # stays on
        app.slider[0].set_value(0).run()          # let the weaker jobs in
        app.selectbox(key="date_window").set_value("30d").run()  # and the older ones
        assert "Vision Engineer" in " ".join(m.value for m in app.markdown)  # score 90, first
        app.button("focus_next").click().run()
        assert "ML Engineer" in " ".join(m.value for m in app.markdown)      # score 82
        app.button("focus_prev").click().run()
        assert "Vision Engineer" in " ".join(m.value for m in app.markdown)

    def test_least_effort_sort_changes_which_job_is_first(self, app, monkeypatch):
        jobs = [
            {"job_id": "big", "job_title": "Big Job", "company": "Co", "resume_score": 95,
             "job_url": None, "scraped_at": _TODAY,
             "score_breakdown": {"application_effort_hours": 6}},
            {"job_id": "small", "job_title": "Small Job", "company": "Co", "resume_score": 75,
             "job_url": None, "scraped_at": _TODAY,
             "score_breakdown": {"application_effort_hours": 0.5}},
        ]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: jobs)
        app.run()
        assert "Big Job" in " ".join(m.value for m in app.markdown)
        app.radio("sort_mode").set_value("effort").run()
        assert "Small Job" in " ".join(m.value for m in app.markdown)

    def test_the_cursor_follows_the_job_when_the_queue_shifts(self, app, monkeypatch):
        """
        Regression: a bare index would leave you looking at a different job after
        a scrape run inserted a higher-scoring one above the one you were on.
        """
        first = [
            {"job_id": "a", "job_title": "Alpha", "company": "Co", "resume_score": 90,
             "job_url": None, "scraped_at": _TODAY, "score_breakdown": {}},
            {"job_id": "b", "job_title": "Beta", "company": "Co", "resume_score": 80,
             "job_url": None, "scraped_at": _TODAY, "score_breakdown": {}},
        ]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: first)
        app.run()
        app.button("focus_next").click().run()
        assert "Beta" in " ".join(m.value for m in app.markdown)

        newcomer = {"job_id": "new", "job_title": "Newcomer", "company": "Co",
                    "resume_score": 99, "job_url": None, "scraped_at": _TODAY,
                    "score_breakdown": {}}
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                            lambda limit: [newcomer] + first)
        app.run()
        text = " ".join(m.value for m in app.markdown)
        assert "Beta" in text and "Newcomer" not in text

    def test_a_job_with_no_url_disables_open_instead_of_breaking(self, app, monkeypatch):
        bare = [{"job_id": "b1", "job_title": "Bare Job", "company": "Co",
                 "resume_score": 80, "job_url": None, "scraped_at": _TODAY,
                 "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: bare)
        app.run()
        assert not app.exception
        assert app.button("focus_open_b1").disabled

    def test_skipping_from_the_list_view_also_works(self, list_app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "dismiss_job",
                            lambda jid, reason=None: calls.append((jid, reason)) or True)
        list_app.run()
        list_app.button("skip_j1").click().run()
        assert calls == [("j1", "not_interested")]


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

    def test_save_confirmation_survives_the_rerun(self, app, monkeypatch):
        """
        Regression: the confirmation used to be written before st.rerun(), which
        threw it away before the browser painted it — saving looked like a no-op.
        """
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: True)
        self._open(app)
        app.selectbox("stage_j3").set_value("offer").run()
        app.button("save_j3").click().run()
        toasts = [t.value for t in app.toast]
        assert any("Saved" in t and "Offer" in t for t in toasts), toasts

    def test_toast_clears_on_the_next_interaction(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: True)
        self._open(app)
        app.button("save_j3").click().run()
        assert any("Saved" in t.value for t in app.toast)
        app.text_input("search_applied").set_value("").run()
        assert not any("Saved" in t.value for t in app.toast)

    def test_stage_timestamp_shown_as_durable_evidence(self, app):
        """The toast vanishes on refresh; stage_updated_at is what persists."""
        self._open(app)
        captions = " ".join(c.value for c in app.caption)
        assert f"updated {_STAGE_UPDATED[:10]}" in captions

    def test_missing_timestamp_is_omitted_not_rendered_as_dash(self, app):
        self._open(app)
        captions = " ".join(c.value for c in app.caption)
        assert "updated —" not in captions

    def test_failed_save_shows_an_error_not_a_confirmation(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: False)
        self._open(app)
        app.button("save_j3").click().run()
        assert any("Failed to save" in e.value for e in app.error)
        assert not any("Saved" in t.value for t in app.toast)

    def test_stats_row_summarizes_every_application(self, app):
        self._open(app)
        values = {m.label: m.value for m in app.metric}
        assert values["Applied"] == "3"
        assert values["Awaiting reply"] == "1"   # j4 has no stage yet
        assert values["Interviews"] == "1"       # j3
        assert values["Rejected"] == "1"         # j5
        assert values["Offers"] == "0"

    def test_no_ghost_prompt_when_nothing_is_stale(self, app):
        self._open(app)
        assert not any("no reply for" in w.value for w in app.warning)

    def test_ghost_prompt_lists_silent_applications(self, app, monkeypatch):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        jobs = [{"job_id": "old1", "job_title": "Silent Role", "company": "Quiet Co",
                 "resume_score": 70, "application_date": old, "stage_updated_at": old,
                 "application_stage": "applied", "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: jobs)
        self._open(app)
        assert any("no reply for" in w.value for w in app.warning)
        assert any("Silent Role" in m.value for m in app.markdown)

    def test_ghosting_all_is_suggested_not_automatic(self, app, monkeypatch):
        """Nothing is written until the button is pressed — a late reply is possible."""
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        jobs = [{"job_id": "old1", "job_title": "Silent Role", "company": "Quiet Co",
                 "resume_score": 70, "application_date": old, "stage_updated_at": old,
                 "application_stage": "applied", "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: jobs)
        calls = []
        monkeypatch.setattr(supabase_utils, "update_application_stage",
                            lambda *a, **k: calls.append(a) or True)
        self._open(app)
        assert calls == []
        app.button("ghost_all").click().run()
        assert calls == [("old1", "ghosted")]

    def test_hiding_resolved_only_affects_the_view(self, app):
        self._open(app)
        assert any("AI Engineer" in m.value for m in app.markdown)   # interview_1
        app.checkbox[0].set_value(False).run()
        text = " ".join(m.value for m in app.markdown)
        assert "AI Engineer" not in text     # resolved, hidden
        assert "NLP Engineer" in text        # still pending, kept
        # Totals are unchanged — nothing was deleted.
        assert {m.label: m.value for m in app.metric}["Applied"] == "3"

    def test_stats_ignore_the_search_filter(self, app):
        """Totals must describe the whole pipeline, not the current search."""
        self._open(app)
        app.text_input("search_applied").set_value("gamma").run()
        values = {m.label: m.value for m in app.metric}
        assert values["Applied"] == "3"

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

    def test_spam_postings_reported_as_excluded(self, app, monkeypatch):
        jobs = _resolved(1, stage="interview_1") + _resolved(1, stage="spam_or_removed")
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: jobs)
        self._open(app)
        values = {m.label: m.value for m in app.metric}
        assert values["Applied"] == "2"
        assert values["Resolved"] == "1"
        assert values["Awaiting reply"] == "0"
        assert values["Interview rate"] == "100%"   # spam must not dilute this
        assert any("excluded as removed/spam" in c.value for c in app.caption)

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
