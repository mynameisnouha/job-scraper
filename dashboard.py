import html
import logging
from datetime import datetime

import config
import supabase_utils

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DASHBOARD_HTML_PATH = "dashboard.html"

STYLES = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 960px; margin: 0 auto; padding: 20px; background: #f5f7fa; color: #1a1a2e; }
h1 { font-size: 1.6rem; border-bottom: 3px solid #4f46e5; padding-bottom: 8px; }
.stats { display: flex; gap: 16px; margin: 20px 0; flex-wrap: wrap; }
.stat-card { background: #fff; border-radius: 10px; padding: 16px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); flex: 1; min-width: 120px; }
.stat-card .num { font-size: 1.8rem; font-weight: 700; color: #4f46e5; }
.stat-card .label { font-size: .8rem; color: #666; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
th { background: #4f46e5; color: #fff; padding: 12px 14px; text-align: left; font-size: .85rem; }
td { padding: 12px 14px; border-bottom: 1px solid #eee; font-size: .9rem; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #f0f0ff; }
.score-high { color: #16a34a; font-weight: 700; }
.score-mid { color: #ca8a04; font-weight: 700; }
.score-low { color: #dc2626; font-weight: 700; }
.score-none { color: #999; }
.badge { display: inline-block; font-size: .7rem; padding: 2px 8px; border-radius: 99px; background: #e0e7ff; color: #3730a3; margin-right: 6px; }
.rec { display: inline-block; font-size: .75rem; padding: 2px 8px; border-radius: 6px; background: #eee; color: #444; }
.rec-strong_apply, .rec-apply_now { background: #dcfce7; color: #166534; }
.rec-apply, .rec-apply_after_fixes { background: #e0f2fe; color: #075985; }
.rec-consider, .rec-apply_if_gate_negotiable { background: #fef9c3; color: #854d0e; }
.rec-skip { background: #fee2e2; color: #991b1b; }
.verdict { font-size: .75rem; color: #444; font-style: italic; margin-top: 4px; max-width: 260px; }
.why { font-size: .75rem; color: #777; margin-top: 4px; max-width: 260px; }
.job-id { font-size: .7rem; color: #aaa; margin-top: 4px; font-family: monospace; user-select: all; }
.posted { font-size: .8rem; color: #666; white-space: nowrap; }
.posted.fresh { color: #16a34a; font-weight: 700; }
details.pitch { margin-top: 6px; }
details.pitch summary { font-size: .75rem; color: #4f46e5; cursor: pointer; }
details.pitch p { font-size: .8rem; color: #444; background: #f8f8ff; border-left: 3px solid #4f46e5; padding: 8px 10px; margin: 6px 0 0; max-width: 420px; }
a { color: #4f46e5; text-decoration: none; }
a:hover { text-decoration: underline; }
.footer { margin-top: 24px; font-size: .8rem; color: #888; text-align: center; }
.applied-badge { display: inline-block; font-size: .7rem; padding: 2px 8px; border-radius: 99px; background: #dcfce7; color: #166534; margin-right: 6px; }
.apply-btn { display: inline-block; font-size: .7rem; padding: 3px 10px; border-radius: 6px; border: 1px solid #4f46e5; background: #fff; color: #4f46e5; cursor: pointer; margin-top: 4px; }
.apply-btn:hover { background: #4f46e5; color: #fff; }
.apply-btn.copied { background: #16a34a; border-color: #16a34a; color: #fff; }
h2.section { font-size: 1.15rem; margin-top: 32px; }
"""

COPY_APPLIED_JS = """
function copyApplyCmd(btn, jobId) {
  var cmd = "python mark_applied.py " + jobId;
  navigator.clipboard.writeText(cmd).then(function() {
    var original = btn.textContent;
    btn.textContent = "Copied!";
    btn.classList.add("copied");
    setTimeout(function() { btn.textContent = original; btn.classList.remove("copied"); }, 1500);
  });
}
"""


def days_ago_label(job):
    """Human-readable age of the posting, from posted_at (preferred) or scraped_at."""
    ts = job.get("posted_at") or job.get("scraped_at")
    if not ts:
        return "—"
    try:
        posted = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        days = (datetime.now(posted.tzinfo) - posted).days
    except (ValueError, TypeError):
        return "—"
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def found_today(job):
    """True if the job was scraped (found) today, based on scraped_at."""
    ts = job.get("scraped_at")
    if not ts:
        return False
    try:
        scraped = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        now = datetime.now(scraped.tzinfo)
    except (ValueError, TypeError):
        return False
    return scraped.date() == now.date()


def score_class(score):
    if score is None:
        return "score-none"
    if score >= 75:
        return "score-high"
    if score >= 50:
        return "score-mid"
    return "score-low"


def build_dashboard():
    top_scored = [j for j in supabase_utils.get_top_scored_jobs_to_apply(999) if found_today(j)]
    unscored = [j for j in supabase_utils.get_jobs_to_score(999) if found_today(j)]
    applied = supabase_utils.get_applied_jobs(999)

    # Stats
    total = len(top_scored) + len(unscored)
    high = sum(1 for j in top_scored if (j.get("resume_score") or 0) >= 75)
    mid = sum(1 for j in top_scored if 50 <= (j.get("resume_score") or 0) < 75)
    low = sum(1 for j in top_scored if 0 <= (j.get("resume_score") or 0) < 50)

    # Always ranked by resume_score, highest first.
    all_jobs = sorted(top_scored, key=lambda j: j.get("resume_score") or 0, reverse=True)

    rows_html = ""
    for job in all_jobs:
        score = job.get("resume_score")
        job_id = html.escape(str(job.get("job_id") or ""))
        title = html.escape(job.get("job_title") or "N/A")
        company = html.escape(job.get("company") or "N/A")
        provider = html.escape(job.get("provider") or "")
        job_url = job.get("job_url", "")
        link = f'<a href="{html.escape(job_url, quote=True)}" target="_blank">Open</a>' if job_url else "—"
        badge = f'<span class="badge">{provider}</span>' if provider else ""
        cls = score_class(score)
        score_display = f'<span class="{cls}">{score}/100</span>' if score is not None else '<span class="score-none">unscored</span>'

        breakdown = job.get("score_breakdown") or {}
        rec = html.escape(str(breakdown.get("recommendation") or "—"))
        gaps = breakdown.get("structural_gaps") or breakdown.get("key_gaps") or []
        gaps_display = html.escape(", ".join(str(g) for g in gaps[:4])) if gaps else "—"
        lang_fit = html.escape(str(breakdown.get("language_fit") or ""))
        why = f'<div class="why">{gaps_display}</div>' if gaps else ""
        lang = f'<div class="why">{lang_fit}</div>' if lang_fit else ""
        verdict = html.escape(str(breakdown.get("one_line_verdict") or ""))
        verdict_html = f'<div class="verdict">{verdict}</div>' if verdict else ""

        posted = html.escape(days_ago_label(job))
        fresh_cls = "fresh" if posted in ("today", "1 day ago", "2 days ago") else ""

        pitch = job.get("why_me_pitch")
        pitch_html = (f'<details class="pitch"><summary>Why-me pitch</summary>'
                      f'<p>{html.escape(pitch)}</p></details>') if pitch else ""

        job_id_html = (f'<div class="job-id" title="Job ID — use with mark_applied.py">{job_id}</div>'
                       f'<button class="apply-btn" onclick="copyApplyCmd(this,\'{job_id}\')">Mark applied</button>') if job_id else ""

        rows_html += (f"<tr><td>{badge}{title}{pitch_html}{job_id_html}</td><td>{company}</td><td>{score_display}</td>"
                      f"<td><span class='rec rec-{rec}'>{rec}</span>{lang}{verdict_html}</td><td>{why}</td>"
                      f"<td><span class='posted {fresh_cls}'>{posted}</span></td><td>{link}</td></tr>")

    if not rows_html:
        rows_html = "<tr><td colspan='7' style='text-align:center;color:#888;padding:30px;'>No jobs found today.</td></tr>"

    applied_rows_html = ""
    for job in applied:
        job_id = html.escape(str(job.get("job_id") or ""))
        title = html.escape(job.get("job_title") or "N/A")
        company = html.escape(job.get("company") or "N/A")
        score = job.get("resume_score")
        score_display = f'<span class="{score_class(score)}">{score}/100</span>' if score is not None else '<span class="score-none">unscored</span>'
        job_url = job.get("job_url", "")
        link = f'<a href="{html.escape(job_url, quote=True)}" target="_blank">Open</a>' if job_url else "—"
        applied_at = job.get("application_date")
        try:
            applied_label = datetime.fromisoformat(str(applied_at).replace("Z", "+00:00")).strftime("%Y-%m-%d") if applied_at else "—"
        except (ValueError, TypeError):
            applied_label = "—"
        applied_rows_html += (f"<tr><td><span class='applied-badge'>&#10003; Applied</span>{title}</td>"
                              f"<td>{company}</td><td>{score_display}</td><td>{applied_label}</td><td>{link}</td></tr>")

    if not applied_rows_html:
        applied_rows_html = "<tr><td colspan='5' style='text-align:center;color:#888;padding:20px;'>No applied jobs yet.</td></tr>"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    page_html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Dashboard</title><style>{STYLES}</style></head>
<body>
<h1>Job Dashboard &mdash; Today's Jobs</h1>
<div class="stats">
  <div class="stat-card"><div class="num">{total}</div><div class="label">Total Jobs</div></div>
  <div class="stat-card"><div class="num">{high}</div><div class="label">Strong Match (75+)</div></div>
  <div class="stat-card"><div class="num">{mid}</div><div class="label">Moderate (50–74)</div></div>
  <div class="stat-card"><div class="num">{low}</div><div class="label">Weak (&lt;50)</div></div>
  <div class="stat-card"><div class="num">{len(unscored)}</div><div class="label">Unscored</div></div>
  <div class="stat-card"><div class="num">{len(applied)}</div><div class="label">Applied</div></div>
</div>
<table><thead><tr><th>Job</th><th>Company</th><th>Score</th><th>Recommendation</th><th>Key Gaps</th><th>Posted</th><th>Link</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<h2 class="section">Applied Jobs</h2>
<table><thead><tr><th>Job</th><th>Company</th><th>Score</th><th>Applied On</th><th>Link</th></tr></thead>
<tbody>{applied_rows_html}</tbody></table>
<div class="footer">Generated {now} &mdash; <a href="https://github.com/mynameisnouha/job-scraper">job-scraper</a></div>
<script>{COPY_APPLIED_JS}</script>
</body></html>"""

    with open(DASHBOARD_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(page_html)
    logging.info(f"Dashboard written to {DASHBOARD_HTML_PATH} ({total} jobs)")


if __name__ == "__main__":
    build_dashboard()
