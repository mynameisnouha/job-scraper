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
from review import weekly
from db import supabase_utils
import config
from onboarding import status as onboarding_status

st = pytest.importorskip("streamlit")
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
    # A weak score. Visible by default now: the queue no longer opens filtered
    # to 70+, because hiding a 64 hides the decision rather than making it.
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


def markdown_text(app):
    """Everything rendered through st.markdown — the HTML cards included."""
    return " ".join(m.value for m in app.markdown)


def tiles(app):
    """label → value for every stat tile on the page."""
    found = {}
    for m in app.markdown:
        for label, value in re.findall(
                r'text-transform:uppercase;color:#7f7c97;margin-bottom:7px">([^<]+)</div>'
                r'<div style="font-family:[^"]*;font-size:34px;line-height:1">([^<]+)</div>',
                m.value):
            found[label] = value
    return found


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                        lambda limit: list(FAKE_TODAY_JOBS))
    monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                        lambda limit=999: list(FAKE_APPLIED_JOBS))
    # Sidebar and calibration reads that would otherwise go to the network.
    monkeypatch.setattr(supabase_utils, "get_scrape_pulse", lambda **kw: None)
    monkeypatch.setattr(supabase_utils, "attach_apply_links", lambda jobs: jobs)
    monkeypatch.setattr(supabase_utils, "get_skip_reason_counts", lambda: {})
    # The app opens on Set up when there is no valid profile. These tests are
    # about the other pages, so they run as an already-onboarded user.
    monkeypatch.setattr(onboarding_status, "profile_present", lambda *a, **k: True)
    # The app's reads go through st.cache_data, which lives for the whole
    # process: without this, one test's fake rows are served to the next.
    st.cache_data.clear()
    # Never read or overwrite the real weekly-tracking preferences.
    monkeypatch.setattr(weekly, "SETTINGS_PATH", str(tmp_path / "weekly_settings.json"))
    return AppTest.from_file(UI_APP_PATH, default_timeout=60)


@pytest.fixture
def list_app(app):
    """The full-list view. One at a time is the default, so switch it off first."""
    app.run()
    app.radio(key="queue_view").set_value("list")
    return app


class TestJobsToApplyPage:
    def test_renders_without_exception(self, list_app):
        list_app.run()
        assert not list_app.exception

    def test_every_scored_job_found_today_is_shown(self, list_app):
        """No score bar by default. Recency still filters, because it decays.

        The score filter used to start at 70 and the queue opened already
        hiding things. A 64 is inside the scorer's own noise, so that hid the
        decision instead of making it.
        """
        list_app.run()
        text = markdown_text(list_app)
        assert "ML Engineer" in text          # today + score 82
        assert "Data Scientist" in text       # today + score 41, no longer hidden
        assert "Vision Engineer" not in text  # score 90 but scraped 10 days ago,
                                              # outside the default 24h window
        # Both cards carry their actions: decide, or reach for the overflow.
        keys = {b.key for b in list_app.button}
        assert {"apply_j1", "skip_j1", "details_j1", "list_pack_j1",
                "list_tailor_j1", "list_closed_j1"} <= keys
        assert {"apply_j2", "skip_j2"} <= keys
        assert not any(k.endswith("_j6") for k in keys)

    def test_the_strip_says_what_the_filters_hide(self, list_app):
        """A short queue must read as filtered, not as an empty market."""
        list_app.run()
        labels = [b.label for b in list_app.button if b.key.startswith("drop_")]
        assert any("Older than 24 hours" in l and "1" in l for l in labels)
        # No score chip: nothing is hidden by score until the slider is raised.
        assert not any("Score under" in l for l in labels)

    def test_raising_the_bar_hides_weak_jobs_and_says_so(self, list_app):
        """The filter still works — it just is not applied for you."""
        list_app.run()
        list_app.slider[0].set_value(70).run()
        text = markdown_text(list_app)
        assert "ML Engineer" in text
        assert "Data Scientist" not in text
        labels = [b.label for b in list_app.button if b.key.startswith("drop_")]
        assert any("Score under 70" in l and "1" in l for l in labels)

    def test_dropping_a_chip_opens_that_filter(self, list_app):
        list_app.run()
        list_app.slider[0].set_value(70).run()
        list_app.button("drop_min_score").click().run()
        assert not list_app.exception
        assert "Data Scientist" in markdown_text(list_app)
        assert not any(b.key == "drop_min_score" for b in list_app.button)

    def test_filters_survive_a_trip_to_another_page(self, list_app):
        """Streamlit drops a widget's state on any run that does not draw it.

        Set the German filter, open another page, come back: every filter was
        back at its default — the 24-hour window "added itself" and the German
        filter was "any" again. Reproduced before the fix as none/30d → any/24h.
        """
        list_app.selectbox(key="german_max").set_value("none").run()
        list_app.selectbox(key="date_window").set_value("30d").run()
        list_app.button("nav_apps").click().run()
        list_app.button("nav_queue").click().run()
        assert list_app.session_state["german_max"] == "none"
        assert list_app.session_state["date_window"] == "30d"
        assert "Vision Engineer" in markdown_text(list_app), "30d window still applied"

    def test_a_failed_apply_says_so_where_it_cannot_be_missed(self, list_app, monkeypatch):
        """The write fails; the job stays; the page must say why, at the top.

        It used to be an st.error inside the button's own column, wiped by the
        next click — which read as "it refreshed and the job is still there".
        """
        monkeypatch.setattr(supabase_utils, "mark_job_applied", lambda job_id: False)
        list_app.run()
        list_app.button("apply_j1").click().run()
        assert not list_app.exception
        assert any("Could not mark" in e.value and "still in the queue" in e.value
                   for e in list_app.error)
        assert "ML Engineer" in markdown_text(list_app), "nothing pretends it left"

    def test_delete_with_a_typed_note_reaches_the_database(self, app, monkeypatch):
        """Note and reason go together, as one form submission.

        A text field beside plain buttons loses the click: the field commits on
        blur, that reruns the script, and the click that lands during the rerun
        is dropped. Typing a note and then choosing a reason did nothing.
        """
        calls = []
        monkeypatch.setattr(supabase_utils, "delete_job",
                            lambda job, reason, note=None: calls.append((job["job_id"], reason, note)) or True)
        app.session_state["queue_view"] = "focus"
        app.run()
        app.button("focus_delete_j1").click().run()
        app.text_input(key="delnote_j1").input("same as the LinkedIn one").run()
        app.button("delreason_j1_duplicate_posting").click().run()
        assert not app.exception
        assert calls == [("j1", "duplicate_posting", "same as the LinkedIn one")]

    def test_widening_the_date_window_reveals_older_jobs(self, list_app):
        list_app.run()
        list_app.selectbox(key="date_window").set_value("30d").run()
        assert "Vision Engineer" in markdown_text(list_app)
        assert not list_app.exception

    def test_card_shows_when_the_job_was_found(self, list_app):
        """Under a day old, the clock time is shown alongside the elapsed time."""
        list_app.run()
        assert re.search(r"found \d{2}:\d{2} · (just now|\d+[mh] ago)", markdown_text(list_app))

    def test_lowering_min_score_reveals_weaker_jobs(self, list_app):
        list_app.run()
        list_app.slider[0].set_value(70).run()
        assert "Data Scientist" not in markdown_text(list_app)
        list_app.slider[0].set_value(0).run()
        assert "Data Scientist" in markdown_text(list_app)

    def test_overview_card_shows_only_the_headline(self, list_app):
        """The list stays scannable: verdict and gates, detail stays behind the overflow."""
        list_app.run()
        text = markdown_text(list_app)
        assert "ML Engineer" in text
        assert "Strong fit." in text
        assert "Lead with" not in text
        assert "QLoRA fine-tuning in production" not in text
        assert "30% interview odds" in text
        assert "1.5h" in text

    def test_card_shows_the_scorer_verdict_as_a_chip(self, list_app):
        list_app.run()
        assert "Apply now" in markdown_text(list_app)

    def test_card_shows_the_score_band_and_its_place_in_the_corpus(self, list_app):
        list_app.run()
        text = markdown_text(list_app)
        assert "Strong" in text
        assert "of 3" in text  # three scored postings, ranked against each other

    def test_details_button_opens_the_dialog_with_the_full_breakdown(self, list_app):
        list_app.run()
        list_app.button("details_j1").click().run()
        assert not list_app.exception
        text = markdown_text(list_app)
        assert "Lead with" in text
        assert "QLoRA fine-tuning in production" in text
        assert "They&#x27;ll push back on" in text
        assert "Kubernetes" in text
        assert "Fix before applying" in text
        assert "GCP not shown → Add a bullet" in text
        assert any("I shipped a fine-tuned LLM to production." in c.value for c in list_app.code)

    def test_dialog_stays_closed_until_clicked(self, list_app):
        list_app.run()
        assert "Open posting" not in markdown_text(list_app)

    def test_reads_are_cached_across_reruns_and_dropped_after_a_write(self, app, monkeypatch):
        """Every click reruns the script; it must not re-fetch 999 rows each time.

        A write has to drop the cache, though, or the job you just marked
        applied would sit in the queue until the TTL ran out.
        """
        fetches = []
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                            lambda limit: fetches.append(limit) or list(FAKE_TODAY_JOBS))
        monkeypatch.setattr(supabase_utils, "mark_job_applied", lambda jid: True)
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: True)
        app.run()
        app.run()
        assert len(fetches) == 1, "the second run is served from the cache"
        app.radio(key="queue_view").set_value("list").run()
        app.button("apply_j1").click().run()
        assert len(fetches) == 2, "the write dropped the cache"

    def test_an_empty_or_failed_read_is_not_cached(self, app, monkeypatch):
        """The readers return [] on an outage too; caching that hides recovery."""
        rows = []
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                            lambda limit: list(rows))
        app.run()
        assert any("No scored jobs" in i.value for i in app.info)
        rows.extend(FAKE_TODAY_JOBS)
        app.run()
        assert not any("No scored jobs" in i.value for i in app.info)

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
        assert "Open posting" in markdown_text(list_app)
        # An unrelated interaction elsewhere on the page.
        list_app.text_input("search_jobs").set_value("ML").run()
        assert "Open posting" in markdown_text(list_app)

    def test_filtering_a_job_out_closes_its_dialog(self, list_app):
        list_app.run()
        list_app.button("details_j1").click().run()
        list_app.text_input("search_jobs").set_value("zzz-no-match").run()
        assert not list_app.exception
        assert "Open posting" not in markdown_text(list_app)

    def test_details_dialog_survives_an_empty_breakdown(self, list_app, monkeypatch):
        bare = [{"job_id": "b1", "job_title": "Bare Job", "company": "Co",
                 "resume_score": 80, "job_url": None, "scraped_at": _TODAY,
                 "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: bare)
        st.cache_data.clear()  # the data changed under a running session: the TTL ran out
        list_app.run()
        list_app.button("details_b1").click().run()
        assert not list_app.exception

    def test_card_survives_a_breakdown_with_nothing_in_it(self, list_app, monkeypatch):
        """Screened-out jobs carry a 5-key breakdown — the card must still render."""
        bare = [{"job_id": "b1", "job_title": "Bare Job", "company": "Co",
                 "resume_score": 80, "job_url": None, "scraped_at": _TODAY,
                 "score_breakdown": {"overall_score": 80, "screen_only": True}}]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: bare)
        st.cache_data.clear()  # the data changed under a running session: the TTL ran out
        list_app.run()
        assert not list_app.exception
        assert any("Bare Job" in m.value for m in list_app.markdown)

    def test_no_longer_accepting_closes_the_job(self, list_app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "mark_job_closed",
                            lambda jid: calls.append(jid) or True)
        list_app.run()
        list_app.button("list_closed_j1").click().run()
        assert calls == ["j1"]
        assert not list_app.exception

    def test_closing_does_not_record_an_application(self, list_app, monkeypatch):
        """A closed posting you never applied to must not enter the outcome data."""
        applied = []
        monkeypatch.setattr(supabase_utils, "mark_job_closed", lambda jid: True)
        monkeypatch.setattr(supabase_utils, "mark_job_applied",
                            lambda jid: applied.append(jid) or True)
        list_app.run()
        list_app.button("list_closed_j1").click().run()
        assert applied == []

    def test_search_narrows_by_title(self, list_app):
        list_app.run()
        list_app.slider[0].set_value(0).run()
        list_app.text_input("search_jobs").set_value("data scien").run()
        text = markdown_text(list_app)
        assert "Data Scientist" in text
        assert "ML Engineer" not in text

    def test_search_matches_company_case_insensitively(self, list_app):
        list_app.run()
        list_app.text_input("search_jobs").set_value("acme").run()
        assert "ML Engineer" in markdown_text(list_app)

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
        st.cache_data.clear()  # the data changed under a running session: the TTL ran out
        list_app.run()
        assert not list_app.exception
        assert any("No scored jobs" in i.value for i in list_app.info)



class TestFocusQueue:
    """One job at a time, keyboard-driven. The page opens on the list view."""

    @pytest.fixture(autouse=True)
    def _focus_view(self, app):
        app.session_state["queue_view"] = "focus"

    def test_focus_mode_shows_one_job_with_its_full_breakdown(self, app):
        app.run()
        assert not app.exception
        text = markdown_text(app)
        # Only the top-ranked job, but everything about it — no Details round trip.
        assert "ML Engineer" in text
        assert "Vision Engineer" not in text
        assert "Lead with" in text
        assert "QLoRA fine-tuning in production" in text
        # The pitch sits in the side rail as a code block, so it can be copied.
        assert any("I shipped a fine-tuned LLM to production." in c.value for c in app.code)

    def test_position_is_shown_so_you_know_where_you_are(self, app):
        """Two today: the 82 and the 41, which the old 70 default hid."""
        app.run()
        assert re.search(r">1 of 2<", markdown_text(app))

    def test_every_shortcut_has_a_control_labelled_with_its_key(self, app):
        """
        The label is the binding: the browser-side listener clicks whatever
        control ends in the key's code chip. A shortcut with no matching label is dead.
        """
        app.run()
        labels = [b.label for b in app.button] + [b.label for b in app.get("link_button")]
        for key, _ in apply_queue.SHORTCUTS:
            assert any(label.endswith(f"`{key}`") for label in labels), key

    def test_a_strong_match_suggests_tailoring_without_doing_it(self, app, monkeypatch):
        """The 82 clears the bar: the rail offers the CV, and the click only navigates."""
        from tailor import store as tailor_store
        monkeypatch.setattr(tailor_store, "exists", lambda job_id: False)
        # The CV page fetches the full posting; nothing to fetch offline.
        monkeypatch.setattr(supabase_utils, "get_job_with_description", lambda job_id: None)
        app.run()
        assert "Worth a tailored CV" in markdown_text(app)
        app.button("suggest_tailor_j1").click().run()
        assert not app.exception
        assert app.session_state["page"] == "tailor"

    def test_an_already_tailored_job_says_so_instead(self, app, monkeypatch):
        from tailor import store as tailor_store
        monkeypatch.setattr(tailor_store, "exists", lambda job_id: job_id == "j1")
        app.run()
        text = markdown_text(app)
        assert "Worth a tailored CV" not in text
        assert "Tailored CV ready" in text

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
        app.button("focus_skip_j1").click().run()
        app.button("skipreason_j1_german_level").click().run()
        assert calls == [("j1", "german_level")]
        assert not app.exception

    def test_skip_asks_why_before_recording_anything(self, app, monkeypatch):
        """The reason is captured at the moment of the decision, never defaulted."""
        calls = []
        monkeypatch.setattr(supabase_utils, "dismiss_job",
                            lambda jid, reason=None: calls.append((jid, reason)) or True)
        app.run()
        assert not any(b.key.startswith("skipreason_") for b in app.button)
        app.button("focus_skip_j1").click().run()
        assert calls == []
        reasons = [b.key for b in app.button if b.key.startswith("skipreason_j1_")]
        assert reasons == [f"skipreason_j1_{r}" for r in apply_queue.SKIP_REASONS]
        app.button("skipcancel_j1").click().run()
        assert not any(b.key.startswith("skipreason_") for b in app.button)
        assert calls == []

    def test_a_failed_skip_says_so_instead_of_silently_dropping_it(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "dismiss_job", lambda jid, reason=None: False)
        app.run()
        app.button("focus_skip_j1").click().run()
        app.button("skipreason_j1_not_interested").click().run()
        assert any("add_dismissal.sql" in e.value for e in app.error)

    def test_a_skip_can_be_undone(self, app, monkeypatch):
        restored = []
        monkeypatch.setattr(supabase_utils, "dismiss_job", lambda jid, reason=None: True)
        monkeypatch.setattr(supabase_utils, "undismiss_job",
                            lambda jid: restored.append(jid) or True)
        app.run()
        app.button("focus_skip_j1").click().run()
        app.button("skipreason_j1_not_interested").click().run()
        app.button("undo_skip").click().run()
        assert restored == ["j1"]

    def test_navigation_moves_through_the_queue_in_rank_order(self, app):
        app.run()
        app.slider[0].set_value(0).run()          # let the weaker jobs in
        app.selectbox(key="date_window").set_value("30d").run()  # and the older ones
        assert "Vision Engineer" in markdown_text(app)  # score 90, first
        app.button("focus_next").click().run()
        assert "ML Engineer" in markdown_text(app)      # score 82
        app.button("focus_prev").click().run()
        assert "Vision Engineer" in markdown_text(app)

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
        assert "Big Job" in markdown_text(app)
        app.radio("sort_mode").set_value("effort").run()
        assert "Small Job" in markdown_text(app)

    def test_newest_sort_puts_the_freshest_posting_first(self, app, monkeypatch):
        jobs = [
            {"job_id": "old", "job_title": "Older Job", "company": "Co", "resume_score": 95,
             "job_url": None, "scraped_at": _days_ago(0.5), "score_breakdown": {}},
            {"job_id": "fresh", "job_title": "Fresh Job", "company": "Co", "resume_score": 75,
             "job_url": None, "scraped_at": _TODAY, "score_breakdown": {}},
        ]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: jobs)
        app.run()
        assert "Older Job" in markdown_text(app)
        app.radio("sort_mode").set_value("new").run()
        assert "Fresh Job" in markdown_text(app)

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
        assert "Beta" in markdown_text(app)

        newcomer = {"job_id": "new", "job_title": "Newcomer", "company": "Co",
                    "resume_score": 99, "job_url": None, "scraped_at": _TODAY,
                    "score_breakdown": {}}
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                            lambda limit: [newcomer] + first)
        app.run()
        text = markdown_text(app)
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
        list_app.button("skipreason_j1_location").click().run()
        assert calls == [("j1", "location")]


class TestApplicationsPage:
    def _open(self, app, tab="all"):
        app.run()
        app.button("nav_apps").click().run()
        assert not app.exception
        app.radio("app_tab").set_value(tab).run()
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
        assert f"updated {_STAGE_UPDATED[:10]}" in markdown_text(app)

    def test_missing_timestamp_is_omitted_not_rendered_as_dash(self, app):
        self._open(app)
        assert "updated —" not in markdown_text(app)

    def test_each_record_shows_its_pipeline_as_a_timeline(self, app):
        self._open(app)
        text = markdown_text(app)
        assert "Interview 1" in text and "Interview 2" in text   # j3 is at interview 1
        assert "Rejected" in text                                 # j5

    def test_tabs_split_open_from_resolved(self, app):
        self._open(app, tab="open")
        text = markdown_text(app)
        assert "NLP Engineer" in text          # still waiting
        assert "MLOps Engineer" not in text    # rejected
        self._open(app, tab="resolved")
        text = markdown_text(app)
        assert "MLOps Engineer" in text
        assert "NLP Engineer" not in text

    def test_failed_save_shows_an_error_not_a_confirmation(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "update_application_stage", lambda *a, **k: False)
        self._open(app)
        app.button("save_j3").click().run()
        assert any("Failed to save" in e.value for e in app.error)
        assert not any("Saved" in t.value for t in app.toast)

    def test_stats_row_summarizes_every_application(self, app):
        self._open(app)
        values = tiles(app)
        assert values["Applied"] == "3"
        assert values["Awaiting reply"] == "1"   # j4 has no stage yet
        assert values["Interviews"] == "1"       # j3
        assert values["Offers"] == "0"

    def test_no_ghost_prompt_when_nothing_is_stale(self, app):
        self._open(app)
        assert "gone quiet" not in markdown_text(app)

    def test_ghost_prompt_lists_silent_applications(self, app, monkeypatch):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        jobs = [{"job_id": "old1", "job_title": "Silent Role", "company": "Quiet Co",
                 "resume_score": 70, "application_date": old, "stage_updated_at": old,
                 "application_stage": "applied", "score_breakdown": {}}]
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: jobs)
        self._open(app)
        assert "gone quiet" in markdown_text(app)
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
        assert "AI Engineer" in markdown_text(app)   # interview_1
        app.radio("app_tab").set_value("open").run()
        text = markdown_text(app)
        assert "AI Engineer" not in text     # resolved, hidden
        assert "NLP Engineer" in text        # still pending, kept
        # Totals are unchanged — nothing was deleted.
        assert tiles(app)["Applied"] == "3"

    def test_stats_ignore_the_search_filter(self, app):
        """Totals must describe the whole pipeline, not the current search."""
        self._open(app)
        app.text_input("search_applied").set_value("gamma").run()
        assert tiles(app)["Applied"] == "3"

    def test_search_narrows_applications(self, app):
        self._open(app)
        app.text_input("search_applied").set_value("gamma").run()
        text = markdown_text(app)
        assert "AI Engineer" in text
        assert "NLP Engineer" not in text

    def test_search_with_no_hits_shows_hint(self, app):
        self._open(app)
        app.text_input("search_applied").set_value("zzzz-no-such-job").run()
        assert not app.exception
        assert any("No applications match that search" in i.value for i in app.info)

    def test_empty_state(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: [])
        app.run()
        app.button("nav_apps").click().run()
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
        app.button("nav_cal").click().run()
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
        values = tiles(app)
        assert values["Applied"] == "3"
        assert values["Awaiting reply"] == "1"
        assert values["Resolved"] == "2"
        assert values["Interview rate"] == "50%"

    def test_spam_postings_reported_as_excluded(self, app, monkeypatch):
        jobs = _resolved(1, stage="interview_1") + _resolved(1, stage="spam_or_removed")
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes", lambda limit=999: jobs)
        self._open(app)
        values = tiles(app)
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
        text = markdown_text(app)
        assert "overconfident" in text
        assert "+80pt" in text

    def test_skip_reasons_count_as_blockers_alongside_rejections(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_skip_reason_counts",
                            lambda: {"german_level": 19, "location": 2})
        self._open(app)
        text = markdown_text(app)
        assert "German level too high" in text
        assert "top blocker at 19" in text

    def test_no_predictions_available(self, app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                            lambda limit=999: _resolved(20, stage="rejected", p=None))
        self._open(app)
        assert any("nothing to calibrate against" in i.value for i in app.info)


class TestArchetypesMarketPanel:
    """The German panel on "Which CV to use".

    It reads a wider population than the archetypes themselves — those exclude
    C1-German postings before fitting, so asking them how much German the market
    demands would answer approximately zero.
    """

    def _open(self, app, breakdowns):
        monkey = [{"score_breakdown": b} for b in breakdowns]
        app.session_state["page"] = "arch"
        import ui_app  # noqa: F401 - the module under test is the script itself
        from db import supabase_utils as u
        u.get_breakdowns_above_score = lambda min_score, page_size=500: monkey
        app.run()
        return markdown_text(app)

    def test_the_panel_reports_the_german_split(self, app):
        text = self._open(app, [
            {"german_required": "C1-fluent", "jd_language": "de"},
            {"german_required": "unstated", "jd_language": "de"},
            {"german_required": "none", "jd_language": "en"},
        ])
        if "No clustering run found yet" in text:
            pytest.skip("no clustering output on this machine")
        assert "What the market asks for" in text
        assert "C2 assumed" in text
        assert "C1 stated" in text
        # Two of three are closed on German: C1 stated, plus the German ad that
        # names no level.
        assert "67%" in text

    def test_b1_is_shown_as_a_zero_rather_than_omitted(self, app):
        """The scorer has no B1 band; a missing row would read as "not asked"."""
        text = self._open(app, [{"german_required": "B2", "jd_language": "de"}])
        if "No clustering run found yet" in text:
            pytest.skip("no clustering output on this machine")
        assert "B1 stated" in text


class TestSetupPage:
    @pytest.fixture
    def setup_app(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr(supabase_utils, "base_resume_exists", lambda: True)
        monkeypatch.setattr(config, "ONBOARDING_DRAFT_PATH", str(tmp_path / "draft.json"))
        monkeypatch.setattr(config, "CANDIDATE_PROFILE_PATH", str(tmp_path / "profile.json"))
        monkeypatch.setattr(config, "APPLICATION_ANSWERS_PATH", str(tmp_path / "answers.json"))
        app.session_state["page"] = "setup"
        return app

    def test_opens_on_setup_when_there_is_no_profile(self, app, monkeypatch):
        monkeypatch.setattr(onboarding_status, "profile_present", lambda *a, **k: False)
        monkeypatch.setattr(supabase_utils, "base_resume_exists", lambda: False)
        app.run()
        assert not app.exception
        assert app.session_state["page"] == "setup"

    def test_renders_the_upload_and_the_checks_with_no_draft(self, setup_app):
        setup_app.run()
        assert not setup_app.exception
        text = markdown_text(setup_app)
        assert "Supabase settings" in text and "Profile saved" in text
        assert setup_app.button("onb_read").disabled  # nothing uploaded yet

    def test_auto_apply_documents_section_hidden_while_paused(self, setup_app):
        setup_app.run()
        assert not setup_app.exception
        assert "Documents for Auto apply" not in markdown_text(setup_app)

    def test_auto_apply_documents_section_with_nothing_uploaded(self, setup_app, monkeypatch):
        monkeypatch.setattr(config, "AUTO_APPLY_ENABLED", True)
        setup_app.run()
        assert not setup_app.exception
        assert "Documents for Auto apply" in markdown_text(setup_app)
        captions = [c.value for c in setup_app.caption]
        assert "CV: none uploaded yet" in captions
        assert "Cover letter: none uploaded yet" in captions

    def test_auto_apply_documents_section_names_the_files_in_use(self, setup_app, monkeypatch):
        monkeypatch.setattr(config, "AUTO_APPLY_ENABLED", True)
        from apply import documents
        documents.save_upload("cv", "My CV.pdf", b"%PDF")
        documents.save_upload("cover_letter", "Anschreiben.docx", b"PK")
        setup_app.run()
        assert not setup_app.exception
        captions = [c.value for c in setup_app.caption]
        assert "CV in use: My CV.pdf" in captions
        assert "Cover letter in use: Anschreiben.docx" in captions

    def test_questions_combine_the_models_and_the_fallbacks(self, setup_app):
        from tests.onboarding_samples import make_draft
        setup_app.session_state["onb_draft"] = make_draft()
        setup_app.run()
        assert not setup_app.exception
        labels = ([w.label for w in setup_app.date_input] + [w.label for w in setup_app.text_input]
                  + [w.label for w in setup_app.selectbox] + [w.label for w in setup_app.radio])
        assert "Your role at Acme is ongoing — from which date could you start?" in labels
        assert "What is your gross yearly salary expectation?" in labels

    def test_a_failed_read_still_leaves_every_question(self, setup_app):
        from onboarding.draft import OnboardingDraft
        from onboarding.questions import FIELDS
        setup_app.session_state["onb_draft"] = OnboardingDraft()
        setup_app.run()
        assert not setup_app.exception
        labels = ([w.label for w in setup_app.date_input] + [w.label for w in setup_app.text_input]
                  + [w.label for w in setup_app.selectbox] + [w.label for w in setup_app.radio])
        for spec in FIELDS:
            assert spec.fallback in labels, spec.key

    def test_save_writes_both_files_and_offers_the_secret(self, setup_app, tmp_path):
        import json
        from tests.onboarding_samples import ANSWERS, make_draft
        setup_app.session_state["onb_draft"] = make_draft()
        setup_app.session_state["onb_answers"] = dict(ANSWERS)
        setup_app.session_state["onb_step"] = "review"
        setup_app.run()
        assert not setup_app.exception
        setup_app.button("onb_save").click().run()
        assert not setup_app.exception
        saved = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
        assert saved["experience_ledger"]["working_student_months"] == 22
        assert (tmp_path / "answers.json").exists()
        assert any('"short_profile"' in c.value for c in setup_app.code)

    def test_duplicate_question_ids_from_the_model_do_not_crash(self, setup_app):
        from onboarding.draft import Question
        from tests.onboarding_samples import make_draft
        setup_app.session_state["onb_draft"] = make_draft(questions=[
            Question(id="q", field="salary_expectation", text="Salary?"),
            Question(id="q", field="notice_period", text="Notice?")])
        setup_app.run()
        assert not setup_app.exception

    def test_no_model_call_while_the_database_is_unreachable(self, setup_app, monkeypatch):
        monkeypatch.setattr(supabase_utils, "base_resume_exists", lambda: None)
        setup_app.run()
        assert not setup_app.exception
        assert setup_app.button("onb_reread").disabled

    def test_database_is_checked_once_not_on_every_rerun(self, setup_app, monkeypatch):
        calls = []
        monkeypatch.setattr(supabase_utils, "base_resume_exists", lambda: calls.append(1) or True)
        setup_app.run()
        setup_app.run()
        assert len(calls) == 1

    def test_a_saved_profile_opens_on_the_questions_without_a_read(self, setup_app, tmp_path):
        import json
        from onboarding import build
        from tests.onboarding_samples import ANSWERS, LEDGER, make_draft
        (tmp_path / "profile.json").write_text(
            json.dumps(build.build_profile(make_draft(), ANSWERS, LEDGER)), encoding="utf-8")
        setup_app.run()
        assert not setup_app.exception
        values = {w.label: w.value for w in setup_app.text_input}
        assert values["What is your residence or work permit status (or citizenship), and does an "
                      "employer need to do anything?"] == "Blue Card holder"


class TestWeeklyTracking:
    """The per-week panel on "Where I applied"."""

    def _open(self, app, monkeypatch, jobs):
        monkeypatch.setattr(supabase_utils, "get_applied_jobs_with_outcomes",
                            lambda limit=999: list(jobs))
        app.run()
        app.button("nav_apps").click().run()
        assert not app.exception
        return app

    def test_counts_this_weeks_sends(self, app, monkeypatch):
        jobs = FAKE_APPLIED_JOBS + [
            {"job_id": "now", "job_title": "Fresh", "company": "Now", "resume_score": 90,
             "application_date": _days_ago(0), "application_stage": None}]
        self._open(app, monkeypatch, jobs)
        assert int(tiles(app)["This week"]) >= 1

    def test_filters_narrow_the_count(self, app, monkeypatch):
        jobs = [{"job_id": "now", "job_title": "Fresh", "company": "Now", "resume_score": 50,
                 "application_date": _days_ago(0), "application_stage": None}]
        self._open(app, monkeypatch, jobs)
        assert tiles(app)["This week"] == "1"
        app.slider(key="wk_min_score").set_value(80).run()
        assert not app.exception
        assert tiles(app)["This week"] == "0"
        assert any("Counting 0 of 1" in c.value for c in app.caption)

    def test_saved_settings_reach_the_file(self, app, monkeypatch):
        self._open(app, monkeypatch, FAKE_APPLIED_JOBS)
        app.number_input[0].set_value(12)
        app.button[[b.label for b in app.button].index("Save")].click().run()
        assert not app.exception
        assert weekly.load_settings()["goal"] == 12



class TestApplyLinksInTheQueue:
    """`o` opens the employer's form when the job has one; the ATS shows as a chip
    and can be filtered on."""

    @staticmethod
    def _with_links(monkeypatch):
        jobs = [dict(FAKE_TODAY_JOBS[0], apply_url="https://jobs.lever.co/acme/1", ats_type="lever"),
                dict(FAKE_TODAY_JOBS[1])]
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: list(jobs))

    def test_open_goes_to_the_application_form(self, app, monkeypatch):
        self._with_links(monkeypatch)
        app.run()
        buttons = [b for b in app.get("link_button") if b.proto.label.startswith("Open application")]
        assert buttons and buttons[0].proto.url == "https://jobs.lever.co/acme/1"

    def test_the_ats_shows_as_a_chip(self, app, monkeypatch):
        self._with_links(monkeypatch)
        app.run()
        app.radio(key="queue_view").set_value("list").run()
        assert "Apply via · Lever" in markdown_text(app)

    def test_known_ats_only_hides_jobs_without_one_and_says_so(self, app, monkeypatch):
        self._with_links(monkeypatch)
        app.run()
        app.radio(key="queue_view").set_value("list").run()
        app.selectbox(key="ats_filter").set_value("known").run()
        text = markdown_text(app)
        assert "ML Engineer" in text and "Data Scientist" not in text
        assert any("Any ATS" in b.label for b in app.button)


class TestFindEmployerLink:
    """A LinkedIn job without an employer link offers to find it in the owner's
    logged-in browser (apply/resolve_link.py), run as a separate process."""

    @staticmethod
    def _linkedin(monkeypatch, **extra):
        job = dict(FAKE_TODAY_JOBS[0], job_id="4430986464", provider="linkedin",
                   job_url="https://www.linkedin.com/jobs/view/4430986464/", **extra)
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: [job])

    def test_offered_for_a_linkedin_job_without_a_link(self, app, monkeypatch):
        self._linkedin(monkeypatch, linkedin_apply_mode="offsite")
        app.run()
        assert any(b.label == "Find employer link" for b in app.button)

    @pytest.mark.parametrize("extra", [{"apply_url": "https://jobs.lever.co/a/1"},
                                       {"linkedin_apply_mode": "easy_apply"}])
    def test_not_offered_when_it_cannot_help(self, app, monkeypatch, extra):
        self._linkedin(monkeypatch, **extra)
        app.run()
        assert not any(b.label == "Find employer link" for b in app.button)

    def test_clicking_runs_the_resolver_for_that_job(self, app, monkeypatch):
        import subprocess
        self._linkedin(monkeypatch, linkedin_apply_mode="offsite")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="Employer link: https://jobs.lever.co/a/1\n", stderr="")
        monkeypatch.setattr(subprocess, "run", fake_run)
        app.run()
        next(b for b in app.button if b.label == "Find employer link").click().run()
        assert calls and calls[0][-3:] == ["-m", "apply.resolve_link", "4430986464"]
        assert any("jobs.lever.co" in s.value for s in app.success) or \
            "jobs.lever.co" in markdown_text(app) or any("jobs.lever.co" in t.value for t in app.toast)


class TestAutoApply:
    """The Auto apply button, its status chip and the unanswered-questions panel.
    The button's help text is the eligibility reason, so the owner sees what to fix."""

    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setattr(config, "AUTO_APPLY_ENABLED", True)

    @staticmethod
    def _setup(monkeypatch, tmp_path, **extra):
        from apply import eligibility
        from review import application_pack
        job = {**FAKE_TODAY_JOBS[0], "status": "new", "apply_url": "https://jobs.lever.co/acme/1",
               "ats_type": "lever", **extra}
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply", lambda limit: [job])
        monkeypatch.setattr(supabase_utils, "get_auto_apply_questions", lambda limit=50: [])
        monkeypatch.setattr(application_pack, "load_answers", lambda *a, **k: {})
        # eligibility imported these by name, so patch them where they are used.
        monkeypatch.setattr(eligibility, "unfilled_answers", lambda a: [])
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF")
        monkeypatch.setattr(eligibility, "choose_cv", lambda title: str(cv))

    @staticmethod
    def _button(app):
        return next(b for b in app.button if b.label == "Auto apply")

    def test_no_auto_apply_button_while_paused(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, auto_apply_status="queued")
        monkeypatch.setattr(config, "AUTO_APPLY_ENABLED", False)
        app.run()
        assert not app.exception
        assert not any(b.label == "Auto apply" for b in app.button)
        assert "Queued, run python -m apply.runner" not in markdown_text(app)

    def test_auto_apply_enabled_for_a_low_score(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, resume_score=41)
        app.run()
        assert not self._button(app).disabled

    def test_auto_apply_enabled_for_eligible_job(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        app.run()
        assert not app.exception
        button = self._button(app)
        assert not button.disabled
        assert "python -m apply.runner" in button.help

    def test_auto_apply_disabled_names_the_reason(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, apply_url=None, job_url=None)
        app.run()
        button = self._button(app)
        assert button.disabled and button.help == "No link to apply from"

    def test_auto_apply_disabled_before_migration(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, auto_apply_missing=True)
        app.run()
        button = self._button(app)
        assert button.disabled and button.help == "Run supabase_setup/add_auto_apply.sql first"

    def test_click_queues_the_job(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        queued = []
        monkeypatch.setattr(supabase_utils, "queue_auto_apply",
                            lambda job_id: queued.append(job_id) or True)
        app.run()
        self._button(app).click().run()
        assert queued == ["j1"]
        assert not app.exception

    def test_failed_queue_shows_the_banner(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(supabase_utils, "queue_auto_apply", lambda job_id: False)
        app.run()
        self._button(app).click().run()
        assert any("Auto apply" in e.value for e in app.error)

    def test_ready_to_submit_chip_shows_review_command(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, auto_apply_status="ready_to_submit", auto_apply_cost_usd=0.42)
        app.run()
        app.radio(key="queue_view").set_value("list").run()
        text = markdown_text(app)
        assert "python -m apply.review j1" in text and "$0.42" in text

    def test_ready_chip_appends_reason(self):
        import ui_app
        label = ui_app.auto_apply_label({"job_id": "j1", "auto_apply_status": "ready_to_submit",
                                         "auto_apply_reason": "cover letter required" + "x" * 100})
        assert label.startswith("Ready to submit: python -m apply.review j1 · cover letter required")
        assert len(label) == len("Ready to submit: python -m apply.review j1 · ") + 80

    def test_verify_chip_shows_review_command(self):
        import ui_app
        label = ui_app.auto_apply_label({"job_id": "j1", "auto_apply_status": "verify",
                                         "auto_apply_reason": "captcha"})
        assert "python -m apply.review j1" in label and "captcha" in label

    def test_questions_panel_lists_verify_reasons(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(supabase_utils, "get_auto_apply_questions", lambda limit=50: [
            {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME",
             "auto_apply_reason": "What is your notice period?"}])
        app.run()
        labels = [e.label for e in app.expander]
        assert "Questions the agent could not answer" in labels
        assert "What is your notice period?" in markdown_text(app)
        assert any("application_answers.json" in c.value for c in app.caption)

    def test_questions_panel_hidden_when_empty(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        app.run()
        assert "Questions the agent could not answer" not in [e.label for e in app.expander]

    def test_questions_panel_survives_a_failing_query(self, app, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)

        def boom(limit=50):
            raise RuntimeError("column auto_apply_status does not exist")
        monkeypatch.setattr(supabase_utils, "get_auto_apply_questions", boom)
        app.run()
        assert not app.exception
