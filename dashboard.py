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
a { color: #4f46e5; text-decoration: none; }
a:hover { text-decoration: underline; }
.footer { margin-top: 24px; font-size: .8rem; color: #888; text-align: center; }
"""


def score_class(score):
    if score is None:
        return "score-none"
    if score >= 75:
        return "score-high"
    if score >= 50:
        return "score-mid"
    return "score-low"


def build_dashboard():
    top_scored = supabase_utils.get_top_scored_jobs_to_apply(999)
    unscored = supabase_utils.get_jobs_to_score(999)

    # Stats
    total = len(top_scored) + len(unscored)
    high = sum(1 for j in top_scored if (j.get("resume_score") or 0) >= 75)
    mid = sum(1 for j in top_scored if 50 <= (j.get("resume_score") or 0) < 75)
    low = sum(1 for j in top_scored if 0 <= (j.get("resume_score") or 0) < 50)

    all_jobs = sorted(top_scored, key=lambda j: j.get("resume_score") or 0, reverse=True)

    rows_html = ""
    for job in all_jobs:
        score = job.get("resume_score")
        title = job.get("job_title", "N/A")
        company = job.get("company", "N/A")
        provider = job.get("provider", "")
        job_url = job.get("job_url", "")
        link = f'<a href="{job_url}" target="_blank">Open</a>' if job_url else "—"
        badge = f'<span class="badge">{provider}</span>' if provider else ""
        cls = score_class(score)
        score_display = f'<span class="{cls}">{score}/100</span>' if score is not None else '<span class="score-none">unscored</span>'
        rows_html += f"<tr><td>{badge}{title}</td><td>{company}</td><td>{score_display}</td><td>{link}</td></tr>"

    if not rows_html:
        rows_html = "<tr><td colspan='4' style='text-align:center;color:#888;padding:30px;'>No jobs found in Supabase yet.</td></tr>"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Dashboard</title><style>{STYLES}</style></head>
<body>
<h1>Job Dashboard</h1>
<div class="stats">
  <div class="stat-card"><div class="num">{total}</div><div class="label">Total Jobs</div></div>
  <div class="stat-card"><div class="num">{high}</div><div class="label">Strong Match (75+)</div></div>
  <div class="stat-card"><div class="num">{mid}</div><div class="label">Moderate (50–74)</div></div>
  <div class="stat-card"><div class="num">{low}</div><div class="label">Weak (&lt;50)</div></div>
  <div class="stat-card"><div class="num">{len(unscored)}</div><div class="label">Unscored</div></div>
</div>
<table><thead><tr><th>Job</th><th>Company</th><th>Score</th><th>Link</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<div class="footer">Generated {now} &mdash; <a href="https://github.com/mynameisnouha/job-scraper">job-scraper</a></div>
</body></html>"""

    with open(DASHBOARD_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    logging.info(f"Dashboard written to {DASHBOARD_HTML_PATH} ({total} jobs)")


if __name__ == "__main__":
    build_dashboard()
