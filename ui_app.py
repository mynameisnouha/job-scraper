"""
Streamlit UI for reviewing jobs and logging application outcomes without
touching the Supabase table editor or the static dashboard.html.

Run with: streamlit run ui_app.py
"""
from datetime import datetime

import pandas as pd
import streamlit as st

from review import apply_queue
from review import calibration
from review import application_pack
from review import job_view
from db import supabase_utils

# Categorical slot 1 from the validated reference palette, stepped per mode.
# Single-series charts only — magnitude, so one hue, never a rainbow.
SERIES_LIGHT = "#2a78d6"
SERIES_DARK = "#3987e5"

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

st.set_page_config(page_title="Job Scraper", layout="wide")


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


def series_color():
    """The single series hue, stepped for whichever theme the viewer is using."""
    try:
        if st.get_option("theme.base") == "dark":
            return SERIES_DARK
    except Exception:
        pass
    return SERIES_LIGHT


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
    "Found …" for a job, or None when the row carries no scrape timestamp.

    Under 24 hours old this carries the clock time as well as the elapsed hours,
    because being early to a posting is most of the advantage.
    """
    found = apply_queue.format_found(job)
    return f"Found {found}" if found else None


def score_badge(score):
    if score is None:
        return "unscored"
    if score >= 75:
        return f":green[{score}/100]"
    if score >= 50:
        return f":orange[{score}/100]"
    return f":red[{score}/100]"


def render_today_page():
    st.header("Jobs to Apply")
    jobs = supabase_utils.get_top_scored_jobs_to_apply(999)
    if not jobs:
        st.info("No scored jobs ready for application right now.")
        return

    search = st.text_input("Search", key="search_jobs",
                           placeholder="Filter by job title or company…")

    controls = st.columns([1.3, 1.5, 1.5, 1.6])
    with controls[0]:
        date_window = st.selectbox("Found within", apply_queue.DATE_WINDOW_KEYS,
                                   key="date_window",
                                   format_func=lambda w: apply_queue.DATE_WINDOW_LABELS[w],
                                   help="A posting's value decays fast — the first "
                                        "applicants are read first. Jobs with no scrape "
                                        "timestamp only show under 'Any time'.")
    with controls[1]:
        min_score = st.slider("Min score", 0, 100, 70, step=5)
    with controls[2]:
        sort_by = st.radio("Sort by", apply_queue.SORT_MODES, horizontal=True,
                           key="sort_mode",
                           format_func=lambda m: "Score" if m == "score" else "Least effort",
                           help="Least effort sorts by the scorer's estimated hours, "
                                "so a short evening can be spent on applications that fit in it.")
    with controls[3]:
        focus = st.checkbox("Focus mode", value=True, key="focus_mode",
                            help="One job at a time, keyboard-driven. Uncheck for the full list.")

    total = len(jobs)
    jobs = [j for j in jobs if apply_queue.within_window(j, date_window)]
    jobs = [j for j in jobs if (j.get("resume_score") or 0) >= min_score]
    jobs = [j for j in jobs if matches_search(j, search)]
    jobs = apply_queue.sort_jobs(jobs, sort_by)

    if not jobs:
        st.info("No jobs match these filters. Try clearing the search, widening "
                "'Found within', or lowering the min score.")
        return

    # Changing the filters or the sort is an explicit "re-shuffle the queue",
    # so the cursor goes back to the top. Only an incidental refresh — a scrape
    # run landing, a job leaving — keeps your place.
    signature = (sort_by, date_window, min_score, (search or "").strip().lower())
    if st.session_state.get("queue_signature") != signature:
        st.session_state["queue_signature"] = signature
        st.session_state["cursor_job"] = None
        st.session_state["cursor_idx"] = 0

    if focus:
        render_focus_queue(jobs, total)
        visible = jobs
    else:
        show_n = st.number_input("Max shown", min_value=5, max_value=200, value=25, step=5)
        matched = len(jobs)
        visible = jobs[: int(show_n)]
        st.caption(f"Showing {len(visible)} of {matched} matching ({total} scored jobs total). "
                   "Rendering every job at once is slow — narrow with the filters above.")
        for job in visible:
            render_job_card(job)

    # Re-opened on every rerun so widgets inside the dialog keep working.
    # A job filtered out of the list closes it rather than stranding it open.
    open_job_id = st.session_state.get("open_job")
    if open_job_id:
        open_job = next((j for j in visible if j.get("job_id") == open_job_id), None)
        if open_job:
            job_details_dialog(open_job)
        else:
            close_details()


REC_LABELS = {
    "apply_now": ":green-badge[Apply now]",
    "apply_after_fixes": ":blue-badge[Apply after fixes]",
    "apply_if_gate_negotiable": ":orange-badge[If gate negotiable]",
    "skip": ":red-badge[Skip]",
}


def mark_applied(job):
    """Shared by the overview card and the detail dialog."""
    job_id = job.get("job_id")
    title = job.get("job_title") or job_id
    if supabase_utils.mark_job_applied(job_id):
        supabase_utils.update_application_stage(job_id, "applied")
        flash_saved(f"Marked applied: {title}")
        st.rerun()
    else:
        st.error("Failed to mark applied — check logs.")


def skip_job(job, reason):
    """
    Take a job out of the queue without applying. Soft — the row survives, and
    the reason is the label that makes the skip worth something later.
    """
    job_id = job.get("job_id")
    title = job.get("job_title") or job_id
    if supabase_utils.dismiss_job(job_id, reason):
        st.session_state["last_skipped"] = {"job_id": job_id, "title": title}
        flash_saved(f"Skipped: {title} ({apply_queue.SKIP_REASON_LABELS.get(reason, reason)})")
        st.rerun()
    else:
        st.error("Failed to skip — has supabase_setup/add_dismissal.sql been run?")


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
    clicks the control whose label starts with the matching "[x] " prefix. The
    label is the binding — nothing here can drift out of sync with a renamed
    button, it just stops matching, and the mouse still works. Typing in any
    field is left alone.
    """
    keys = "".join(f'"{key}",' for key, _ in apply_queue.SHORTCUTS)
    st.iframe(
        f"""
        <script>
        const keys = [{keys}];
        const doc = window.parent.document;
        if (!doc.__jobQueueKeysBound) {{
            doc.__jobQueueKeysBound = true;
            doc.addEventListener("keydown", (e) => {{
                if (e.metaKey || e.ctrlKey || e.altKey) return;
                const tag = (doc.activeElement || {{}}).tagName;
                if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
                if (!keys.includes(e.key)) return;
                const wanted = "[" + e.key + "]";
                const controls = doc.querySelectorAll("button, a");
                for (const el of controls) {{
                    if ((el.innerText || "").trim().startsWith(wanted)) {{
                        e.preventDefault();
                        el.click();
                        return;
                    }}
                }}
            }});
        }}
        </script>
        """,
        # No visible output — the iframe exists only to host the key listener.
        height=1,
    )


def render_focus_queue(jobs, total):
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

    st.caption(f"**{index + 1} of {len(jobs)}** in the queue · {total} scored jobs total")
    st.progress((index + 1) / len(jobs))

    with st.container(border=True):
        render_job_body(job)

        st.divider()
        actions = st.columns([1.4, 1.2, 1.6, 1, 1])
        with actions[0]:
            if st.button("[a] Mark applied", key=f"focus_apply_{job_id}",
                         type="primary", width="stretch"):
                mark_applied(job)
        with actions[1]:
            if st.button("[s] Skip", key=f"focus_skip_{job_id}", width="stretch",
                         help="Removes it from the queue for good. The row stays — a skip "
                              "is a label, not a delete."):
                skip_job(job, st.session_state.get("skip_reason") or "not_interested")
        with actions[2]:
            st.selectbox("Skip reason", apply_queue.SKIP_REASONS, key="skip_reason",
                         format_func=lambda r: apply_queue.SKIP_REASON_LABELS[r],
                         label_visibility="collapsed")
        with actions[3]:
            url = job.get("job_url")
            if url:
                st.link_button("[o] Open", url, width="stretch")
            else:
                st.button("[o] Open", key=f"focus_open_{job_id}", disabled=True,
                          width="stretch", help="This posting has no URL.")
        with actions[4]:
            if st.button("[p] Pack", key=f"focus_pack_{job_id}", width="stretch",
                         help="Write answers, checklist, pitch and the routed CV "
                              "to output/applications/"):
                build_application_pack(job)

    nav = st.columns([1, 1, 4])
    with nav[0]:
        if st.button("[k] Previous", key="focus_prev", width="stretch", disabled=index == 0):
            move_cursor(jobs, index - 1)
    with nav[1]:
        if st.button("[j] Next", key="focus_next", width="stretch",
                     disabled=index >= len(jobs) - 1):
            move_cursor(jobs, index + 1)

    render_undo_skip()
    st.caption("Keys: " + " · ".join(f"**{key}** {label.lower()}"
                                     for key, label in apply_queue.SHORTCUTS))
    keyboard_shortcuts()


def render_undo_skip():
    """One-step undo, because `s` is one keystroke away from the wrong job."""
    last = st.session_state.get("last_skipped")
    if not last:
        return
    cols = st.columns([3, 1])
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


def _bullets(heading, items):
    if not items:
        return
    st.markdown(f"**{heading}**")
    for item in items:
        st.markdown(f"- {item}")


def _facts_line(facts):
    if facts:
        st.caption(" · ".join(f"**{label}** {value}" for label, value in facts))


def close_details():
    st.session_state.pop("open_job", None)


def render_job_body(job):
    """
    The full picture for one job — everything needed to actually write the
    application. Shared by the detail dialog and the focus queue, which show the
    same thing and differ only in what surrounds it.
    """
    breakdown = job.get("score_breakdown") or {}
    title = job.get("job_title") or "N/A"
    company = job.get("company") or "N/A"
    url = job.get("job_url")

    st.markdown(f"### {title}")
    rec = REC_LABELS.get(breakdown.get("recommendation"), "")
    st.markdown(f"{company} &nbsp; {score_badge(job.get('resume_score'))} &nbsp; {rec}")
    if url:
        st.markdown(f"[Open posting ↗]({url})")

    found = found_label(job)
    if found:
        st.caption(found)

    verdict = job_view.summary(breakdown)
    if verdict:
        st.markdown(verdict)
    _facts_line(job_view.quick_facts(breakdown))

    st.divider()
    left, right = st.columns(2)
    with left:
        _bullets("Lead with", job_view.pros(breakdown))
    with right:
        _bullets("They'll push back on", job_view.cons(breakdown))

    wins = job_view.quick_wins(breakdown)
    if wins:
        st.divider()
        _bullets("Before applying", wins)

    pitch = job.get("why_me_pitch")
    if pitch:
        st.divider()
        st.markdown("**Pitch**")
        st.markdown(pitch)

    context = job_view.context_facts(breakdown)
    rivals = job_view.competition(breakdown)
    if context or rivals:
        st.divider()
        for label, value in context + rivals:
            st.markdown(f"**{label}** — {value}")

    note = job_view.confidence_note(breakdown)
    if note:
        st.caption(note)


@st.dialog("Job details", width="large", on_dismiss=close_details)
def job_details_dialog(job):
    render_job_body(job)
    st.divider()
    if st.button("Mark applied", key=f"dlg_apply_{job.get('job_id')}", type="primary"):
        close_details()
        mark_applied(job)


def render_job_card(job):
    """
    Overview only — enough to decide whether this one is worth a closer look.
    The full breakdown lives behind Details so the list stays scannable.
    """
    breakdown = job.get("score_breakdown") or {}
    job_id = job.get("job_id")
    title = job.get("job_title") or "N/A"
    company = job.get("company") or "N/A"
    url = job.get("job_url")

    with st.container(border=True):
        head = st.columns([6, 1.1])
        with head[0]:
            heading = f"**[{title}]({url})**" if url else f"**{title}**"
            rec = REC_LABELS.get(breakdown.get("recommendation"), "")
            st.markdown(f"{heading} — {company} &nbsp; {rec}")
        with head[1]:
            st.markdown(score_badge(job.get("resume_score")))

        verdict = job_view.summary(breakdown)
        if verdict:
            st.markdown(verdict)
        found = found_label(job)
        if found:
            st.caption(found)
        _facts_line(job_view.quick_facts(breakdown))

        actions = st.columns([1, 1, 1.2, 1.4, 1.4])
        with actions[3]:
            if st.button("No longer accepting", key=f"closed_{job_id}", width="stretch",
                         help="Posting is closed and you never applied — remove it from the queue"):
                if supabase_utils.mark_job_closed(job_id):
                    flash_saved(f"Closed: {title}")
                    st.rerun()
                else:
                    st.error("Failed to close — check logs.")
        with actions[0]:
            if st.button("Details", key=f"details_{job_id}", width="stretch"):
                # Held in session_state rather than opened inline: a widget click
                # inside the dialog reruns the script, and an inline-opened dialog
                # would vanish mid-interaction.
                st.session_state["open_job"] = job_id
                st.rerun()
        with actions[1]:
            if st.button("Mark applied", key=f"apply_{job_id}", width="stretch"):
                mark_applied(job)
        with actions[2]:
            if st.button("Pack", key=f"pack_{job_id}", width="stretch",
                         help="Write answers, checklist, pitch and the routed CV "
                              "to output/applications/"):
                build_application_pack(job)
        with actions[4]:
            if st.button("Skip", key=f"skip_{job_id}", width="stretch",
                         help="Not applying to this one — take it out of the queue. "
                              "The row stays; use Focus mode to record why."):
                skip_job(job, "not_interested")


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


def render_ghost_prompt(jobs):
    """
    Offer to close out applications that have gone quiet. Suggested, never
    automatic — a late reply is possible, and a wrong 'ghosted' is a false
    negative in the data everything downstream learns from.
    """
    stale = calibration.stale_pending(jobs)
    if not stale:
        return

    st.warning(f"{len(stale)} application(s) have had no reply for "
               f"{calibration.GHOSTED_AFTER_DAYS}+ days. Until they're resolved they "
               "count for nothing — calibration only learns from settled outcomes.")
    with st.expander(f"Review {len(stale)} silent application(s)"):
        for job in stale:
            age = calibration.days_since_applied(job)
            st.markdown(f"- **{job.get('job_title') or job.get('job_id')}** — "
                        f"{job.get('company') or '—'} · applied {age} days ago")
        if st.button(f"Mark all {len(stale)} as ghosted", key="ghost_all"):
            failed = [j.get("job_id") for j in stale
                      if not supabase_utils.update_application_stage(j.get("job_id"), "ghosted")]
            if failed:
                st.error(f"{len(failed)} could not be updated — check logs.")
            else:
                flash_saved(f"Marked {len(stale)} application(s) as ghosted")
                st.rerun()


def render_applications_page():
    st.header("Applications — Outcome Tracking")
    jobs = supabase_utils.get_applied_jobs_with_outcomes(999)
    if not jobs:
        st.info("No applied jobs yet.")
        return

    # Stats cover every application, not just the search results — otherwise
    # typing in the box would silently change what the totals mean.
    s = calibration.summarize(jobs)
    tiles = st.columns(5)
    tiles[0].metric("Applied", s["total_applied"])
    tiles[1].metric("Awaiting reply", s["pending"])
    tiles[2].metric("Interviews", s["interviews"])
    tiles[3].metric("Offers", s["offers"])
    tiles[4].metric("Rejected", s["rejected"])
    if s["resolved"]:
        extra = f" · {s['ghosted']} ghosted" if s["ghosted"] else ""
        st.caption(f"Interview rate {pct(s['interview_rate'])} of {s['resolved']} resolved{extra}")

    render_ghost_prompt(jobs)

    show_resolved = st.checkbox("Show resolved", value=True,
                                help="Resolved applications stay in the database — they're the "
                                     "data calibration learns from. This only hides them here.")
    if not show_resolved:
        jobs = [j for j in jobs if not calibration.is_resolved(j)]

    search = st.text_input("Search", key="search_applied",
                           placeholder="Filter by job title or company…")
    jobs = [j for j in jobs if matches_search(j, search)]
    if not jobs:
        st.info("No applications match that search.")
        return

    def sort_key(j):
        return j.get("stage_updated_at") or j.get("application_date") or ""
    jobs = sorted(jobs, key=sort_key, reverse=True)

    for job in jobs:
        job_id = job.get("job_id")
        current_stage = job.get("application_stage") or "applied"
        with st.container(border=True):
            cols = st.columns([4, 1.3, 2, 2])
            with cols[0]:
                title = job.get("job_title") or "N/A"
                company = job.get("company") or "N/A"
                url = job.get("job_url")
                label = f"**[{title}]({url})** — {company}" if url else f"**{title}** — {company}"
                st.markdown(label)
                # stage_updated_at is durable proof the write landed — it survives
                # a refresh, unlike the transient toast.
                updated = fmt_date(job.get("stage_updated_at"))
                st.caption(f"Applied {fmt_date(job.get('application_date'))} · "
                           f"Score {job.get('resume_score', '—')} · "
                           f"Stage: **{STAGE_LABELS.get(current_stage, current_stage)}**"
                           + (f" · updated {updated}" if updated != "—" else ""))
            with cols[1]:
                new_stage = st.selectbox(
                    "Stage", STAGE_ORDER,
                    index=STAGE_ORDER.index(current_stage) if current_stage in STAGE_ORDER else 0,
                    format_func=lambda s: STAGE_LABELS[s],
                    key=f"stage_{job_id}", label_visibility="collapsed",
                )
            with cols[2]:
                reason = ""
                if new_stage == "rejected":
                    reason = st.selectbox(
                        "Reason", REJECTION_REASONS,
                        index=REJECTION_REASONS.index(job.get("rejection_reason") or "")
                        if job.get("rejection_reason") in REJECTION_REASONS else 0,
                        key=f"reason_{job_id}", label_visibility="collapsed",
                        placeholder="Rejection reason",
                    )
            with cols[3]:
                save = st.button("Save", key=f"save_{job_id}")

            notes = st.text_input(
                "Notes", value=job.get("outcome_notes") or "",
                key=f"notes_{job_id}", placeholder="Optional notes (what they said, next steps)",
                label_visibility="collapsed",
            )

            if save:
                ok = supabase_utils.update_application_stage(
                    job_id, new_stage,
                    rejection_reason=reason or None,
                    notes=notes or None,
                )
                if ok:
                    flash_saved(f"Saved: {title} → {STAGE_LABELS.get(new_stage, new_stage)}")
                    st.rerun()
                else:
                    st.error("Failed to save — check logs (has the SQL migration been run?).")


def render_calibration_page():
    st.header("Calibration")
    st.caption("Is the scorer's confidence actually predictive? Applications still "
               "waiting for a reply are excluded — no answer yet isn't a rejection.")

    if st.button("Refresh"):
        st.rerun()

    jobs = supabase_utils.get_applied_jobs_with_outcomes(999)
    if not jobs:
        st.info("No applied jobs yet. Log some outcomes on the Applications page first.")
        return

    s = calibration.summarize(jobs)

    tiles = st.columns(5)
    tiles[0].metric("Applied", s["total_applied"])
    tiles[1].metric("Awaiting reply", s["pending"])
    tiles[2].metric("Resolved", s["resolved"])
    tiles[3].metric("Interview rate", pct(s["interview_rate"]))
    tiles[4].metric("Offers", s["offers"])

    if s["excluded"]:
        st.caption(f"{s['excluded']} application(s) excluded as removed/spam postings — "
                   "those never produced a real verdict, so they don't count for or "
                   "against the scorer.")

    if not s["enough_data"]:
        st.warning(
            f"Not enough resolved outcomes yet — {s['resolved']}/{s['min_required']}. "
            "The numbers below will swing wildly until you have more; treat them as a "
            "preview, not a signal."
        )

    st.subheader("Predicted vs actual")
    if s["mean_predicted"] is None:
        st.info("No scored job carries a predicted interview probability yet, so there's "
                "nothing to calibrate against.")
    else:
        cols = st.columns(3)
        cols[0].metric("Scorer predicted (avg)", pct(s["mean_predicted"]))
        cols[1].metric("Actually happened", pct(s["interview_rate"]))
        cols[2].metric("Brier score", "—" if s["brier"] is None else f"{s['brier']:.3f}",
                       help="Mean squared error of the predicted probability. Lower is "
                            "better; 0.25 is what always guessing 50% would score.")
        gap = (s["mean_predicted"] - (s["interview_rate"] or 0))
        if abs(gap) >= 0.05:
            direction = "over" if gap > 0 else "under"
            st.caption(f"The scorer is **{direction}confident** by roughly "
                       f"{abs(gap) * 100:.0f} percentage points on resolved applications.")

    st.subheader("Interview rate by score bucket")
    rows = calibration.bucket_stats(jobs)
    table = pd.DataFrame([
        {"Score": r["bucket"], "Resolved": r["n"], "Interviews": r["interviews"],
         "Interview rate": pct(r["interview_rate"])}
        for r in rows
    ])
    st.dataframe(table, hide_index=True, width="stretch")

    charted = [r for r in rows if r["n"] > 0 and r["interview_rate"] is not None]
    if s["enough_data"] and charted:
        chart_df = pd.DataFrame(
            [{"Score bucket": r["bucket"], "Interview rate": r["interview_rate"]}
             for r in charted]
        ).set_index("Score bucket")
        st.bar_chart(chart_df, color=series_color(), y_label="Interview rate")
    elif charted:
        st.caption("Chart appears once there are enough resolved outcomes to be worth plotting.")

    st.subheader("Why applications were rejected")
    reasons = calibration.rejection_reason_counts(jobs)
    if not reasons:
        st.info("No rejections logged yet.")
    else:
        reason_df = pd.DataFrame(
            [{"Reason": k, "Count": v} for k, v in reasons.items()]
        ).set_index("Reason")
        st.bar_chart(reason_df, color=series_color(), horizontal=True, x_label="Applications")


def main():
    st.title("Job Scraper")

    # Consumed here, not per page, so a save made on one page still confirms
    # even though the rerun that follows it may land somewhere else.
    saved_message = consume_flash()
    if saved_message:
        st.toast(saved_message, icon="✅")

    page = st.sidebar.radio("View", ["Jobs to Apply", "Applications", "Calibration"])
    if page == "Jobs to Apply":
        render_today_page()
    elif page == "Applications":
        render_applications_page()
    else:
        render_calibration_page()


if __name__ == "__main__":
    main()
