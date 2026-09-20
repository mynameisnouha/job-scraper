"""
Streamlit UI for reviewing jobs and logging application outcomes without
touching the Supabase table editor or the static dashboard.html.

Five screens, grouped by how often they are opened: the queue and the CV
writer every day, the applications every week, calibration and the archetypes
once a month. The look — one palette, pills, surface cards — lives in
review/theme.py; this file decides what goes on each screen.

Run with: streamlit run ui_app.py
"""
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from review import apply_queue
from review import application_view
from review import calibration
from review import application_pack
from review import job_view
from review import theme as T
from clustering import results as cluster_results
from tailor import facts as tailor_facts
from tailor import harvest as tailor_harvest
from tailor import interview as tailor_interview
from tailor import loop as tailor_loop
from tailor import settings as tailor_settings
from tailor import store as tailor_store
from tailor import documents as tailor_documents
from db import supabase_utils

STAGE_LABELS = {
    "applied": "Applied",
    "interview_1": "Interview 1",
    "interview_2": "Interview 2",
    "interview_3": "Interview 3",
    "offer": "Offer",
    "rejected": "Rejected",
    "ghosted": "Ghosted (no response)",
    "spam_or_removed": "Offer removed / spam",
}
STAGE_ORDER = list(STAGE_LABELS.keys())

REJECTION_REASONS = [
    "", "years_experience", "german_level", "visa", "other_candidate",
    "role_filled", "no_reason_given", "other",
]
REJECTION_LABELS = {
    "": "Reason not given yet",
    "years_experience": "Years required",
    "german_level": "German level",
    "visa": "No sponsorship",
    "other_candidate": "Another candidate",
    "role_filled": "Role filled",
    "no_reason_given": "No reason given",
    "other": "Other",
}

REC_LABELS = {
    "apply_now": "Apply now",
    "apply_after_fixes": "Apply after fixes",
    "apply_if_gate_negotiable": "If the gate is negotiable",
    "skip": "Scorer says skip",
}

# The sidebar, grouped by cadence. The id is the session key; the label is what
# the button says.
PAGES = [
    ("Every day", [("queue", "Jobs to apply"), ("tailor", "Write my CV")]),
    ("Every week", [("apps", "Where I applied")]),
    ("Every month", [("cal", "Is the score right?"), ("arch", "Which CV to use")]),
]
DEFAULT_PAGE = "queue"

# The bar a scored posting has to clear to be worth an evening. Used for the
# scrape pulse ("how many above the bar today?"), which is a statement about the
# market and needs a fixed reference point to mean anything.
#
# It is NOT the queue's default filter any more. Opening the queue already
# filtered to 70+ hid the decision rather than making it: a 64 you would have
# applied to never appeared, and the scorer is calibrated but not that
# calibrated — five points is well inside its own noise. The filter still
# exists, it just starts open, and the chip strip says what raising it would
# hide.
SCORE_BAR = 70

# What the "Min score" slider starts at. Zero: show everything scored, and let
# the sort order do the ranking.
DEFAULT_MIN_SCORE = 0

st.set_page_config(page_title="Job Hunt", layout="wide", initial_sidebar_state="expanded")

# Filled once per script run. Streamlit re-executes this file top to bottom on
# every interaction, so this is a per-run memo, not a cache: the sidebar and
# the page both need the queue and must not query it twice.
_RUN = {}


def load_queue():
    if "queue" not in _RUN:
        _RUN["queue"] = supabase_utils.get_top_scored_jobs_to_apply(999)
    return _RUN["queue"]


def load_applied():
    if "applied" not in _RUN:
        _RUN["applied"] = supabase_utils.get_applied_jobs_with_outcomes(999)
    return _RUN["applied"]


def html(fragment):
    st.markdown(fragment, unsafe_allow_html=True)


def fmt_date(value):
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "—"


def flash_saved(message):
    """
    Remember that a save just succeeded so it can be shown *after* st.rerun().
    Writing the confirmation before a rerun is pointless — the rerun discards it
    before the browser ever paints it.
    """
    st.session_state["flash"] = message


def consume_flash():
    """Pop the pending save confirmation, if any."""
    return st.session_state.pop("flash", None)


def flash_failed(message):
    """Remember that a write FAILED, so it is shown where it cannot be missed.

    A failure used to be an st.error inside the button's own column - a narrow
    red box under "Apply" that the next click wiped. That is how a job you
    marked applied stayed in the queue with nothing on screen to say why: the
    write had failed, the page had redrawn, and the message was gone. Failures
    now survive the rerun and render as a banner at the top of the page.
    """
    st.session_state["flash_failed"] = message


def consume_failure():
    return st.session_state.pop("flash_failed", None)


def pct(value):
    return "—" if value is None else f"{value * 100:.0f}%"


def matches_search(job, term):
    """Case-insensitive substring match over job title and company."""
    if not term or not term.strip():
        return True
    needle = term.strip().lower()
    haystack = f"{job.get('job_title') or ''} {job.get('company') or ''}".lower()
    return needle in haystack


def found_label(job):
    """
    "found …" for a job, or None when the row carries no scrape timestamp.

    Under 24 hours old this carries the clock time as well as the elapsed hours,
    because being early to a posting is most of the advantage.
    """
    found = apply_queue.format_found(job)
    return f"found {found}" if found else None


def go(page):
    """Switch screens. Applied before the sidebar is drawn, on the next run."""
    st.session_state["page"] = page


def tailor_this_job(job):
    """Send a job to the Write my CV page, already selected.

    Deliberately a hand-off rather than generating in place. A run is several
    LLM calls over a few minutes, and Streamlit reruns the whole script per
    interaction, so generating inside a queue card would freeze the queue for
    the duration. It would also skip the gap interview, which is the step that
    adds information rather than rearranging it — the part actually worth having.
    """
    st.session_state["tailor_job_id"] = job.get("job_id")
    go("tailor")
    st.rerun()


def close_posting(job):
    """Mark a posting closed: it is gone, rather than declined.

    Kept separate from Skip on purpose. A skip records a judgement you made and
    is evidence about the scorer; a closed posting is the world changing
    underneath the queue and says nothing about fit. Collapsing the two would
    quietly poison the skip-reason data.
    """
    job_id = job.get("job_id")
    if supabase_utils.mark_job_closed(job_id):
        flash_saved(f"Closed: {job.get('job_title') or job_id}")
        st.rerun()
    else:
        st.error("Failed to close — check logs.")


# ═══════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════

def nav_counts():
    """The badges: how many things each screen is asking you to look at."""
    queue = load_queue()
    applied = load_applied()
    summary = cluster_results.load_summary() or {}
    return {
        "queue": len(queue),
        "apps": application_view.counts(applied)["chase"],
        "arch": summary.get("chosen_k") or 0,
    }


def render_sidebar():
    with st.sidebar:
        html(T.brand())
        counts = nav_counts()
        current = st.session_state.get("page", DEFAULT_PAGE)
        for group, items in PAGES:
            html(f'<div class="jh-navlabel">{T.esc(group)}</div>')
            for page_id, label in items:
                n = counts.get(page_id)
                text = f"{label} `{n}`" if n else label
                if st.button(text, key=f"nav_{page_id}", width="stretch",
                             type="primary" if page_id == current else "secondary"):
                    go(page_id)
                    st.rerun()

        pulse = supabase_utils.get_scrape_pulse(bar=SCORE_BAR)
        when = ""
        if pulse and pulse.get("last_scraped_at"):
            when = apply_queue.format_found({"scraped_at": pulse["last_scraped_at"]}) or ""
        html('<div style="height:18px"></div>' + T.pulse_card(pulse, when))


# ═══════════════════════════════════════════════════════════════════════════
# Jobs to apply
# ═══════════════════════════════════════════════════════════════════════════

# What each filter hides, and how to stop it hiding anything. The strip under
# the controls shows these counts so a short queue never reads as an empty
# market — it reads as "12 postings under your bar, 194 older than a day".
FILTER_KEYS = {
    "min_score": {"default": DEFAULT_MIN_SCORE, "open": 0},
    "date_window": {"default": apply_queue.DEFAULT_DATE_WINDOW, "open": "all"},
    "role_type": {"default": "all", "open": "all"},
    "german_max": {"default": "any", "open": "any"},
}


# Queue controls whose value must outlive a trip to another page. Everything
# here is bound to a widget, and that is the problem: Streamlit deletes a
# widget's session-state entry at the end of any run in which the widget was
# not drawn. Go to "Write my CV" and back, and every filter is gone — the German
# filter you set is "any" again and the 24-hour window is back. Observed, not
# theorised: set to none/30d, one page away, returned as any/24h.
_PERSISTENT_KEYS = ("queue_view", "sort_mode", "search_jobs", "search_applied")


def init_filters():
    """Seed the queue controls once, and keep them alive on every run after.

    Called from main() on every page, not from the queue page. Re-assigning a
    key to itself is the documented way to stop Streamlit's cleanup taking it:
    the assignment marks it as set by the script rather than by a widget, so a
    run that never draws the widget still leaves the value where it was.
    """
    for key, spec in FILTER_KEYS.items():
        st.session_state.setdefault(key, spec["default"])
    st.session_state.setdefault("queue_view", "focus")
    st.session_state.setdefault("sort_mode", "score")
    for key in (*FILTER_KEYS, *_PERSISTENT_KEYS):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def open_filter(key):
    """Callback for a strip chip: stop this filter hiding anything."""
    st.session_state[key] = FILTER_KEYS[key]["open"]


def hidden_by_filters(jobs):
    """(filter key, label, count) for every filter currently hiding something."""
    out = []
    window = st.session_state["date_window"]
    if window != "all":
        n = sum(1 for j in jobs if not apply_queue.within_window(j, window))
        span = apply_queue.DATE_WINDOW_LABELS[window].replace("Last ", "")
        out.append(("date_window", f"Older than {span}", n))
    bar = st.session_state["min_score"]
    if bar > 0:
        n = sum(1 for j in jobs if (j.get("resume_score") or 0) < bar)
        out.append(("min_score", f"Score under {bar}", n))
    role = st.session_state["role_type"]
    if role != "all":
        n = sum(1 for j in jobs if not apply_queue.matches_role_type(j, role))
        out.append(("role_type", "Programmes" if role == "roles" else "Standard roles", n))
    german = st.session_state["german_max"]
    if german != "any":
        n = sum(1 for j in jobs if not apply_queue.matches_german(j, german))
        out.append(("german_max", "German above B2" if german == "B2" else "Any German", n))
    return [(k, label, n) for k, label, n in out if n]


def render_queue_page():
    jobs = load_queue()
    if not jobs:
        html(T.page_header("Jobs to apply", "Nothing scored is waiting."))
        st.info("No scored jobs ready for application right now.")
        return

    total = len(jobs)
    all_scores = [j.get("resume_score") for j in jobs if j.get("resume_score") is not None]
    fresh = sum(1 for j in jobs if apply_queue.within_window(j, "24h"))

    head = st.columns([3, 1.6], vertical_alignment="bottom")
    with head[0]:
        html(T.page_header(
            "Jobs to apply",
            f"{total} scored postings waiting. <b>{fresh} arrived in the last day.</b>"))
    with head[1]:
        st.radio("View", ["list", "focus"], key="queue_view", horizontal=True,
                 format_func=lambda v: f"All {total}" if v == "list" else "One at a time",
                 label_visibility="collapsed")

    controls = st.columns([2.4, 2.2, 1.1], vertical_alignment="center")
    with controls[0]:
        st.radio("Sort by", apply_queue.SORT_MODES, key="sort_mode", horizontal=True,
                 format_func=lambda m: apply_queue.SORT_MODE_LABELS[m],
                 label_visibility="collapsed")
    with controls[1]:
        search = st.text_input("Search", key="search_jobs", placeholder="Title or company",
                               label_visibility="collapsed")
    with controls[2]:
        with st.popover("Filters", width="stretch"):
            st.selectbox("Found within", apply_queue.DATE_WINDOW_KEYS, key="date_window",
                         format_func=lambda w: apply_queue.DATE_WINDOW_LABELS[w],
                         help="A posting's value decays fast — the first applicants are "
                              "read first. Jobs with no scrape timestamp only show under "
                              "'Any time'.")
            st.slider("Min score", 0, 100, step=5, key="min_score")
            st.selectbox("Show", apply_queue.ROLE_TYPES, key="role_type",
                         format_func=lambda r: apply_queue.ROLE_TYPE_LABELS[r],
                         help="Graduate/trainee programmes have one intake a year and an "
                              "assessment centre; standard roles have a recruiter reading "
                              "CVs this week. Work them in separate sittings.")
            st.selectbox("German", apply_queue.GERMAN_FILTERS, key="german_max",
                         format_func=lambda g: apply_queue.GERMAN_FILTER_LABELS[g],
                         help="What the ad demands, not the language it is written in. "
                              "Ads that name no level are kept under every setting but "
                              "'No German demanded'.")

    render_hidden_strip(jobs)

    date_window = st.session_state["date_window"]
    min_score = st.session_state["min_score"]
    role_type = st.session_state["role_type"]
    german_max = st.session_state["german_max"]
    sort_by = st.session_state["sort_mode"]

    jobs = [j for j in jobs if apply_queue.within_window(j, date_window)]
    jobs = [j for j in jobs if apply_queue.matches_role_type(j, role_type)]
    jobs = [j for j in jobs if apply_queue.matches_german(j, german_max)]
    jobs = [j for j in jobs if (j.get("resume_score") or 0) >= min_score]
    jobs = [j for j in jobs if matches_search(j, search)]
    jobs = apply_queue.sort_jobs(jobs, sort_by)

    if not jobs:
        st.info("No jobs match these filters. Try clearing the search, or open one of "
                "the filters above.")
        return

    # Changing the filters or the sort is an explicit "re-shuffle the queue",
    # so the cursor goes back to the top. Only an incidental refresh — a scrape
    # run landing, a job leaving — keeps your place.
    signature = (sort_by, date_window, min_score, role_type, german_max,
                 (search or "").strip().lower())
    if st.session_state.get("queue_signature") != signature:
        st.session_state["queue_signature"] = signature
        st.session_state["cursor_job"] = None
        st.session_state["cursor_idx"] = 0

    if st.session_state["queue_view"] == "focus":
        render_focus_queue(jobs, all_scores)
        visible = jobs
    else:
        visible = render_job_list(jobs, all_scores, sort_by)

    # Re-opened on every rerun so widgets inside the dialog keep working.
    # A job filtered out of the list closes it rather than stranding it open.
    open_job_id = st.session_state.get("open_job")
    if open_job_id:
        open_job = next((j for j in visible if j.get("job_id") == open_job_id), None)
        if open_job:
            job_details_dialog(open_job)
        else:
            close_details()


def render_hidden_strip(jobs):
    """What the filters are hiding, each removable with one click."""
    hidden = hidden_by_filters(jobs)
    with st.container(key="hidden-strip"):
        cols = st.columns([1.3] + [1.4] * len(hidden) + [max(0.2, 5 - 1.4 * len(hidden))],
                          vertical_alignment="center")
        with cols[0]:
            html(f'<span style="font-size:13px;color:{T.BLUE[900]};font-weight:600">'
                 'Hidden right now:</span>')
        if not hidden:
            with cols[1]:
                html(f'<span style="font-size:13px;color:{T.BLUE[800]}">Nothing — you\'re '
                     'seeing every scored posting.</span>')
        for col, (key, label, n) in zip(cols[1:], hidden):
            with col:
                st.button(f"{label} · **{n}** ×", key=f"drop_{key}", on_click=open_filter,
                          args=(key,), help="Show these", width="stretch")


def render_job_list(jobs, all_scores, sort_by):
    show_n = int(st.session_state.get("show_n", 20))
    visible = jobs[:show_n]
    for job in visible:
        render_job_card(job, all_scores)

    foot = st.columns([3, 1], vertical_alignment="center")
    with foot[0]:
        html(f'<span style="font-size:14px;color:{T.NEUTRAL[700]}">Showing {len(visible)} of '
             f'{len(jobs)} · sorted by {T.esc(apply_queue.SORT_MODE_LABELS[sort_by].lower())}'
             '</span>')
    with foot[1]:
        if len(jobs) > show_n and st.button("Load 20 more", key="load_more", width="stretch"):
            st.session_state["show_n"] = show_n + 20
            st.rerun()
    return visible


def mark_applied(job):
    """Shared by the list card, the focus card and the detail dialog.

    Reruns on failure as well as success. The rerun is what makes the outcome
    visible either way: on success the job leaves the queue, on failure the
    banner says it did not - and the row is re-read from Supabase rather than
    trusted from the cached list, so what you see is what is stored.
    """
    job_id = job.get("job_id")
    title = job.get("job_title") or job_id
    if supabase_utils.mark_job_applied(job_id):
        supabase_utils.update_application_stage(job_id, "applied")
        flash_saved(f"Marked applied: {title}")
    else:
        flash_failed(f"Could not mark \"{title}\" as applied — it is still in the queue. "
                     "Supabase rejected or dropped the write; the terminal running "
                     "Streamlit has the error. Try again.")
    st.rerun()


def skip_job(job, reason):
    """
    Take a job out of the queue without applying. Soft — the row survives, and
    the reason is the label that makes the skip worth something later.
    """
    job_id = job.get("job_id")
    title = job.get("job_title") or job_id
    if supabase_utils.dismiss_job(job_id, reason):
        st.session_state["last_skipped"] = {"job_id": job_id, "title": title}
        st.session_state.pop("skipping", None)
        flash_saved(f"Skipped: {title} ({apply_queue.SKIP_REASON_LABELS.get(reason, reason)})")
    else:
        flash_failed(f"Could not skip \"{title}\" — it is still in the queue. Has "
                     "supabase_setup/add_dismissal.sql been run? The terminal has the error.")
    st.rerun()


def start_delete(job):
    st.session_state["deleting"] = job.get("job_id")
    st.rerun()


def delete_posting(job, reason, note=""):
    """Remove a posting from the corpus, with the reason it should not be there.

    No undo, and the confirm step is the whole reason this is a two-click action:
    the row carries the description, the score and the breakdown, and nothing
    keeps a copy. What survives is the tombstone, which is what stops the next
    scrape quietly putting the posting back.
    """
    job_id = job.get("job_id")
    title = job.get("job_title") or job_id
    if supabase_utils.delete_job(job, reason, note):
        st.session_state.pop("deleting", None)
        # The cached queue still holds the deleted row, and the card would render
        # one more time before the next load.
        _RUN.pop("queue", None)
        flash_saved(f"Deleted: {title} "
                    f"({apply_queue.DELETE_REASON_LABELS.get(reason, reason)})")
        st.rerun()
    else:
        flash_failed(f"Could not delete \"{title}\" — nothing was removed. Has "
                     "supabase_setup/add_deleted_jobs.sql been run? The terminal has the error.")
        st.rerun()


def render_delete_panel(job):
    """The confirm step, with the reason asked at the moment of the decision.

    Deliberately heavier than the skip panel: a skip is reversible from the queue
    and a delete is not, so this one says what is about to be lost and takes a
    second click.
    """
    if st.session_state.get("deleting") != job.get("job_id"):
        return
    job_id = job.get("job_id")
    with st.container(border=True, key="delete-panel"):
        html('<div style="font-size:13.5px;font-weight:600;margin-bottom:2px">'
             'Delete this posting for good?</div>'
             f'<div style="font-size:12.5px;color:{T.NEUTRAL[700]};margin-bottom:8px">'
             'The description, score and breakdown go with it and cannot be restored. '
             'It will not come back on the next scrape. To take a real job out of the '
             'queue instead, cancel and use Skip.</div>')
        note = st.text_input("Anything worth remembering?", key=f"delnote_{job_id}",
                             placeholder="Optional — a note stored with the deletion.")
        with st.container(horizontal=True, gap="small"):
            for reason in apply_queue.DELETE_REASONS:
                if st.button(apply_queue.DELETE_REASON_SHORT[reason],
                             key=f"delreason_{job_id}_{reason}", width="content",
                             help=apply_queue.DELETE_REASON_LABELS[reason]):
                    delete_posting(job, reason, note)
        if st.button("Cancel", key=f"delcancel_{job_id}", type="tertiary"):
            st.session_state.pop("deleting", None)
            st.rerun()


def start_skip(job):
    st.session_state["skipping"] = job.get("job_id")
    st.rerun()


def render_skip_panel(job):
    """
    The reason, asked at the moment of the decision. "I skipped every job
    needing C1 German" is a finding, and it only exists if the reason was
    recorded when the skip happened. Digits are the keyboard path.
    """
    if st.session_state.get("skipping") != job.get("job_id"):
        return
    job_id = job.get("job_id")
    with st.container(key="skip-panel"):
        html('<div style="font-size:13.5px;font-weight:600;margin-bottom:6px">'
             'Why are you skipping this one?</div>')
        with st.container(horizontal=True, gap="small"):
            for n, reason in enumerate(apply_queue.SKIP_REASONS, start=1):
                if st.button(f"{apply_queue.SKIP_REASON_SHORT[reason]} `{n}`",
                             key=f"skipreason_{job_id}_{reason}", width="content",
                             help=apply_queue.SKIP_REASON_LABELS[reason]):
                    skip_job(job, reason)
        if st.button("Cancel", key=f"skipcancel_{job_id}", type="tertiary"):
            st.session_state.pop("skipping", None)
            st.rerun()


def render_overflow(job, prefix, with_keys=False):
    """
    One primary, one secondary, one overflow — same order everywhere. The
    overflow holds the actions that are not a decision about the job.
    """
    job_id = job.get("job_id")
    url = job.get("job_url")

    def k(key):
        return f" `{key}`" if with_keys else ""

    with st.popover("⋯", help="More actions"):
        if prefix == "list":
            if st.button("Full breakdown", key=f"details_{job_id}", width="stretch"):
                # Held in session_state rather than opened inline: a widget click
                # inside the dialog reruns the script, and an inline-opened dialog
                # would vanish mid-interaction.
                st.session_state["open_job"] = job_id
                st.rerun()
        if url:
            st.link_button(f"Open posting{k('o')}", url, width="stretch")
        else:
            st.button(f"Open posting{k('o')}", key=f"{prefix}_open_{job_id}", disabled=True,
                      width="stretch", help="This posting has no URL.")
        if st.button(f"Build application pack{k('p')}", key=f"{prefix}_pack_{job_id}",
                     width="stretch",
                     help="Write answers, checklist, pitch and the routed CV to "
                          "output/applications/"):
            build_application_pack(job)
        if st.button(f"Tailor a CV for this{k('c')}", key=f"{prefix}_tailor_{job_id}",
                     width="stretch",
                     help="Write a CV and Anschreiben for this posting, from your fact base."):
            tailor_this_job(job)
        html('<div class="jh-divider"></div>')
        if st.button("No longer accepting", key=f"{prefix}_closed_{job_id}", width="stretch",
                     help="The posting is closed and you never applied — take it out of "
                          "the queue. Distinct from Skip, which records that you decided "
                          "against it."):
            close_posting(job)
        if st.button("Delete this posting", key=f"{prefix}_delete_{job_id}", width="stretch",
                     type="tertiary",
                     help="For postings that should never have been here: agency reposts, "
                          "duplicates, mis-scraped rows. The record goes for good — use "
                          "Skip for a real job you decided against, because those rows "
                          "are what the skip statistics are built from."):
            start_delete(job)


def card_facts(job, breakdown):
    """The chip row: German first, then effort, odds, source and the verdict word."""
    facts = job_view.quick_facts(breakdown)
    rec = breakdown.get("recommendation")
    if rec in REC_LABELS and rec != "apply_now":
        facts.append(("Scorer", REC_LABELS[rec]))
    elif rec == "apply_now":
        facts.append(("Scorer", "Apply now"))
    return facts


def render_job_card(job, all_scores):
    """
    One row of the list: the verdict on the left, the argument in the middle,
    the decision on the right. The full breakdown stays behind the overflow so
    the list stays scannable.
    """
    breakdown = job.get("score_breakdown") or {}
    job_id = job.get("job_id")

    with st.container(border=True):
        cols = st.columns([1.1, 6.4, 2.6], vertical_alignment="top")
        with cols[0]:
            html(T.score_disc(job.get("resume_score"), all_scores))
        with cols[1]:
            html(T.title_block(job.get("job_title") or "N/A", job.get("company") or "N/A",
                               found_label(job), is_program=apply_queue.is_program(job))
                 + '<div style="height:10px"></div>'
                 + T.verdict(job_view.summary(breakdown))
                 + '<div style="height:10px"></div>'
                 + T.fact_chips(breakdown, card_facts(job, breakdown)))
            render_skip_panel(job)
            render_delete_panel(job)
        with cols[2]:
            actions = st.columns([1.3, 1.1, 0.7])
            with actions[0]:
                if st.button("Apply", key=f"apply_{job_id}", type="primary", width="stretch",
                             help="Mark applied and move it to Where I applied."):
                    mark_applied(job)
            with actions[1]:
                if st.button("Skip", key=f"skip_{job_id}", width="stretch",
                             help="Not applying to this one — take it out of the queue. "
                                  "The row stays; you'll be asked why."):
                    start_skip(job)
            with actions[2]:
                render_overflow(job, "list")


def render_job_body(job, all_scores=()):
    """
    The full picture for one job — everything needed to actually write the
    application. Shared by the detail dialog and the focus queue, which show the
    same thing and differ only in what surrounds it.
    """
    breakdown = job.get("score_breakdown") or {}
    zone = st.columns([1.1, 6], vertical_alignment="top")
    with zone[0]:
        html(T.score_disc(job.get("resume_score"), all_scores, size=92))
    with zone[1]:
        html(T.title_block(job.get("job_title") or "N/A", job.get("company") or "N/A",
                           found_label(job), url=job.get("job_url"),
                           is_program=apply_queue.is_program(job), size=34)
             + '<div style="height:12px"></div>'
             + T.verdict(job_view.summary(breakdown), size=19))

    html(T.gate_row(breakdown, card_facts(job, breakdown)))
    html('<div style="height:8px"></div>'
         + T.two_up(T.tinted_list("Lead with", job_view.pros(breakdown), "blue"),
                    T.tinted_list("They'll push back on", job_view.cons(breakdown), "purple")))
    wins = job_view.quick_wins(breakdown)
    if wins:
        effort = next((v for k, v in job_view.quick_facts(breakdown) if k == "Effort"), None)
        html('<div style="height:8px"></div>' + T.fix_list(wins, effort))


def render_side_rail(job):
    """Pitch, context and the key legend — what you need while typing."""
    breakdown = job.get("score_breakdown") or {}
    pitch = job.get("why_me_pitch")
    with st.container(key="pitch-card"):
        html(T.kicker("Pitch — ready to paste", T.BLUE[800]))
        if pitch:
            st.code(pitch, language=None, wrap_lines=True)
        else:
            html(f'<div style="font-size:13.5px;color:{T.BLUE[800]}">No pitch stored for this '
                 'posting yet — the scorer writes one on the next pass.</div>')
    with st.container(border=True):
        html(T.context_list(job_view.context_facts(breakdown) + job_view.competition(breakdown),
                            job_view.confidence_note(breakdown)))
    with st.container(border=True):
        html(T.key_legend(apply_queue.SHORTCUTS))


def move_cursor(jobs, index):
    """Point the queue at a different job, by position."""
    index = apply_queue.clamp_cursor(index, len(jobs))
    st.session_state["cursor_idx"] = index
    st.session_state["cursor_job"] = jobs[index].get("job_id") if jobs else None
    st.rerun()


def keyboard_shortcuts():
    """
    Bind the single-key shortcuts to the buttons already on the page.

    Streamlit has no key-binding API, so this listens on the parent document and
    clicks the control whose label ends in the key's code chip — "Skip `s`". The
    label is the binding: nothing here can drift out of sync with a renamed
    button, it just stops matching, and the mouse still works. Keys that live in
    the overflow menu open it first. Typing in any field is left alone.
    """
    keys = [key for key, _ in apply_queue.SHORTCUTS] + [str(n) for n in range(1, 10)]
    st.iframe(
        f"""
        <script>
        const keys = {keys!r};
        const doc = window.parent.document;
        const find = (key) => {{
            for (const el of doc.querySelectorAll("button, a")) {{
                const text = (el.innerText || "").trim();
                if (text === key || text.endsWith(" " + key)) return el;
            }}
            return null;
        }};
        if (!doc.__jobQueueKeysBound) {{
            doc.__jobQueueKeysBound = true;
            doc.addEventListener("keydown", (e) => {{
                if (e.metaKey || e.ctrlKey || e.altKey) return;
                const tag = (doc.activeElement || {{}}).tagName;
                if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
                if (!keys.includes(e.key)) return;
                e.preventDefault();
                const hit = find(e.key);
                if (hit) {{ hit.click(); return; }}
                // Not on the page: it may sit in the overflow menu. Open that, retry.
                const more = find("⋯");
                if (!more) return;
                more.click();
                setTimeout(() => {{ const later = find(e.key); if (later) later.click(); }}, 250);
            }});
        }}
        </script>
        """,
        # No visible output — the iframe exists only to host the key listener.
        height=1,
    )


def render_focus_queue(jobs, all_scores):
    """
    One job at a time, in rank order, driven from the keyboard.

    At 50-100 applications a week the list view is the wrong shape: it re-reads
    the same headers on every pass and offers no answer to "where did I stop".
    The cursor is anchored to a job id rather than a position, because the queue
    refreshes underneath you — four scrape runs a day, and every apply or skip
    removes a row.
    """
    index = apply_queue.resume_cursor(jobs, st.session_state.get("cursor_job"),
                                      st.session_state.get("cursor_idx", 0))
    st.session_state["cursor_idx"] = index
    job = jobs[index]
    st.session_state["cursor_job"] = job.get("job_id")
    job_id = job.get("job_id")
    left = len(jobs) - index - 1
    hours = sum(apply_queue.effort_hours(j) or 0 for j in jobs[index + 1:])
    pace = f" · about {hours:.0f}h of applications" if hours else ""

    main, rail = st.columns([1.9, 1], gap="medium")
    with main:
        top = st.columns([1.2, 5, 2.4], vertical_alignment="center")
        with top[0]:
            html(f'<span style="font-family:{T.FONT_HEADING};font-size:15px">'
                 f'{index + 1} of {len(jobs)}</span>')
        with top[1]:
            st.progress((index + 1) / len(jobs))
        with top[2]:
            html(f'<span style="font-size:13px;color:{T.NEUTRAL[700]}">{left} left{pace}</span>')

        with st.container(border=True):
            render_job_body(job, all_scores)
            render_skip_panel(job)
            render_delete_panel(job)
            html('<div style="height:6px;border-bottom:1px solid var(--jh-divider);'
                 'margin-bottom:12px"></div>')
            actions = st.columns([1.7, 1, 0.6, 1.6, 0.6, 0.6], vertical_alignment="center")
            with actions[0]:
                if st.button("Mark applied `a`", key=f"focus_apply_{job_id}",
                             type="primary", width="stretch"):
                    mark_applied(job)
            with actions[1]:
                if st.button("Skip `s`", key=f"focus_skip_{job_id}", width="stretch",
                             help="Removes it from the queue for good. The row stays — a skip "
                                  "is a label, not a delete."):
                    start_skip(job)
            with actions[2]:
                render_overflow(job, "focus", with_keys=True)
            with actions[4]:
                if st.button("`k`", key="focus_prev", width="stretch", disabled=index == 0,
                             help="Previous"):
                    move_cursor(jobs, index - 1)
            with actions[5]:
                if st.button("`j`", key="focus_next", width="stretch",
                             disabled=index >= len(jobs) - 1, help="Next"):
                    move_cursor(jobs, index + 1)

        render_undo_skip()
    with rail:
        render_side_rail(job)
    keyboard_shortcuts()


def render_undo_skip():
    """One-step undo, because `s` is one keystroke away from the wrong job."""
    last = st.session_state.get("last_skipped")
    if not last:
        return
    cols = st.columns([3, 1], vertical_alignment="center")
    with cols[0]:
        st.caption(f"Last skipped: {last['title']}")
    with cols[1]:
        if st.button("Undo skip", key="undo_skip", width="stretch"):
            if supabase_utils.undismiss_job(last["job_id"]):
                st.session_state.pop("last_skipped", None)
                st.session_state["cursor_job"] = last["job_id"]
                flash_saved(f"Restored: {last['title']}")
                st.rerun()
            else:
                st.error("Could not restore that job — check logs.")


def close_details():
    st.session_state.pop("open_job", None)


def close_applied_details():
    st.session_state.pop("open_applied_job", None)


def render_dialog_body(job):
    """The breakdown plus the pitch and context the side rail would show."""
    breakdown = job.get("score_breakdown") or {}
    render_job_body(job)
    pitch = job.get("why_me_pitch")
    if pitch:
        html('<div style="height:8px"></div>' + T.kicker("Pitch — ready to paste", T.BLUE[800]))
        st.code(pitch, language=None, wrap_lines=True)
    context = job_view.context_facts(breakdown) + job_view.competition(breakdown)
    note = job_view.confidence_note(breakdown)
    if context or note:
        html('<div style="height:8px"></div>' + T.context_list(context, note))


@st.dialog("Application details", width="large", on_dismiss=close_applied_details)
def applied_details_dialog(job):
    """The same breakdown the queue shows, for a job you have already applied to.

    A separate dialog rather than a flag on the other one: the queue's version
    ends in "Mark applied", which is meaningless here and actively confusing
    next to a stage selector that already says Interview or Rejected.
    """
    render_dialog_body(job)


@st.dialog("Job details", width="large", on_dismiss=close_details)
def job_details_dialog(job):
    render_dialog_body(job)
    html('<div style="height:8px"></div>')
    if st.button("Mark applied", key=f"dlg_apply_{job.get('job_id')}", type="primary"):
        close_details()
        mark_applied(job)


def build_application_pack(job):
    """Write the application pack and report what landed — and what did not.

    Warnings are shown rather than swallowed: a pack missing the CV or the pitch is
    still useful, but only if you know which part you have to do by hand.
    """
    try:
        result = application_pack.build_pack(job)
    except OSError as e:
        st.error(f"Could not write the pack: {e}")
        return

    flash_saved(f"Pack written to {result['path']}")
    for warning in result["warnings"]:
        st.warning(warning)


# ═══════════════════════════════════════════════════════════════════════════
# Where I applied
# ═══════════════════════════════════════════════════════════════════════════

def render_ghost_prompt(jobs):
    """
    Offer to close out applications that have gone quiet. Suggested, never
    automatic — a late reply is possible, and a wrong 'ghosted' is a false
    negative in the data everything downstream learns from.
    """
    stale = calibration.stale_pending(jobs)
    if not stale:
        return
    n = len(stale)
    with st.container(key="ghost-banner"):
        cols = st.columns([3, 1.1], vertical_alignment="center")
        with cols[0]:
            html(f'<div style="font-family:{T.FONT_HEADING};font-size:19px;color:{T.PURPLE[900]};'
                 f'margin-bottom:4px">{n} application{"s" if n != 1 else ""} '
                 f'{"have" if n != 1 else "has"} gone quiet for '
                 f'{calibration.GHOSTED_AFTER_DAYS}+ days</div>'
                 f'<div style="font-size:14px;line-height:1.5;color:{T.PURPLE[900]}">Unresolved '
                 'applications teach calibration nothing. Chase them or close them out — a late '
                 'reply is still possible, so nothing happens automatically.</div>')
        with cols[1]:
            if st.button(f"Mark all {n} ghosted", key="ghost_all", type="primary", width="stretch"):
                failed = [j.get("job_id") for j in stale
                          if not supabase_utils.update_application_stage(j.get("job_id"), "ghosted")]
                if failed:
                    st.error(f"{len(failed)} could not be updated — check logs.")
                else:
                    flash_saved(f"Marked {n} application(s) as ghosted")
                    st.rerun()
        with st.expander(f"Review the {n}"):
            for job in stale:
                age = calibration.days_since_applied(job)
                st.markdown(f"- **{job.get('job_title') or job.get('job_id')}** — "
                            f"{job.get('company') or '—'} · applied {age} days ago")


def render_update_form(job):
    """The stage, the reason, the note — a form, not a row of controls."""
    job_id = job.get("job_id")
    title = job.get("job_title") or "N/A"
    current_stage = application_view.stage_of(job)
    with st.popover("Update", width="stretch"):
        new_stage = st.selectbox(
            "Stage", STAGE_ORDER,
            index=STAGE_ORDER.index(current_stage) if current_stage in STAGE_ORDER else 0,
            format_func=lambda s: STAGE_LABELS[s], key=f"stage_{job_id}",
        )
        reason = ""
        if new_stage == "rejected":
            reason = st.selectbox(
                "Reason", REJECTION_REASONS,
                index=REJECTION_REASONS.index(job.get("rejection_reason") or "")
                if job.get("rejection_reason") in REJECTION_REASONS else 0,
                key=f"reason_{job_id}", format_func=lambda r: REJECTION_LABELS.get(r, r),
            )
        notes = st.text_input(
            "Notes", value=job.get("outcome_notes") or "", key=f"notes_{job_id}",
            placeholder="What they said, next steps",
        )
        if st.button("Save", key=f"save_{job_id}", type="primary", width="stretch"):
            ok = supabase_utils.update_application_stage(
                job_id, new_stage, rejection_reason=reason or None, notes=notes or None)
            if ok:
                flash_saved(f"Saved: {title} → {STAGE_LABELS.get(new_stage, new_stage)}")
                st.rerun()
            else:
                st.error("Failed to save — check logs (has the SQL migration been run?).")


def render_application_record(job):
    """The row is a record, not a form: what happened, and what you argued from."""
    job_id = job.get("job_id")
    breakdown = job.get("score_breakdown") or {}
    age = application_view.age_label(job)
    age_color = T.PURPLE[700] if age["tone"] == "warn" else T.NEUTRAL[700]
    updated = fmt_date(job.get("stage_updated_at"))
    line = (f'<b>{T.esc(job.get("company") or "N/A")}</b> · applied '
            f'{fmt_date(job.get("application_date"))}')
    if age["text"]:
        line += f' · <span style="color:{age_color};font-weight:600">{T.esc(age["text"])}</span>'
    if updated != "—":
        # stage_updated_at is durable proof the write landed — it survives a
        # refresh, unlike the transient toast.
        line += f' · <span style="color:{T.NEUTRAL[600]}">updated {updated}</span>'
    url = job.get("job_url")
    title = T.esc(job.get("job_title") or "N/A")
    if url:
        title = f'<a href="{T.esc(url)}" target="_blank" rel="noopener" style="color:inherit">{title}</a>'

    with st.container(border=True):
        head = st.columns([4.4, 3.2], vertical_alignment="top")
        with head[0]:
            html(f'<div style="display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-bottom:5px">'
                 f'<h3 style="margin:0;font-family:{T.FONT_HEADING};font-weight:400;font-size:21px;'
                 f'line-height:1.2">{title}</h3>{T.score_pill(job.get("resume_score"))}'
                 f'{T.gate_chip(breakdown)}</div>'
                 f'<div style="font-size:14.5px;color:{T.NEUTRAL[800]}">{line}</div>')
        with head[1]:
            side = st.columns([1.7, 1.15, 1.05], vertical_alignment="center")
            with side[0]:
                html(T.stage_pill(application_view.stage_label(job),
                                  application_view.STAGE_TONE.get(application_view.stage_of(job), "wait")))
            with side[1]:
                render_update_form(job)
            with side[2]:
                if st.button("Details", key=f"applied_details_{job_id}", width="stretch",
                             help="The score breakdown and pitch this application was "
                                  "written from — useful when a reply arrives weeks later."):
                    # Held in session_state and re-opened below, for the same
                    # reason as the queue: a widget click inside a dialog reruns
                    # the script, and an inline-opened dialog vanishes mid-use.
                    st.session_state["open_applied_job"] = job_id
                    st.rerun()

        parts = [T.verdict(job_view.summary(breakdown), 15),
                 '<div style="height:14px"></div>',
                 T.timeline_html(application_view.timeline(job)),
                 '<div style="height:10px"></div>',
                 T.two_up(T.labelled_box("What I led with", application_view.led_with(job), T.BLUE[700]),
                          T.labelled_box("What they pushed back on", application_view.pushback(job),
                                         T.PURPLE[700]))]
        note = job.get("outcome_notes")
        if note:
            parts.append(f'<div style="margin-top:14px;font-size:14px;line-height:1.55;'
                         f'color:{T.NEUTRAL[800]};padding-left:14px;border-left:2px solid '
                         f'{T.NEUTRAL[400]}">{T.esc(note)}</div>')
        html("".join(parts))


def render_applications_page():
    jobs = load_applied()
    if not jobs:
        html(T.page_header("Where I applied", "Nothing sent yet."))
        st.info("No applied jobs yet.")
        return

    # Stats cover every application, not just the search results — otherwise
    # typing in the box would silently change what the totals mean.
    s = calibration.summarize(jobs)
    c = application_view.counts(jobs)
    html(T.page_header(
        "Where I applied",
        f"{s['total_applied']} sent · {c['open']} still open · "
        f"<b>{c['chase']} need chasing this week</b>"))
    html(T.stat_tiles([
        ("Applied", str(s["total_applied"]), f"{c['open']} still open"),
        ("Awaiting reply", str(s["pending"]), f"{c['chase']} of them need chasing"),
        ("Interviews", str(s["interviews"]), "of resolved applications"),
        ("Interview rate", pct(s["interview_rate"]), f"of {s['resolved']} resolved"),
        ("Offers", str(s["offers"]), f"{s['rejected']} rejected · {s['ghosted']} ghosted"),
    ]))

    render_ghost_prompt(jobs)

    controls = st.columns([3, 2], vertical_alignment="center")
    with controls[0]:
        st.session_state.setdefault("app_tab", "open")
        labels = {"open": f"Open {c['open']}", "chase": f"Needs chasing {c['chase']}",
                  "resolved": f"Resolved {c['resolved']}", "all": f"All {c['all']}"}
        tab = st.radio("Show", list(labels), key="app_tab", horizontal=True,
                       format_func=labels.get, label_visibility="collapsed")
    with controls[1]:
        search = st.text_input("Search", key="search_applied", placeholder="Title or company",
                               label_visibility="collapsed")

    shown = application_view.filter_tab(jobs, tab)
    shown = [j for j in shown if matches_search(j, search)]
    if not shown:
        # Also drop any open dialog. This path returns before the re-open block
        # at the bottom, so without this the id survives a search that hides
        # every row and the dialog springs back open when the search is cleared.
        close_applied_details()
        st.info("No applications match that search." if search
                else "Nothing in this tab.")
        return

    def sort_key(j):
        return j.get("stage_updated_at") or j.get("application_date") or ""
    shown = sorted(shown, key=sort_key, reverse=True)

    for job in shown:
        render_application_record(job)

    # An application filtered out by the search or the tab closes the dialog
    # rather than being stranded open over a row that is no longer there.
    open_id = st.session_state.get("open_applied_job")
    if open_id:
        open_job = next((j for j in shown if j.get("job_id") == open_id), None)
        if open_job:
            applied_details_dialog(open_job)
        else:
            close_applied_details()


# ═══════════════════════════════════════════════════════════════════════════
# Is the score right?
# ═══════════════════════════════════════════════════════════════════════════

def calibration_verdict(s):
    """The answer, in one banner: how far off the scorer's odds are, and why."""
    predicted, actual = s["mean_predicted"], s["interview_rate"] or 0
    gap = (predicted - actual) * 100
    if abs(gap) < 5:
        word, big = "on the money", f"{gap:+.0f}pt"
        headline = f"It promises {predicted * 100:.0f}% interview odds and delivers {actual * 100:.0f}%."
        body = "Within five points — read a stated probability as roughly what it says. "
    elif gap > 0:
        word, big = "overconfident", f"+{gap:.0f}pt"
        headline = f"It promises {predicted * 100:.0f}% interview odds and delivers {actual * 100:.0f}%."
        body = (f"Close enough to be useful, not close enough to trust to the point — read a "
                f"stated {predicted * 100:.0f}% as roughly {actual * 100:.0f}%. ")
    else:
        word, big = "underconfident", f"{gap:.0f}pt"
        headline = f"It promises {predicted * 100:.0f}% interview odds and delivers {actual * 100:.0f}%."
        body = "Better than it thinks — the postings it calls marginal are worth more than it says. "
    if s["enough_data"]:
        body += f"Based on {s['resolved']} resolved outcomes."
    else:
        body += (f"Based on {s['resolved']} resolved outcomes — the floor for a reliable read is "
                 f"{s['min_required']}, so this will move.")
    return word, big, headline, body


def render_calibration_page():
    jobs = load_applied()
    html(T.page_header("Is the score right?",
                       "Whether the scorer is telling you the truth about your odds. Applications "
                       "still waiting for a reply are excluded — no answer yet isn't a rejection."))
    if not jobs:
        st.info("No applied jobs yet. Log some outcomes on the Where I applied page first.")
        return

    s = calibration.summarize(jobs)
    if s["mean_predicted"] is None:
        st.info("No scored job carries a predicted interview probability yet, so there's "
                "nothing to calibrate against.")
    else:
        word, big, headline, body = calibration_verdict(s)
        html(T.banner(headline, body, big=big, big_note=word))

    if not s["enough_data"]:
        st.warning(
            f"Not enough resolved outcomes yet — {s['resolved']}/{s['min_required']}. "
            "The numbers below will swing wildly until you have more; treat them as a "
            "preview, not a signal."
        )

    rows = calibration.bucket_stats(jobs)
    # Best band among those with enough resolved to mean something; a band of
    # two applications at 50% is not a finding.
    rated = [r for r in rows if r["interview_rate"] is not None]
    solid = [r for r in rated if r["n"] >= 3] or rated
    best = max(solid, key=lambda r: r["interview_rate"]) if solid else None
    html(T.stat_tiles([
        ("Applied", str(s["total_applied"]), f"{s['pending']} awaiting reply"),
        ("Awaiting reply", str(s["pending"]), "excluded from every metric"),
        ("Resolved", str(s["resolved"]),
         f"{max(0, s['min_required'] - s['resolved'])} short of a reliable read"
         if not s["enough_data"] else "enough for a read"),
        ("Interview rate", pct(s["interview_rate"]), f"of {s['resolved']} resolved"),
        ("Brier score", "—" if s["brier"] is None else f"{s['brier']:.3f}",
         "always guessing 50% scores 0.25"),
        ("Best band", best["bucket"] if best else "—",
         f"{pct(best['interview_rate'])} interview rate" if best else "no resolved outcomes"),
    ]))
    if s["excluded"]:
        st.caption(f"{s['excluded']} application(s) excluded as removed/spam postings — "
                   "those never produced a real verdict, so they don't count for or "
                   "against the scorer.")

    panels = st.columns(2, gap="medium")
    with panels[0]:
        with st.container(border=True):
            html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;'
                 f'font-size:22px">Does a higher score actually help?</h3>'
                 f'<p style="margin:0 0 22px;font-size:14px;color:{T.NEUTRAL[700]};line-height:1.5">'
                 'Interview rate per score band, resolved applications only.</p>'
                 + T.bucket_bars([{"label": r["bucket"], "n": r["n"], "rate": r["interview_rate"]}
                                  for r in rows]))
            if best and best["n"]:
                html(f'<p style="margin:22px 0 0;font-size:14px;line-height:1.6;color:{T.NEUTRAL[800]};'
                     f'padding-top:18px;border-top:1px solid var(--jh-divider)">The {best["bucket"]} band '
                     f'converts best — {pct(best["interview_rate"])} of {best["n"]}. Any band with fewer '
                     'than five resolved is noise, not a ceiling.</p>')

    with panels[1]:
        with st.container(border=True):
            rejections = calibration.rejection_reason_counts(jobs)
            skips = supabase_utils.get_skip_reason_counts()
            combined = [{"label": REJECTION_LABELS.get(k, k.replace("_", " ")), "n": v,
                         "fill": T.PURPLE[500]} for k, v in rejections.items()]
            combined += [{"label": apply_queue.SKIP_REASON_LABELS.get(k, k.replace("_", " ")),
                          "n": v, "fill": T.BLUE[500]} for k, v in skips.items()]
            combined.sort(key=lambda r: -r["n"])
            top = max((r["n"] for r in combined), default=0)
            for r in combined:
                r["share"] = r["n"] / top if top else 0
            html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;'
                 f'font-size:22px">What\'s actually stopping you</h3>'
                 f'<p style="margin:0 0 22px;font-size:14px;color:{T.NEUTRAL[700]};line-height:1.5">'
                 'Rejection reasons, plus your own skip reasons — the two together are the real filter.</p>')
            if combined:
                html(T.bar_rows(combined[:8]) + T.legend([(T.PURPLE[500], "They rejected me"),
                                                          (T.BLUE[500], "I skipped it")]))
                lead = combined[0]
                html(f'<p style="margin:18px 0 0;font-size:14px;line-height:1.6;color:{T.NEUTRAL[800]}">'
                     f'<b>{T.esc(lead["label"])}</b> is the top blocker at {lead["n"]}. That is the '
                     'single highest-leverage thing to change — or to filter on in the queue.</p>')
            else:
                st.info("No rejections or skips logged yet.")


# ═══════════════════════════════════════════════════════════════════════════
# Which CV to use
# ═══════════════════════════════════════════════════════════════════════════

def render_archetypes_page():
    summary = cluster_results.load_summary()
    assignments = cluster_results.load_assignments()
    if not summary or not assignments:
        html(T.page_header("Which CV to use", "No clustering run found yet."))
        st.info(
            "Generate one with `python -m clustering.run` — it reads the scored jobs, "
            "extracts a requirement profile per posting, and writes the archetypes this "
            "page renders."
        )
        return

    clusters = sorted(summary.get("clusters", []), key=lambda c: -c["size"])
    health = cluster_results.health(summary)
    cfg = summary.get("settings", {})
    corpus = summary.get("corpus", {})
    k = len(clusters)
    html(T.page_header(
        "Which CV to use",
        f"{len(assignments)} addressable postings fall into {k} kind{'s' if k != 1 else ''} of CV. "
        f"Maintain {k} base document{'s' if k != 1 else ''}, not one generic one. "
        f'<span style="color:{T.NEUTRAL[700]}">Stability {health["stability"]:.2f} across resamples — '
        f'{T.esc(health["verdict"])}</span>'))
    st.caption(
        f"Fitted on postings scoring {cfg.get('min_score', '?')}+ with at most "
        f"{cfg.get('max_years_required', '?')} years required, out of "
        f"{corpus.get('n_scored', '?')} scored. Generated {fmt_date(summary.get('generated_at'))}."
    )
    if health["tone"] == "bad":
        st.error("Stability is too low to trust these groupings — re-run the clustering "
                 "before building CVs on them.")

    if not clusters:
        st.warning("The run produced no clusters.")
        return

    cv_fit = cluster_results.load_cv_fit()
    if cv_fit and cluster_results.fit_is_stale(cv_fit, summary):
        st.warning(
            "Your CV was scored against an older clustering run, so those numbers "
            "no longer line up with the archetypes below. Re-run "
            "`python -m clustering.cv_fit` to refresh them."
        )
        cv_fit = None
    if not cv_fit:
        st.info("Coverage is not scored yet. Run `python -m clustering.cv_fit` to see, per "
                "archetype, what your CV already covers and what is missing.")

    cols = st.columns(min(3, len(clusters)), gap="medium")
    for i, cluster in enumerate(clusters):
        with cols[i % len(cols)]:
            render_archetype_card(cluster, i, cluster_results.fit_for_cluster(cv_fit, cluster["cluster"]))

    if cv_fit:
        render_gap_panel(cv_fit)

    opened = st.session_state.get("arch_open")
    for cluster in clusters:
        if cluster["cluster"] == opened:
            with st.container(border=True):
                head = st.columns([4, 1], vertical_alignment="center")
                with head[0]:
                    html(f'<h3 style="margin:0;font-family:{T.FONT_HEADING};font-weight:400;font-size:24px">'
                         f'{T.esc(cluster["label"])} in detail</h3>')
                with head[1]:
                    if st.button("Close", key="arch_close", width="stretch"):
                        st.session_state.pop("arch_open", None)
                        st.rerun()
                render_archetype(cluster, assignments, summary,
                                 cluster_results.fit_for_cluster(cv_fit, cluster["cluster"]))

    with st.expander("How the number of archetypes was chosen"):
        st.caption(
            "Each k is refit on random subsamples and scored against the full-data "
            "fit. Stability decides, silhouette breaks ties — silhouette alone keeps "
            "rising with k and would hand you more CVs than you could maintain."
        )
        st.dataframe(
            pd.DataFrame([
                {"k": r["k"], "Silhouette": round(r["silhouette"], 3),
                 "Stability (ARI)": round(r["stability"], 3),
                 "± std": round(r["stability_std"], 3),
                 "Cluster sizes": ", ".join(str(s) for s in r["sizes"]),
                 "Chosen": "✓" if r["k"] == summary.get("chosen_k") else ""}
                for r in summary.get("k_sweep", [])
            ]),
            hide_index=True, width="stretch",
        )


def render_archetype_card(cluster, rank, fit):
    """One archetype: what it is, how much of it the CV proves, the cheapest win."""
    with st.container(border=True):
        label = "Archetype 1 · your best fit" if rank == 0 else f"Archetype {rank + 1}"
        parts = [
            T.kicker(label, T.PURPLE[700]),
            f'<h3 style="margin:0 0 6px;font-family:{T.FONT_HEADING};font-weight:400;font-size:25px;'
            f'line-height:1.15">{T.esc(cluster["label"])}</h3>',
            f'<div style="font-size:14px;color:{T.NEUTRAL[800]};margin-bottom:18px">'
            f'{cluster["size"]} postings · best score {cluster["max_score"]} · '
            f'{cluster["n_score_55_plus"]} above 55</div>',
        ]
        if fit:
            gain = fit["coverage_if_written"] - fit["coverage"]
            parts.append(
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                f'margin-bottom:8px"><span style="font-size:12px;letter-spacing:.08em;text-transform:'
                f'uppercase;color:{T.NEUTRAL[600]}">Coverage</span><span style="font-family:'
                f'{T.FONT_HEADING};font-size:20px">{pct(fit["coverage"])} <span style="font-size:14px;'
                f'color:{T.BLUE[700]}">→ {pct(fit["coverage_if_written"])}</span></span></div>'
                + T.coverage_bar(fit["coverage"], fit["coverage_if_written"])
                + f'<div style="font-size:12.5px;color:{T.NEUTRAL[700]};margin:8px 0 18px;line-height:1.5">'
                  f'Purple is what your CV proves today; blue is what it would prove after an edit — '
                  f'{"no new learning" if gain > 0 else "nothing is left unwritten"}.</div>')
            win = (fit["unwritten"] or [None])[0]
            if win:
                text = (f"Write up {win['skill']}. {pct(win['demand'])} of these postings ask for it "
                        "and your CV never mentions it.")
            elif fit["missing"]:
                gap = fit["missing"][0]
                text = (f"Build {gap['skill']} — {pct(gap['demand'])} of these postings ask for it "
                        "and nothing on the CV answers it.")
            else:
                text = "Nothing left on the table — this archetype is covered."
            parts.append(f'<div style="background:{T.BLUE[200]};border-radius:16px;padding:16px 18px;'
                         f'margin-bottom:18px">{T.kicker("Cheapest win", T.BLUE[800])}'
                         f'<div style="font-size:14.5px;line-height:1.55;color:{T.BLUE[900]}">'
                         f'{T.esc(text)}</div></div>')
        skills = [s["Skill"] for s in cluster_results.top_skills(cluster, limit=6)]
        parts.append(T.kicker("Lead with") + T.mono_chips(skills) + '<div style="height:14px"></div>')
        html("".join(parts))
        gaps_label = f"{len(fit['missing'])} gaps" if fit else "Details"
        if st.button(gaps_label, key=f"arch_open_{cluster['cluster']}", width="stretch"):
            st.session_state["arch_open"] = cluster["cluster"]
            st.rerun()


def render_gap_panel(cv_fit):
    """The gaps worth closing, across every archetype, weighted by how many ask."""
    weighted = {}
    total = sum(f["size"] for f in cv_fit.get("fits", [])) or 1
    for f in cv_fit.get("fits", []):
        for r in f["missing"]:
            w = weighted.setdefault(r["skill"], {"demand": 0.0, "kind": "build"})
            w["demand"] += r["demand"] * f["size"] / total
        for r in f["unwritten"]:
            w = weighted.setdefault(r["skill"], {"demand": 0.0, "kind": "write"})
            w["demand"] += r["demand"] * f["size"] / total
            w["kind"] = "write"
    rows = sorted(weighted.items(), key=lambda kv: -kv[1]["demand"])[:7]
    if not rows:
        return
    top = rows[0][1]["demand"] or 1
    lines = "".join(
        f'<div style="display:flex;align-items:center;gap:16px">'
        f'<div style="width:150px;flex:none;font-family:{T.FONT_MONO};font-size:13px">{T.esc(skill)}</div>'
        f'<div style="flex:1;height:22px;border-radius:999px;background:{T.BG};overflow:hidden">'
        f'<div style="height:100%;background:{T.BLUE[500] if w["kind"] == "write" else T.PURPLE[500]};'
        f'width:{max(2, round(100 * w["demand"] / top))}%"></div></div>'
        f'<div style="width:52px;flex:none;font-size:13.5px;text-align:right">{pct(w["demand"])}</div>'
        f'<div style="width:150px;flex:none;font-size:12.5px;color:{T.NEUTRAL[700]}">'
        f'{"already true — write it" if w["kind"] == "write" else "build it"}</div></div>'
        for skill, w in rows)
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 6px;font-family:{T.FONT_HEADING};font-weight:400;font-size:22px">'
             f'The {len(rows)} gaps worth closing</h3>'
             f'<p style="margin:0 0 22px;font-size:14px;color:{T.NEUTRAL[700]};line-height:1.5">Across '
             'every archetype, weighted by how many postings ask. Blue ones are true of you already '
             'and only need writing down.</p>'
             f'<div style="display:flex;flex-direction:column;gap:11px;margin-bottom:6px">{lines}</div>')


def render_archetype(cluster, assignments, summary, fit=None):
    """One archetype in depth: who it is, what it wants, and the jobs it covers."""
    if fit:
        render_cv_gap(fit)

    st.subheader("Skills to lead with")
    st.caption("Ranked by lift — how over-represented a skill is here compared with "
               "the whole corpus. A skill everyone wants says nothing about which CV "
               "this is; a rare one says everything.")
    skills = cluster_results.top_skills(cluster)
    if skills:
        st.dataframe(
            pd.DataFrame([
                {"Skill": s["Skill"],
                 "Share of cluster": pct(s["Share of cluster"]),
                 "Lift vs corpus": f"{s['Lift vs corpus']:.1f}x"}
                for s in skills
            ]),
            hide_index=True, width="stretch",
        )

    st.subheader("What these roles are")
    st.dataframe(pd.DataFrame(cluster_results.context_rows(cluster)),
                 hide_index=True, width="stretch")
    st.caption(f"Median years of experience required: "
               f"{cluster.get('median_years_required', 0):.0f}")

    requirements = cluster.get("common_requirements") or []
    if requirements:
        st.subheader("Requirements these postings lead with")
        st.caption("Phrases repeated across postings in this cluster. Anything named "
                   "once is one employer's wording, so only repeats are shown.")
        for phrase, count in requirements:
            st.markdown(f"- {phrase} — *{count} postings*")

    st.subheader("Jobs in this archetype")
    confident_only = st.checkbox(
        "Hide postings sitting between two archetypes", value=False,
        key=f"conf_{cluster['cluster']}",
        help=f"Assignment margin below {summary.get('ambiguous_margin', 0.15)} — these "
             f"could plausibly belong to another archetype, so decide them by eye.",
    )
    jobs = cluster_results.jobs_in_cluster(
        assignments, cluster["cluster"], confident_only=confident_only
    )
    st.dataframe(
        pd.DataFrame([
            {"Score": j["score"], "Title": j["title"], "Company": j["company"],
             "Seniority": j["seniority"], "German": j["german_required"],
             "Confident": "yes" if j.get("confident") == "yes" else "borderline",
             "Link": j.get("job_url") or ""}
            for j in jobs
        ]),
        hide_index=True, width="stretch",
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")},
    )

    with st.expander("Most representative postings"):
        st.caption("Closest to the centroid — these read as the archetype itself "
                   "rather than as its edge cases.")
        for e in cluster.get("exemplars", []):
            st.markdown(f"**{e['title']}** — {e['company']} · {e['score']}/100  \n{e['summary']}")


def render_cv_gap(fit):
    """What your CV has and what it does not, for one archetype.

    Three buckets rather than a single match score, because they need different
    responses: covered is nothing to do, unwritten is an edit, missing is either
    a project to build or an archetype to drop.
    """
    gain = fit["coverage_if_written"] - fit["coverage"]
    html(T.stat_tiles([
        ("Coverage", pct(fit["coverage"]), "of this archetype's weighted demand"),
        ("If written up", pct(fit["coverage_if_written"]),
         f"+{gain * 100:.0f} pts from editing alone" if gain > 0 else "nothing unwritten"),
        ("Real gaps", str(len(fit["missing"])), f"of {fit['n_demanded']} skills demanded"),
    ]))

    have, edit, gap = st.tabs([
        f"Have it ({len(fit['covered'])})",
        f"True but not on the CV ({len(fit['unwritten'])})",
        f"Missing ({len(fit['missing'])})",
    ])

    with have:
        if not fit["covered"]:
            st.info("Nothing this archetype demands is currently evidenced on your CV.")
        else:
            st.dataframe(
                pd.DataFrame([
                    {"Skill": r["skill"], "Asked by": pct(r["demand"]),
                     "Evidence tier": r["tier"], "Backed by": r["evidence"]}
                    for r in fit["covered"]
                ]),
                hide_index=True, width="stretch",
            )
            st.caption("Tier 1 = shipped with a metric · 2 = built and working · "
                       "3 = coursework or listed only. Lead with tier 1.")

    with edit:
        if not fit["unwritten"]:
            st.success("Nothing is being left on the table — everything true of you "
                       "that this archetype wants is already written down.")
        else:
            st.caption("The cheapest points available: true of you per your profile "
                       "notes, but a reader of the CV would never know.")
            for r in fit["unwritten"]:
                st.markdown(f"- **{r['skill']}** — asked by {pct(r['demand'])} of these postings")

    with gap:
        if not fit["missing"]:
            st.success("No demanded skill is missing.")
        else:
            st.caption("Ranked by how many postings in this archetype ask for it. "
                       "The top of this list is what to build next — or the reason "
                       "to deprioritise this archetype.")
            st.dataframe(
                pd.DataFrame([
                    {"Skill": r["skill"], "Asked by": pct(r["demand"])}
                    for r in fit["missing"]
                ]),
                hide_index=True, width="stretch",
            )

    if fit["off_target"]:
        with st.expander(f"On your CV but not wanted here ({len(fit['off_target'])})"):
            st.caption("Not a weakness — page space this archetype would not reward. "
                       "Worth cutting from this version of the CV to make room.")
            st.markdown(", ".join(f"`{r['skill']}`" for r in fit["off_target"]))


# ═══════════════════════════════════════════════════════════════════════════
# Write my CV
# ═══════════════════════════════════════════════════════════════════════════

def close_interview(job_id, questions):
    """Drop the answered questions and the text typed into them.

    The answer widgets are keyed per job and question, so their contents outlive
    the form unless they are cleared — reopening the gap check would show the
    previous answers already filled in, which reads as though they had not been
    saved.
    """
    st.session_state.pop(f"gaps_{job_id}", None)
    for q in questions:
        st.session_state.pop(f"a_{job_id}_{q.id}", None)


def tailor_steps(job_id, interview, saved):
    """Where the three steps stand for this posting."""
    gaps_done = st.session_state.get(f"gaps_done_{job_id}") or \
        (interview is not None and not interview.questions)
    rounds = (saved or {}).get("rounds") or []
    if saved and saved.get("awaiting_answers"):
        # A paused run is not finished, and saying "done" next to a draft that is
        # waiting on you is how a pause gets missed entirely.
        step1 = {"state": "done", "note": "answered" if gaps_done else "skipped or answered earlier"}
        step2 = {"state": "current", "note": f"{len(rounds)} round"
                                             f"{'s' if len(rounds) != 1 else ''} · "
                                             "paused for your answers"}
        step3 = {"state": "todo", "note": "after the loop"}
    elif saved:
        step1 = {"state": "done", "note": "answered" if gaps_done else "skipped or answered earlier"}
        step2 = {"state": "done", "note": f"{len(rounds)} round{'s' if len(rounds) != 1 else ''} · "
                                          + ("no new objections" if not saved.get("judge_failed")
                                             else "last review failed")}
        step3 = {"state": "current", "note": "you are here"}
    elif gaps_done:
        step1 = {"state": "done", "note": "facts saved"}
        step2 = {"state": "current", "note": "you are here"}
        step3 = {"state": "todo", "note": "after the loop"}
    else:
        step1 = {"state": "current", "note": "you are here"}
        step2 = {"state": "todo", "note": "a few minutes of LLM calls"}
        step3 = {"state": "todo", "note": "after the loop"}
    return [dict(n="1", label="Fill the gaps", **step1),
            dict(n="2", label="Write and argue", **step2),
            dict(n="3", label="Read and send", **step3)]


def render_generate_cv_page():
    # status='new' already excludes anything applied or dismissed.
    queue = load_queue()
    if not queue:
        html(T.page_header("Write my CV", "Nothing in the queue to write for."))
        st.info("No unapplied scored jobs in the queue.")
        return

    labels = {
        f"{j.get('resume_score')} · {j.get('job_title')} — {j.get('company')}": j
        for j in apply_queue.sort_jobs(queue, "score")
    }
    options = list(labels)

    # A "Tailor CV" button in the queue lands here with a job already chosen.
    # Consumed rather than read, so returning to this page later does not keep
    # dragging the selection back to whatever was picked days ago.
    requested = st.session_state.pop("tailor_job_id", None)
    if requested:
        match = next((label for label in options if labels[label].get("job_id") == requested), None)
        if match is None:
            st.warning("That posting is no longer in the queue — it may have been "
                       "applied to, skipped, or closed. Pick another one.")
        else:
            st.session_state["tailor_choice"] = match
    if st.session_state.get("tailor_choice") not in labels:
        st.session_state["tailor_choice"] = options[0]

    head = st.columns([3.4, 1.2], vertical_alignment="top")
    with head[0]:
        html(T.kicker("Writing a CV for", T.PURPLE[700]))
        choice = st.selectbox("Job posting", options, key="tailor_choice",
                              label_visibility="collapsed")
    with head[1]:
        if st.button("← Back to the jobs", key="tailor_back", width="stretch"):
            go("queue")
            st.rerun()

    # The queue rows carry no description; the writer and the interview both need
    # one, so the full row is fetched only for the posting actually chosen.
    job = supabase_utils.get_job_with_description(labels[choice]["job_id"])
    if not job:
        st.error("Could not load that posting.")
        return
    job_id = job["job_id"]
    link = (f' · <a href="{T.esc(job["job_url"])}" target="_blank" rel="noopener">open posting ↗</a>'
            if job.get("job_url") else "")
    html(f'<h1 style="margin:0 0 6px;font-family:{T.FONT_HEADING};font-weight:400;font-size:40px;'
         f'line-height:1.1">{T.esc(job.get("job_title") or "N/A")}</h1>'
         f'<p style="margin:0 0 18px;font-size:16px;color:{T.NEUTRAL[800]}">'
         f'{T.esc(job.get("company") or "N/A")} · score {T.esc(job.get("resume_score"))}{link}</p>')
    if len(job.get("description") or "") < 300:
        st.warning("This posting has almost no description stored, so the tailoring "
                   "has little to work from.")

    try:
        base = tailor_facts.ensure_seeded()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not build the fact base: {exc}")
        return

    interview = st.session_state.get(f"gaps_{job_id}")
    saved = tailor_store.load(job_id)
    html(T.step_bar(tailor_steps(job_id, interview, saved)) + '<div style="height:18px"></div>')

    left, right = st.columns([1, 1], gap="medium")
    with left:
        render_scorer_leads(base)
        render_gap_step(job, base, interview)
        render_generate_step(job, base, saved)
        if saved:
            # Above the round history on purpose: a paused run is waiting on you,
            # and the questions are the only thing on this page that is.
            render_probe_step(job, base, saved)
            render_rounds(saved)
    with right:
        if saved:
            render_generated_application(saved)
        else:
            with st.container(border=True):
                html(T.kicker("The result")
                     + f'<div style="font-size:14.5px;line-height:1.6;color:{T.NEUTRAL[800]}">Nothing '
                       'written for this posting yet. Fill the gaps on the left, then generate — you '
                       'get a CV and an Anschreiben written only from facts about you, argued over '
                       'by a hiring manager until the objections run out.</div>')
                citable = base.citable()
                st.caption(f"Fact base: {len(citable)} facts "
                           f"({sum(1 for f in citable if f.tier == 4)} true but not yet on your CV). "
                           "Every line of the generated CV cites these.")


def harvest_leads_once(base):
    """Pull in what the scorer noticed, once per session.

    Scoring runs in GitHub Actions, where `profile_facts.json` does not exist —
    it is personal and gitignored — so the leads from those runs cannot be
    written there and are read back from the stored breakdowns here instead.

    Once per session, not once per rerun: this page reruns on every keystroke in
    a text area, and a Supabase read per keystroke is not a feature.
    """
    if st.session_state.get("scorer_leads_harvested"):
        return
    st.session_state["scorer_leads_harvested"] = True
    try:
        counts = tailor_harvest.harvest_from_supabase(base)
        if counts["added"] or counts["merged"]:
            tailor_facts.save(base)
    except Exception as exc:  # noqa: BLE001 - never block the page on this
        logging.warning("Could not harvest scorer leads: %s", exc)


def render_scorer_leads(base):
    """What the scorer keeps noticing across the queue, put back to you as questions.

    These are leads, not facts: the scorer read your CV against a posting and
    concluded the CV does not show something, which is not the same as knowing
    whether you have done it. So nothing here is citable until you answer it —
    the writer cannot see these and the verifier rejects any line that cites one.
    Answering is what converts a lead into a fact, in your words.
    """
    harvest_leads_once(base)
    leads = base.unconfirmed()
    if not leads:
        return
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;font-size:19px">'
             'What the scorer keeps noticing</h3>'
             f'<p style="margin:0 0 12px;font-size:14px;line-height:1.55;color:{T.NEUTRAL[700]}">Gaps '
             'the scorer flagged while working through your queue, most-seen first — the ones where it '
             'thought the substance was probably there and your CV just does not show it. It cannot '
             'know whether you have done these; only you can. Nothing here reaches a CV until you '
             'answer it, and an answer is kept for every future application.</p>')
        with st.form("scorer_leads"):
            replies, dismissed = {}, {}
            for lead in leads[:8]:
                seen = len(lead.seen_in)
                html(f'<div style="display:flex;align-items:center;gap:9px;margin:10px 0 4px">'
                     f'<span style="font-size:14.5px;font-weight:500">{T.esc(lead.claim)}</span>'
                     f'<span style="font-size:12px;padding:3px 9px;border-radius:999px;'
                     f'background:{T.PURPLE[200]};color:{T.PURPLE[800]};white-space:nowrap">'
                     f'{seen} posting{"s" if seen != 1 else ""}</span></div>')
                if lead.context:
                    st.caption(lead.context)
                replies[lead.id] = st.text_area(
                    "Have you done this?", key=f"lead_{lead.id}", label_visibility="collapsed",
                    placeholder="Where you did it, roughly when, any number attached. "
                                "Leave blank if you haven't — that is a perfectly good answer.",
                )
                dismissed[lead.id] = st.checkbox(
                    "Never ask me this again", key=f"drop_{lead.id}",
                    help="Drops the lead. Use it for gaps that are simply not true of "
                         "you — it stops the scorer re-raising them every run.")
            if st.form_submit_button("Save what's true", type="primary"):
                added, dropped = [], 0
                with st.spinner("Turning your answers into facts…"):
                    for lead in leads[:8]:
                        answer = (replies.get(lead.id) or "").strip()
                        if answer:
                            # Through text_to_facts rather than confirming the lead
                            # in place: the lead is the scorer's sentence about an
                            # absence, and what belongs in the base is your sentence
                            # about what you did — split into atomic facts, in your
                            # own words, with the tier your answer actually supports.
                            added += tailor_interview.text_to_facts(answer, base)
                            base.drop(lead.id)
                        elif dismissed.get(lead.id):
                            dropped += base.drop(lead.id)
                if added or dropped:
                    tailor_facts.save(base)
                for lead in leads[:8]:
                    st.session_state.pop(f"lead_{lead.id}", None)
                    st.session_state.pop(f"drop_{lead.id}", None)
                flash_saved(
                    f"Added {len(added)} fact(s)"
                    + (f", dropped {dropped} lead(s)" if dropped else "")
                    + "." if (added or dropped) else "Nothing to save — left as they were."
                )
                st.rerun()
        if len(leads) > 8:
            st.caption(f"{len(leads) - 8} more below the fold — answer these and the rest "
                       "move up. They are ranked by how often the scorer hit them.")


def render_gap_step(job, base, interview):
    """Step 1: what the posting asks for that the fact base cannot answer."""
    job_id = job["job_id"]
    gap_key = f"gaps_{job_id}"
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;font-size:19px">'
             '1 · Fill the gaps</h3>'
             f'<p style="margin:0 0 12px;font-size:14px;line-height:1.55;color:{T.NEUTRAL[700]}">Before '
             'writing, check what this posting asks for that your fact base cannot answer. Anything '
             'you add here is saved and helps every future application, not just this one.</p>')
        if st.button("Find what's missing", key="find_gaps", type="primary"):
            with st.spinner("Reading the posting against your fact base…"):
                st.session_state[gap_key] = tailor_interview.find_gaps(job, base)
            st.rerun()

        if interview is not None:
            if interview.already_covered:
                st.success("Already covered: " + ", ".join(interview.already_covered))
            if not interview.questions:
                st.info("Nothing to ask — your fact base answers what this posting requires.")
            else:
                with st.form(f"answers_{job_id}"):
                    answers = {}
                    for q in interview.questions:
                        st.markdown(f"**{q.question}**")
                        st.caption(f"{q.requirement} · {q.why_it_matters} · my guess: {q.likely}")
                        answers[q.id] = st.text_area(
                            "Your answer", key=f"a_{job_id}_{q.id}",
                            label_visibility="collapsed",
                            placeholder="Leave blank if you haven't done this — a no costs nothing.",
                        )
                    if st.form_submit_button("Save answers to my profile", type="primary"):
                        with st.spinner("Turning your answers into facts…"):
                            new_ids = tailor_interview.answers_to_facts(
                                interview.questions, answers, base)
                        if new_ids:
                            tailor_facts.save(base)
                        # Close the form either way. The questions have been answered;
                        # leaving them on screen invites answering them twice, and a
                        # blank answer is a "no" that is not worth asking again.
                        close_interview(job_id, interview.questions)
                        st.session_state[f"gaps_done_{job_id}"] = True
                        flash_saved(
                            f"Added {len(new_ids)} fact(s) to your profile."
                            if new_ids else
                            "Saved — nothing new to add from those answers."
                        )
                        st.rerun()

        with st.expander("Add something your CV doesn't say"):
            st.caption(
                "The gap check only asks about what *this posting* demands, so work "
                "that is real and relevant but that no posting happens to name never "
                "gets picked up — a thesis, a side project, a tool you use daily. "
                "Nothing here is posting-specific: what you add is available to every "
                "application afterwards. Write it as you would say it."
            )
            with st.form(f"freeform_{job_id}"):
                told = st.text_area(
                    "What did you do?", height=120, label_visibility="collapsed",
                    placeholder="e.g. My Master's thesis at Daimler Buses, Sept 2026 – "
                                "Feb 2027, 30h/week — what it's on, what you've built so "
                                "far, any numbers.",
                )
                if st.form_submit_button("Add to my profile"):
                    with st.spinner("Turning that into facts…"):
                        new_ids = tailor_interview.text_to_facts(told, base)
                    if new_ids:
                        tailor_facts.save(base)
                        flash_saved(f"Added {len(new_ids)} fact(s) to your profile.")
                        st.rerun()
                    else:
                        st.info("Nothing checkable in that — try naming what you built, "
                                "where, and any numbers attached to it.")


def render_generate_step(job, base, saved):
    """Step 2: the loop's knobs and the button that spends money."""
    job_id = job["job_id"]
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;font-size:19px">'
             '2 · Write and argue</h3>'
             f'<p style="margin:0 0 12px;font-size:14px;line-height:1.55;color:{T.NEUTRAL[700]}">Each '
             'round is one rewrite plus one hiring-manager review. The loop stops early once a round '
             'raises nothing new.</p>')
        # Both keyed. Without a key a widget resets to its default on every rerun,
        # and this page reruns constantly — finding gaps, saving an answer, opening
        # the fact base. Dragging the slider to 4 and then touching anything else
        # snapped it back to 2, which read as the slider being stuck.
        rounds = st.slider("Rounds of review", 1, 5, tailor_settings.MAX_ROUNDS,
                           key="tailor_rounds",
                           help="Past round two the models largely converge on each other, "
                                "so 3-4 buys less than it costs.")
        probe = st.checkbox(
            "Ask me questions between rounds", value=True, key="tailor_probe",
            help="After a round, the objections that need evidence your fact base does "
                 "not hold are put back to you as questions. No rewrite can answer "
                 "those — the writer may only use facts it can cite — so without this "
                 "they survive every remaining round. The run pauses until you answer, "
                 "and what you add is kept for every future application.")
        score_it = st.checkbox(
            "Score old vs new afterwards", value=True, key="tailor_score_it",
            help="Scores your current CV and the tailored one head to head, once, after "
                 "the loop and never inside it — feeding a score back into the writer "
                 "would make it optimise against the same model that measures it.")

        label = "Generate again" if saved else "Generate CV and cover letter"
        if st.button(label, key="generate_cv", type="primary" if not saved else "secondary"):
            run_generation(job, base, rounds, score_it, probe=probe)

        # Acted on here rather than inside the result renderer, because continuing
        # needs the job, the fact base and the personal details, and the renderer is
        # deliberately given only the saved payload.
        request = st.session_state.pop("refine_request", None)
        if request and saved and request[0] == job_id:
            resume = tailor_loop.continuation_from(saved)
            if resume is None:
                st.error("That saved draft could not be reloaded, so it cannot be "
                         "continued. Generate it again.")
            else:
                run_generation(job, base, request[1], score_it, resume=resume,
                               prior_rounds=saved.get("rounds") or [], probe=probe)


def run_generation(job, base, rounds, score_it, resume=None, prior_rounds=None,
                   probe=False):
    """Drive the loop, streaming progress, then persist the result."""
    status = st.empty()
    with st.spinner("Writing…"):
        result = tailor_loop.run(
            # int(): the slider reports a float in some Streamlit builds, and
            # range() will not take one.
            job, base, tailor_store.personal_details(), max_rounds=int(rounds),
            progress=lambda msg: status.caption(msg), resume=resume, probe=probe,
        )
    status.empty()

    if result.application is None:
        st.error(result.stopped_because or "Nothing was produced.")
        return

    # Scoring a paused run would price a draft that is about to be rewritten with
    # facts it has not seen, and it is the expensive half of the whole page.
    if score_it and not result.awaiting_answers:
        with st.spinner("Scoring the original and the tailored CV head to head…"):
            result.score_before, result.score_after, result.score_note = (
                tailor_loop.holdout_compare(
                    job, tailor_documents.render_cv(result.application.cv)))

    path = tailor_store.save(job, result, prior_rounds=prior_rounds)
    flash_saved(f"Saved to {os.path.basename(path)}")
    st.rerun()


def render_probe_step(job, base, saved):
    """The questions a paused run is waiting on, and the button that resumes it.

    This is the only place in the loop where information enters rather than being
    rearranged, which is why it gets its own panel rather than a line in the round
    history. A blank answer is a "no" and costs nothing: the facts are what keep
    every generated CV citable, so an unanswered question is strictly better than
    a guessed one.
    """
    if not saved.get("awaiting_answers") or not saved.get("questions"):
        return
    job_id = job["job_id"]
    questions = [tailor_interview.Question.model_validate(q) for q in saved["questions"]]
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;font-size:19px">'
             'The hiring manager has questions for you</h3>'
             f'<p style="margin:0 0 12px;font-size:14px;line-height:1.55;color:{T.NEUTRAL[700]}">The '
             'run is paused. These objections ask for evidence your fact base does not hold, so no '
             'rewrite can answer them — the writer may only use facts it can cite. Answer what you '
             'can and the next round uses it; leave the rest blank, a no costs nothing.</p>')
        with st.form(f"probe_{job_id}"):
            answers = {}
            for q in questions:
                st.markdown(f"**{q.question}**")
                st.caption(f"{q.requirement} · {q.why_it_matters} · my guess: {q.likely}")
                answers[q.id] = st.text_area(
                    "Your answer", key=f"p_{job_id}_{q.id}", label_visibility="collapsed",
                    placeholder="Leave blank if you haven't done this — a no costs nothing.",
                )
            extra = st.selectbox(
                "Rounds to run after this", [1, 2], key=f"probe_rounds_{job_id}",
                format_func=lambda n: f"then run {n} more round" + ("s" if n > 1 else ""),
            )
            if st.form_submit_button("Save answers and carry on", type="primary"):
                with st.spinner("Turning your answers into facts…"):
                    new_ids = tailor_interview.answers_to_facts(questions, answers, base)
                if new_ids:
                    tailor_facts.save(base)
                for q in questions:
                    st.session_state.pop(f"p_{job_id}_{q.id}", None)
                # Continuing goes through the same path as "add a round", so the
                # draft you have already read is revised rather than rewritten
                # from scratch — now against a fact base that answers more.
                st.session_state["refine_request"] = (job_id, int(extra))
                flash_saved(
                    f"Added {len(new_ids)} fact(s) — carrying on with those."
                    if new_ids else
                    "Nothing new to add from those answers — carrying on regardless."
                )
                st.rerun()


def render_rounds(saved):
    """How it got here: each round's objections and repairs."""
    rounds = saved.get("rounds") or []
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 14px;font-family:{T.FONT_HEADING};font-weight:400;font-size:19px">'
             'How it got here</h3>')
        if saved.get("structural"):
            st.warning("**Gaps no rewrite can close** — these are real, and worth "
                       "knowing before you spend an evening on this application:\n\n"
                       + "\n".join(f"- {s}" for s in saved["structural"]))
        for rnd in rounds:
            tag = (f"{len(rnd['new_objections'])} objection{'s' if len(rnd['new_objections']) != 1 else ''}"
                   + (f", {rnd['repairs']} repair{'s' if rnd['repairs'] != 1 else ''}" if rnd["repairs"] else ""))
            if not rnd["new_objections"]:
                tag = "nothing new"
            tone = (T.BLUE[200], T.BLUE[900]) if not rnd["new_objections"] else (T.PURPLE[200], T.PURPLE[800])
            with st.container(border=True):
                html(f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:7px">'
                     f'<span style="font-family:{T.FONT_HEADING};font-size:14px">Round {rnd["number"]}</span>'
                     f'<span style="font-size:12px;padding:3px 10px;border-radius:999px;background:{tone[0]};'
                     f'color:{tone[1]}">{T.esc(tag)}</span></div>')
                if not rnd["verifier_ok"]:
                    st.error("Mechanical checks still failing:\n\n" + rnd.get("verifier_problems", ""))
                else:
                    st.markdown("Every claim cites a fact, every number checks out, no banned phrasing.")
                if rnd.get("standout"):
                    st.markdown(f"**Strongest point per the judge:** {rnd['standout']}")
                for objection in rnd["new_objections"]:
                    st.markdown(f"- {objection}")
                if rnd.get("ai_tells"):
                    st.caption("Lines flagged as machine-written: "
                               + "; ".join(f'"{t}"' for t in rnd["ai_tells"]))


def render_generated_application(saved):
    """The finished CV and letter, with the head-to-head score above them."""
    rounds = saved.get("rounds") or []
    job_id = saved["job"]["job_id"]
    with st.container(border=True):
        html(f'<h3 style="margin:0 0 5px;font-family:{T.FONT_HEADING};font-weight:400;font-size:22px">'
             'The result</h3>'
             f'<div style="font-size:13.5px;color:{T.NEUTRAL[700]};margin-bottom:16px">Generated '
             f'{fmt_date(saved.get("generated_at"))} · survived {len(rounds)} review '
             f'round{"s" if len(rounds) != 1 else ""} · {T.esc(saved.get("stopped_because", ""))}</div>')
        if saved.get("judge_failed"):
            # Distinguish "nothing was ever reviewed" from "the last rewrite was not
            # reviewed". The blanket first-draft wording was wrong whenever earlier
            # rounds had succeeded — it told you no objections were raised on a run
            # that had already produced nineteen of them.
            reviewed = [r for r in rounds if r.get("would_interview")]
            if reviewed:
                st.warning(
                    f"**The last review didn't land.** {len(reviewed)} of {len(rounds)} "
                    "rounds were reviewed, but the draft below is the final rewrite and "
                    "no hiring-manager pass has read *this* version. Add a round to have "
                    "it read."
                )
            else:
                st.error(
                    "**This draft was never reviewed.** The hiring-manager pass failed on "
                    "every attempt, so nothing was revised and no objections were raised. "
                    "Add a round, or regenerate."
                )

        before, after = saved.get("score_before"), saved.get("score_after")
        if after is not None:
            delta = f" ↑{after - before}" if before is not None and after > before else \
                (f" ↓{before - after}" if before is not None and after < before else "")
            verdict = (rounds[-1].get("would_interview") or "—") if rounds else "—"
            html(f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:12px">'
                 f'<div style="flex:1;min-width:120px;background:{T.BG};border-radius:16px;padding:16px 18px">'
                 f'{T.kicker("Your CV")}<div style="font-family:{T.FONT_HEADING};font-size:30px;line-height:1">'
                 f'{"—" if before is None else before}</div></div>'
                 f'<div style="flex:1;min-width:120px;background:{T.PURPLE[200]};border-radius:16px;padding:16px 18px">'
                 f'{T.kicker("Tailored", T.PURPLE[800])}<div style="font-family:{T.FONT_HEADING};font-size:30px;'
                 f'line-height:1;color:{T.PURPLE[900]}">{after}<span style="font-size:15px">{delta}</span></div></div>'
                 f'<div style="flex:1;min-width:120px;background:{T.BLUE[200]};border-radius:16px;padding:16px 18px">'
                 f'{T.kicker("Would interview", T.BLUE[800])}<div style="font-family:{T.FONT_HEADING};font-size:30px;'
                 f'line-height:1;color:{T.BLUE[900]}">{T.esc(verdict)}</div></div></div>'
                 f'<div style="font-size:13px;line-height:1.55;color:{T.NEUTRAL[700]};margin-bottom:14px">'
                 f'{T.esc(saved.get("score_note") or "Both CVs scored against this posting in one holdout call, after the loop finished — never fed back into the writer.")}</div>')

        cv_tab, letter_tab = st.tabs(["CV", "Anschreiben"])
        with cv_tab:
            st.code(saved.get("cv_text", ""), language=None, wrap_lines=True)
            st.download_button("Download CV", saved.get("cv_text", ""),
                               file_name=f"CV_{saved['job']['company']}.txt", type="primary")
        with letter_tab:
            st.code(saved.get("cover_letter_text", ""), language=None, wrap_lines=True)
            st.download_button("Download Anschreiben", saved.get("cover_letter_text", ""),
                               file_name=f"Anschreiben_{saved['job']['company']}.txt", type="primary")

        if saved.get("application"):
            cols = st.columns([1, 1.3], vertical_alignment="center")
            with cols[0]:
                extra = st.selectbox(
                    "Add rounds", [1, 2], key=f"extra_rounds_{job_id}",
                    label_visibility="collapsed",
                    format_func=lambda n: f"{n} more round" + ("s" if n > 1 else ""),
                )
            with cols[1]:
                more = st.button("Add a review round", key=f"refine_{job_id}", width="stretch")
            st.caption("Continues from this draft against the objections still open, "
                       "rather than starting again — the document you have read stays "
                       "recognisable and you only pay for the extra rounds.")
            if more:
                st.session_state["refine_request"] = (job_id, int(extra))
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════

def main():
    html(T.CSS)

    # Consumed here, not per page, so a save made on one page still confirms
    # even though the rerun that follows it may land somewhere else.
    saved_message = consume_flash()
    if saved_message:
        st.toast(saved_message, icon="✅")
    failure = consume_failure()
    if failure:
        # Both: the toast for the eye, the banner so it is still there when
        # you look for it. A toast alone is gone in four seconds.
        st.toast(failure, icon="❌")
        st.error(failure)

    st.session_state.setdefault("page", DEFAULT_PAGE)
    # Every page, every run - see init_filters for why it cannot live on the
    # queue page alone.
    init_filters()
    render_sidebar()

    page = st.session_state["page"]
    if page == "queue":
        render_queue_page()
    elif page == "apps":
        render_applications_page()
    elif page == "cal":
        render_calibration_page()
    elif page == "arch":
        render_archetypes_page()
    else:
        render_generate_cv_page()


if __name__ == "__main__":
    main()
