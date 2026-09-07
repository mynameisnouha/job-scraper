"""
Email alerts for strong matches, sent at the end of a scoring run.

The pipeline runs unattended four times a day. Without a nudge a job scored at
09:00 is not looked at until the evening, and applying early is measurably most
of the advantage a small pipeline has. So: one mail per run, listing only the
jobs *that run* scored at or above EMAIL_ALERT_MIN_SCORE. No mail when there is
nothing to say — an alert that arrives every run is an alert nobody opens.

De-duplication is structural rather than a flag in the database: a job is scored
once, so it can only appear in one run's batch. Re-running the scorer over the
same rows does not re-alert, because those rows are no longer unscored.

Transport is plain SMTP over the standard library, so any provider works — Gmail
with an App Password, Outlook, Resend, SendGrid, Postmark. Credentials come only
from the environment; this repository is public.

Test the wiring without waiting for a run:  python notify.py --test
"""
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

import config


def alert_worthy(jobs: List[Dict[str, Any]],
                 min_score: Optional[int] = None) -> List[Dict[str, Any]]:
    """The jobs from a run that are worth interrupting someone for, best first."""
    threshold = config.EMAIL_ALERT_MIN_SCORE if min_score is None else min_score
    matches = [j for j in jobs if (j.get("resume_score") or 0) >= threshold]
    return sorted(matches, key=lambda j: j.get("resume_score") or 0, reverse=True)


def missing_settings() -> List[str]:
    """Which required settings are unset. Empty means the mailer can run."""
    required = {
        "SMTP_HOST": config.SMTP_HOST,
        "SMTP_USER": config.SMTP_USER,
        "SMTP_PASSWORD": config.SMTP_PASSWORD,
        "EMAIL_FROM": config.EMAIL_FROM,
        "EMAIL_TO": config.EMAIL_TO,
    }
    return [name for name, value in required.items() if not value]


def recipients() -> List[str]:
    """EMAIL_TO as a list — a comma-separated string is allowed."""
    return [addr.strip() for addr in (config.EMAIL_TO or "").split(",") if addr.strip()]


def _subject(matches: List[Dict[str, Any]]) -> str:
    """The subject line carries the best match, so the inbox preview is enough."""
    top = matches[0]
    title = top.get("job_title") or "Untitled role"
    company = top.get("company") or "unknown company"
    score = top.get("resume_score") or 0
    if len(matches) == 1:
        return f"New match ({score}/100): {title} at {company}"
    return f"{len(matches)} new matches — top {score}/100: {title} at {company}"


def build_message(matches: List[Dict[str, Any]],
                  min_score: Optional[int] = None) -> Tuple[str, str, str]:
    """
    (subject, plain text, html) for a batch of matches.

    Both bodies carry the same facts. The mail is deliberately a summary ending
    in "open the queue" — deciding needs the full breakdown, which lives in the
    UI, and a copy of it here would only go stale.
    """
    threshold = config.EMAIL_ALERT_MIN_SCORE if min_score is None else min_score
    cap = max(1, getattr(config, "EMAIL_ALERT_MAX_JOBS", 10))
    shown = matches[:cap]
    hidden = len(matches) - len(shown)

    text = [f"{len(matches)} new job(s) scored {threshold}+ in the latest run.", ""]
    items = []
    for job in shown:
        breakdown = job.get("score_breakdown") or {}
        title = job.get("job_title") or "Untitled role"
        company = job.get("company") or "Unknown company"
        url = job.get("job_url") or ""
        verdict = breakdown.get("one_line_verdict") or ""
        score = job.get("resume_score") or 0

        text.append(f"[{score}/100] {title} — {company}")
        if verdict:
            text.append(f"    {verdict}")
        if url:
            text.append(f"    {url}")
        text.append("")

        heading = (f'<a href="{html.escape(url, quote=True)}" '
                   f'style="color:#2a78d6;text-decoration:none">{html.escape(title)}</a>'
                   if url else html.escape(title))
        items.append(
            '<li style="margin:0 0 14px 0">'
            f'<strong style="font-size:15px">{heading}</strong><br>'
            f'<span style="color:#555">{html.escape(company)} · <strong>{score}/100</strong></span>'
            + (f'<br><span style="color:#555">{html.escape(verdict)}</span>' if verdict else "")
            + '</li>'
        )

    if hidden > 0:
        text.append(f"…and {hidden} more. Open the queue to see them all.")
        items.append(f'<li style="color:#555">…and {hidden} more.</li>')
    text.append("Run `streamlit run ui_app.py` to work the queue.")

    body_html = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'font-size:14px;line-height:1.5;color:#222">'
        f'<p>{len(matches)} new job(s) scored <strong>{threshold}+</strong> in the latest run.</p>'
        f'<ul style="padding-left:18px">{"".join(items)}</ul>'
        '<p style="color:#555">Run <code>streamlit run ui_app.py</code> to work the queue.</p>'
        '</div>'
    )
    return _subject(matches), "\n".join(text), body_html


def send(subject: str, text: str, body_html: str) -> bool:
    """
    Deliver one mail. Returns False (never raises) on any delivery problem.

    A scoring run must not fail because a mail server was unreachable — the jobs
    are already in the database, and the alert is a convenience on top of them.
    """
    missing = missing_settings()
    if missing:
        logging.warning("Email alert not sent — unset: %s.", ", ".join(missing))
        return False
    to = recipients()
    if not to:
        logging.warning("Email alert not sent — EMAIL_TO has no usable address.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.EMAIL_FROM
    message["To"] = ", ".join(to)
    message.set_content(text)
    message.add_alternative(body_html, subtype="html")

    try:
        # Port 465 is implicit TLS; everything else (587, 25) negotiates with
        # STARTTLS. Getting this pair backwards is the usual cause of a hang.
        if int(config.SMTP_PORT) == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, int(config.SMTP_PORT), timeout=30,
                                  context=ssl.create_default_context()) as server:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(config.SMTP_HOST, int(config.SMTP_PORT), timeout=30) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        logging.error("Email alert failed: SMTP rejected the credentials. With Gmail, "
                      "SMTP_PASSWORD must be a 16-character App Password — the account "
                      "password is always refused.")
        return False
    except Exception as exc:
        logging.error("Email alert failed to send: %s", exc)
        return False

    logging.info("Email alert sent to %s.", ", ".join(to))
    return True


def notify_matches(jobs: List[Dict[str, Any]], min_score: Optional[int] = None) -> int:
    """
    Alert on the strong matches in a finished run. Returns how many were alerted.

    Zero is the normal, quiet outcome: alerts switched off, nothing cleared the
    bar, or a delivery failure that has already been logged.
    """
    if not getattr(config, "EMAIL_ALERTS_ENABLED", False):
        return 0
    matches = alert_worthy(jobs, min_score)
    if not matches:
        threshold = config.EMAIL_ALERT_MIN_SCORE if min_score is None else min_score
        logging.info("No job in this run scored %s+; no email alert sent.", threshold)
        return 0
    try:
        subject, text, body_html = build_message(matches, min_score)
    except Exception as exc:
        # Nothing about a malformed job row may cost the run its remaining phases.
        logging.error("Email alert could not be composed: %s", exc)
        return 0
    return len(matches) if send(subject, text, body_html) else 0


def _self_test() -> int:
    """`python notify.py --test` — prove the credentials work before a real run."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    missing = missing_settings()
    if missing:
        print("Cannot send — these are unset: " + ", ".join(missing))
        print("Set them in .env (local) or as repository secrets (GitHub Actions).")
        return 1
    sample = [{
        "job_id": "test", "job_title": "Machine Learning Engineer", "company": "Example GmbH",
        "resume_score": 84, "job_url": "https://example.com/job",
        "score_breakdown": {"one_line_verdict": "Test alert — the mail wiring works."},
    }]
    subject, text, body_html = build_message(sample)
    if send(subject, text, body_html):
        print(f"Test alert sent to {', '.join(recipients())}.")
        return 0
    print("Sending failed — see the error above.")
    return 1


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Email alerts for strong matches.")
    parser.add_argument("--test", action="store_true",
                        help="Send one sample alert to EMAIL_TO and exit.")
    args = parser.parse_args()
    sys.exit(_self_test() if args.test else parser.print_help() or 0)
