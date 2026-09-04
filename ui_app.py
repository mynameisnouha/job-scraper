"""
Streamlit UI for reviewing jobs and logging application outcomes without
touching the Supabase table editor or the static dashboard.html.

Run with: streamlit run ui_app.py
"""
from datetime import datetime

import streamlit as st

import supabase_utils
from dashboard import found_today

STAGE_LABELS = {
    "applied": "Applied",
    "interview_1": "Interview 1",
    "interview_2": "Interview 2",
    "interview_3": "Interview 3",
    "offer": "Offer",
    "rejected": "Rejected",
    "ghosted": "Ghosted (no response)",
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

    controls = st.columns([1.5, 1.5, 2])
    with controls[0]:
        today_only = st.checkbox("Found today only", value=True)
    with controls[1]:
        min_score = st.slider("Min score", 0, 100, 70, step=5)
    with controls[2]:
        show_n = st.number_input("Max shown", min_value=5, max_value=200, value=25, step=5)

    total = len(jobs)
    if today_only:
        jobs = [j for j in jobs if found_today(j)]
    jobs = [j for j in jobs if (j.get("resume_score") or 0) >= min_score]
    jobs = sorted(jobs, key=lambda j: j.get("resume_score") or 0, reverse=True)

    matched = len(jobs)
    jobs = jobs[: int(show_n)]
    st.caption(f"Showing {len(jobs)} of {matched} matching ({total} scored jobs total). "
               "Rendering every job at once is slow — narrow with the filters above.")

    if not jobs:
        st.info("No jobs match these filters. Try unchecking 'Found today only' or lowering the min score.")
        return

    for job in jobs:
        breakdown = job.get("score_breakdown") or {}
        with st.container(border=True):
            cols = st.columns([5, 1.2, 1.5])
            with cols[0]:
                title = job.get("job_title") or "N/A"
                company = job.get("company") or "N/A"
                url = job.get("job_url")
                if url:
                    st.markdown(f"**[{title}]({url})** — {company}")
                else:
                    st.markdown(f"**{title}** — {company}")
                rec = breakdown.get("recommendation")
                verdict = breakdown.get("one_line_verdict")
                if rec:
                    st.caption(f"Recommendation: {rec}")
                if verdict:
                    st.caption(verdict)
            with cols[1]:
                st.markdown(score_badge(job.get("resume_score")))
            with cols[2]:
                job_id = job.get("job_id")
                if st.button("Mark applied", key=f"apply_{job_id}"):
                    if supabase_utils.mark_job_applied(job_id):
                        supabase_utils.update_application_stage(job_id, "applied")
                        st.success("Marked applied.")
                        st.rerun()
                    else:
                        st.error("Failed to mark applied — check logs.")


def render_applications_page():
    st.header("Applications — Outcome Tracking")
    jobs = supabase_utils.get_applied_jobs_with_outcomes(999)
    if not jobs:
        st.info("No applied jobs yet.")
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
                st.caption(f"Applied {fmt_date(job.get('application_date'))} · "
                           f"Score {job.get('resume_score', '—')} · "
                           f"Current stage: {STAGE_LABELS.get(current_stage, current_stage)}")
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
                    st.success("Saved.")
                    st.rerun()
                else:
                    st.error("Failed to save — check logs (has the SQL migration been run?).")


def main():
    st.title("Job Scraper")
    page = st.sidebar.radio("View", ["Jobs to Apply", "Applications"])
    if page == "Jobs to Apply":
        render_today_page()
    else:
        render_applications_page()


if __name__ == "__main__":
    main()
