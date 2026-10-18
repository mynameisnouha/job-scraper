"""Find a LinkedIn job's employer link in your own logged-in browser.

LinkedIn's public job page never shows where an "Apply" leads; the logged-in page
does. This opens the job in a browser profile you have logged into once, reads the
employer URL, saves it as the job's apply_url, and closes. No LLM, no tokens.

    python -m apply.resolve_link <job_id>      # find and save one job's employer link
    python -m apply.resolve_link --login       # optional: log in ahead of time

Logged out, the window stays open on LinkedIn's login page for up to five minutes;
log in there and the lookup carries on.

What it reads, in order:
  1. a /safety/go link on the page (LinkedIn's redirect to the employer), decoded;
  2. otherwise the external "Apply" button, clicked only to read the new tab it
     opens, which is then closed. An external apply button only opens the
     employer's site; it submits nothing.
It never clicks Easy Apply / "Einfach bewerben", which is LinkedIn's own form.

One page view per job you ask for, at your pace, from your own session: the
usage LinkedIn sees is a person opening a job ad.

Exit codes: 0 link saved, 1 bad job, 2 no employer link (Easy Apply or none),
3 not logged in (no login within the wait), 4 browser error.
"""

import argparse
import logging
import os
import re
import sys
import time
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import config
from db import supabase_utils
from sources import ats_detect

PROFILE_DIR = os.environ.get("JOB_SCRAPER_BROWSER_PROFILE") or os.path.join(
    os.path.expanduser("~"), ".job-scraper", "browser")
LOGIN_URL = "https://www.linkedin.com/login"
PAGE_TIMEOUT_MS = 30_000
SETTLE_MS = 2_500
# Logged out, the window stays open on LinkedIn's login page this long so the
# owner can log in there and the lookup carries on, instead of closing on them.
LOGIN_WAIT_SECONDS = 300
LOGIN_POLL_SECONDS = 2

_LOGIN_WALLS = ("/authwall", "/login", "/checkpoint", "/signup", "/uas/login")
_EASY_APPLY = re.compile(r"easy\s*apply|einfach\s*bewerben", re.I)
_APPLY = re.compile(r"\bapply\b|bewerb", re.I)
_BUTTONS_JS = ("els => els.map((e, i) => ({i, text: ((e.innerText || '') + ' ' "
               "+ (e.getAttribute('aria-label') || '')).trim()}))")


def decode_safety_link(href: Optional[str]) -> Optional[str]:
    """The employer URL inside a linkedin.com/safety/go?url=... redirect."""
    if not href:
        return None
    target = (parse_qs(urlsplit(href).query).get("url") or [""])[0]
    if not target.startswith(("http://", "https://")) or ats_detect.is_job_board(target):
        return None
    return target


def linkedin_job_url(job: dict) -> Optional[str]:
    url = job.get("job_url") or ""
    if "linkedin.com/jobs/view/" in url:
        return url
    if job.get("provider") == "linkedin" and str(job.get("job_id") or "").isdigit():
        return f"https://www.linkedin.com/jobs/view/{job['job_id']}/"
    return None


def _employer_url(url: Optional[str]) -> Optional[str]:
    if url and url.startswith(("http://", "https://")) and not ats_detect.is_job_board(url):
        return url
    return None


def resolve(page, job_url: str) -> Tuple[str, Optional[str]]:
    """(status, url) for one job page. Status: found, easy_apply, not_found, not_logged_in."""
    page.goto(job_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)
    if any(wall in (page.url or "") for wall in _LOGIN_WALLS):
        return "not_logged_in", None
    # Logged out, LinkedIn serves the public page without redirecting; its controls
    # carry public_jobs_* tracking names, and its Apply only opens a sign-in box.
    if page.eval_on_selector_all('[data-tracking-control-name^="public_jobs"]', "els => els.length > 0 ? [1] : []"):
        return "not_logged_in", None

    for href in page.eval_on_selector_all('a[href*="/safety/go"]', "els => els.map(e => e.href)"):
        url = decode_safety_link(href)
        if url:
            return "found", url

    buttons = page.eval_on_selector_all("button", _BUTTONS_JS)
    external = [b for b in buttons if _APPLY.search(b["text"]) and not _EASY_APPLY.search(b["text"])]
    if not external:
        easy = any(_EASY_APPLY.search(b["text"]) for b in buttons)
        return ("easy_apply" if easy else "not_found"), None

    try:
        with page.context.expect_page(timeout=PAGE_TIMEOUT_MS) as new_tab:
            page.locator("button").nth(external[0]["i"]).click()
        tab = new_tab.value
        tab.wait_for_load_state("domcontentloaded")
        url = _employer_url(tab.url)
        tab.close()
    except Exception as e:  # no new tab: the button did something else
        logging.warning(f"The apply button opened no employer tab: {e}")
        return "not_found", None
    return ("found", url) if url else ("not_found", None)


def resolve_with_login(page, job_url: str, logged_in: Callable[[], bool], wait_seconds: int,
                       now: Callable[[], float] = time.monotonic,
                       sleep: Callable[[float], None] = time.sleep) -> Tuple[str, Optional[str]]:
    """resolve(), but when logged out wait on the login page for the owner, then retry.

    wait_seconds 0 (headless: nobody can log in) returns not_logged_in at once."""
    status, url = resolve(page, job_url)
    if status != "not_logged_in" or wait_seconds <= 0:
        return status, url
    print(f"Not logged into LinkedIn. Log in in the browser window; waiting up to "
          f"{wait_seconds // 60} min.", flush=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    deadline = now() + wait_seconds
    while now() < deadline:
        if logged_in():
            return resolve(page, job_url)
        sleep(LOGIN_POLL_SECONDS)
    return "not_logged_in", None


def _has_session(context) -> bool:
    """LinkedIn's session cookie is present: the owner is logged in."""
    try:
        return any(c.get("name") == "li_at" for c in context.cookies("https://www.linkedin.com"))
    except Exception:
        return False


def _resolve_in_browser(job_url: str, headless: bool) -> Tuple[str, Optional[str]]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(PROFILE_DIR, headless=headless)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            return resolve_with_login(page, job_url, lambda: _has_session(context),
                                      0 if headless else LOGIN_WAIT_SECONDS)
        finally:
            context.close()


def login() -> int:
    """Open the browser profile on LinkedIn's login page; close the window when done."""
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    print(f"Log into LinkedIn in the window that opens, then close it. Profile: {PROFILE_DIR}")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        context.close()
    return 0


def _load_job(job_id: str) -> Optional[dict]:
    return supabase_utils.get_job_with_description(job_id)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Find a LinkedIn job's employer link.")
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--login", action="store_true", help="Log into LinkedIn once.")
    parser.add_argument("--headless", action="store_true",
                        default=bool(getattr(config, "RESOLVE_LINK_HEADLESS", False)))
    args = parser.parse_args(argv)
    if args.login:
        return login()
    if not args.job_id:
        parser.error("give a job_id, or --login")

    job = _load_job(args.job_id)
    if not job:
        print(f"No job {args.job_id}.")
        return 1
    url = linkedin_job_url(job)
    if not url:
        print("Only LinkedIn jobs need this; others carry their link already.")
        return 1
    try:
        status, employer_url = _resolve_in_browser(url, args.headless)
    except Exception as e:
        print(f"Browser error: {e}")
        return 4
    if status == "found":
        supabase_utils.set_apply_url(args.job_id, employer_url, ats_detect.detect(employer_url))
        print(f"Employer link: {employer_url}")
        return 0
    if status == "not_logged_in":
        print("Not logged into LinkedIn: no login in the browser window in time.")
        return 3
    print("This job applies through LinkedIn Easy Apply only." if status == "easy_apply"
          else "No employer link found on the page.")
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
