import logging
import requests

import config
import supabase_utils

logger = logging.getLogger(__name__)


def send_telegram_message(text: str) -> bool:
    """Send a plain text message via Telegram bot."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.info("Telegram not configured — skip notification.")
        return False

    url = f"{config.TELEGRAM_API_URL}{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            logger.info("Telegram message sent.")
            return True
        else:
            logger.warning(f"Telegram API error {resp.status_code}: {resp.text}")
            return False
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def format_job_line(job: dict, idx: int) -> str:
    """Format a single job as a digest line."""
    score = job.get("resume_score", "?")
    title = job.get("job_title", "N/A")
    company = job.get("company", "N/A")
    job_url = job.get("job_url", "")
    link = f"\n   {job_url}" if job_url else ""
    return f"{idx}. <b>{title}</b> @ {company}  —  Score: <b>{score}/100</b>{link}"


def send_daily_digest(top_n: int = None) -> bool:
    """Fetch top scored jobs and send a Telegram digest."""
    top_n = top_n or getattr(config, "DIGEST_TOP_N", 10)

    jobs = supabase_utils.get_top_scored_jobs_to_apply(top_n)
    if not jobs:
        logger.info("No scored jobs to digest.")
        return False

    lines = ["<b>Daily Job Digest</b>", f"Top {len(jobs)} matching jobs\n"]
    for i, job in enumerate(jobs, 1):
        lines.append(format_job_line(job, i))

    lines.append(f"\nRun the workflow again to refresh.")
    return send_telegram_message("\n".join(lines))
