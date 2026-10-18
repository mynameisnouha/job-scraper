"""Search queries, caps, thresholds and models — the settings you are expected to edit.

Secrets never go here: this file is committed. They go in .env (see .env.example).
Every setting below, and every environment variable that overrides one, is
explained in the Configuration section of README.md.
"""

import glob
import os
import shutil
import sys
from dotenv import load_dotenv

load_dotenv()


def _utf8_console(streams) -> None:
    """Write console output as UTF-8.

    On Windows, output that is piped or captured (Streamlit, Task Scheduler, a
    subprocess, `| tee`) is encoded as cp1252, and one character outside it in a
    job title or description — "→", "�" — raises UnicodeEncodeError and kills
    the run. Every entry point imports config, so switching here covers them all.
    """
    for stream in streams:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_console((sys.stdout, sys.stderr))


def _int_env(name: str, default: int) -> int:
    """An env override for a processing cap.

    The values below are the steady state for the scheduled pipeline. A one-off
    backfill needs different ones for a single run, and editing the committed
    defaults to get them leaves a window where a scheduled trigger picks up the
    backfill values instead. An env var is scoped to the run that sets it.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        # A typo in a workflow input must not silently become an unbounded run.
        raise ValueError(f"{name} must be an integer, got {raw!r}")

def _bool_env(name: str, default: bool) -> bool:
    """An on/off switch that a workflow can flip without a commit."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

# --- DO NOT MODIFY THE BELOW SECTION ---

# =================================================================
# 1. CORE SYSTEM CONFIGURATION (Do Not Modify)
# =================================================================
SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_TABLE_NAME: str = "jobs"
SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME = "customized_resumes"
SUPABASE_STORAGE_BUCKET="personalized_resumes"
SUPABASE_RESUME_STORAGE_BUCKET="resumes"
SUPABASE_BASE_RESUME_TABLE_NAME = "base_resume"
BASE_RESUME_PATH = "resume.json"

# API keys — set only the key(s) needed for your chosen provider.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_FIRST_API_KEY")

# =================================================================
# 2. USER PREFERENCES (Editable)
# =================================================================

# --- LLM Settings ---
# Use any model supported by LiteLLM (gemini, openai/gpt-4o-mini, groq/llama-3.3-70b-versatile)
# Full list of supported models & naming: https://docs.litellm.ai/docs/providers
LLM_MODEL = os.environ.get("LLM_MODEL") or "anthropic/claude-sonnet-4-6"

# Cheap fast model for the pre-scoring screen pass (hard gates + rough band).
# Only jobs that pass the screen get the full (expensive) scoring call.
SCREENING_ENABLED = True
LLM_SCREEN_MODEL = os.environ.get("LLM_SCREEN_MODEL") or "anthropic/claude-haiku-4-5"
# Per-RUN, and the pipeline runs four times a day. The screen cap is the
# pipeline's throughput, not its spend: intake runs at about 100 jobs a day, and
# at 40 a run (160 a day) any burst left a backlog that took weeks to drain — 764
# rows in October 2026, some old enough for the 14-day cleanup to take them
# unseen. At 100 a run the backlog clears within a day. Spend still tracks intake,
# because a run only screens what is unscored.
JOBS_TO_SCREEN_PER_RUN = _int_env("JOBS_TO_SCREEN_PER_RUN", 100)
LLM_SCREEN_MAX_RPM = 30
LLM_SCREEN_REQUEST_DELAY = 1

# --- Search Configuration ---

# --- Graduate / trainee programmes ---
# Structured early-careers intakes are wanted alongside standard roles, but they
# need their own queries: a bare "Graduate Program" search returns the finance,
# legal and sales programmes first and the per-query cap is spent before a data
# one appears (LinkedIn, 2026-09-12: 0 of the top 10 named data or AI). Every
# term here names the field. Both languages, as with the other queries — German
# employers write "Trainee Data Science" and "Traineeprogramm Data Analytics",
# not "Graduate Programme". Postings are tagged program_type='graduate_program'
# by title at scrape time (sources/scraper.py), whichever query found them.
GRADUATE_PROGRAM_QUERIES_EN = [
    "Trainee Data Science",
    "Trainee Data Analytics",
    "Trainee Machine Learning",
    "Trainee Artificial Intelligence",
    "Graduate Program Data",
    "Graduate Program AI",
    "AI Residency",
]
GRADUATE_PROGRAM_QUERIES_DE = [
    "Traineeprogramm Data",
    "Trainee Künstliche Intelligenz",
    "Trainee Data Engineering",
    "Trainee Business Intelligence",
    "Absolventenprogramm Data",
]

LINKEDIN_SEARCH_QUERIES = [
    "Data Scientist",
    "Machine Learning Engineer",
    "AI Engineer",
    "NLP Engineer",
    "MLOps Engineer",
    "Computer Vision Engineer",
    "Deep Learning Engineer",
    "LLM Engineer",
    "AI Research Scientist",
    "Machine Learning Scientist",
    "AI Developer",
    "Prompt Engineer",
    "AI Specialist",
    "Data Scientist PySpark",
    "Big Data Scientist",
    "Applied Scientist",
    *GRADUATE_PROGRAM_QUERIES_EN,
    *GRADUATE_PROGRAM_QUERIES_DE,
]
LINKEDIN_LOCATION = "Germany"
LINKEDIN_GEO_ID = 101282230      # Singapore: 102454443, Dubai: 100205264
LINKEDIN_JOB_TYPE = "F" # F=Full-time, C=Contract, P=Part-time, T=Temporary, I=Internship
# Stays at 24h deliberately. The fragility of "one failed run loses that day's
# postings" is fixed by running four times a day (see run_all.yml), not by widening
# the window: a 7-day window would re-fetch the same 500 results without telling us
# which went up an hour ago, and applying early measurably helps.
LINKEDIN_JOB_POSTING_DATE = "r86400" # r86400=Past 24h, r604800=Past week
LINKEDIN_F_WT = "1%2C2%2C3" # 1=Onsite, 2=Remote, 3=Hybrid (URL-encoded comma list; single value like "3" also works)
LINKEDIN_F_E = "2%2C3" # Experience level: 1=Internship, 2=Entry level, 3=Associate, 4=Mid-Senior, 5=Director

# --- Bundesagentur für Arbeit (Jobsuche API) ---
# Free for employers to post to, which is why it carries the Mittelstand that never
# reaches LinkedIn. German search terms return materially different (and better)
# results than their English equivalents, so both forms are listed rather than
# assuming the English one covers it.
# Measured 2026-09-06 over a 7-day window. "Datenwissenschaftler", "Dateningenieur"
# and "NLP Engineer" returned zero at both 1 and 7 days and were replaced: nobody in
# Germany advertises for a Datenwissenschaftler. The lesson is that a German term only
# helps when it is the term employers actually type — "Künstliche Intelligenz" (100+)
# and "KI Ingenieur" (48) are the strongest queries here, while literal translations
# are empty. English terms do appear in German ads, so both forms stay.
ARBEITSAGENTUR_SEARCH_QUERIES = [
    "Data Scientist",
    "Data Analyst",
    "Machine Learning",
    "Machine Learning Engineer",
    "Machine Learning Ingenieur",
    "Deep Learning",
    "KI Entwickler",   # the hyphenated form returns an identical result set
    "KI Ingenieur",
    "Künstliche Intelligenz",
    "Data Engineer",
    "Softwareentwickler Python",
    "Python Entwickler",
    "AI Engineer",
    "NLP",
    "Computer Vision",
    "MLOps Engineer",
    "Business Intelligence",
    *GRADUATE_PROGRAM_QUERIES_DE,
    *GRADUATE_PROGRAM_QUERIES_EN,
]
ARBEITSAGENTUR_LOCATION = "Deutschland"

# --- Xing (sources/xing.py) ---
# Same intent as the LinkedIn queries. LAST_24_HOURS suits four runs a day.
XING_SEARCH_QUERIES = list(LINKEDIN_SEARCH_QUERIES)
XING_SINCE_PERIOD = os.environ.get("XING_SINCE_PERIOD") or "LAST_24_HOURS"
# Xing lists Austrian and Swiss jobs in German searches. Checked on the card before
# the detail fetch; the job page's addressCountry catches the rest.
XING_FOREIGN_PLACES = [
    "wien", "vienna", "graz", "linz", "salzburg", "innsbruck", "österreich", "austria",
    "zürich", "zurich", "basel", "bern", "genf", "geneva", "lausanne", "luzern",
    "schweiz", "switzerland",
]

# --- StepStone (sources/stepstone.py) ---
STEPSTONE_SEARCH_QUERIES = list(LINKEDIN_SEARCH_QUERIES)

# --- Career-page feeds (sources/ats) ---
# A feed lists every opening at the company, worldwide, so postings are kept only
# when the title is on-topic and the location is German or remote-open-to-Germany.
# Matched as whole words (sources/intent.py), so "AI" never matches "Mail".
ATS_TITLE_TERMS = sorted({
    *(q.lower() for q in LINKEDIN_SEARCH_QUERIES),
    *(q.lower() for q in ARBEITSAGENTUR_SEARCH_QUERIES),
    "data", "machine learning", "ml", "ai", "ki", "künstliche intelligenz",
    "analytics", "llm", "nlp", "genai",
})
# Whole-word matches on a location part. "de" catches "Hamburg, DE".
ATS_LOCATION_KEEP = [
    "germany", "deutschland", "de",
    "berlin", "hamburg", "münchen", "munich", "köln", "cologne", "frankfurt",
    "stuttgart", "düsseldorf", "dusseldorf", "leipzig", "dortmund", "essen", "bremen",
    "dresden", "hannover", "nürnberg", "nuremberg", "duisburg", "bochum", "wuppertal",
    "bielefeld", "bonn", "münster", "karlsruhe", "mannheim", "augsburg", "wiesbaden",
    "mainz", "aachen", "heidelberg", "darmstadt", "regensburg", "freiburg", "potsdam",
    "walldorf", "ingolstadt", "erlangen", "jena", "kiel", "braunschweig",
]
# A remote posting is kept unless it names one of these regions.
# A company with no detectable feed is probed again only after this many days.
ATS_PROBE_RETRY_DAYS = 30
# Pause between companies; the feeds are public APIs, this is politeness only.
ATS_COMPANY_DELAY_SECONDS = 1
# Big employers list thousands of postings; 10 pages of 100 covers the recent ones.
ATS_SMARTRECRUITERS_MAX_PAGES = 10
ATS_LOCATION_DROP = ["us", "usa", "united states", "uk", "united kingdom", "india",
                     "canada", "latam", "apac", "americas", "north america"]
ARBEITSAGENTUR_PAGE_SIZE = 100
# Day granularity is all the API offers. 1 suits a pipeline that runs several times
# a day; raise it to 2-3 as insurance if scheduled runs start getting dropped —
# dedup absorbs the repeats and no LLM call is made for a job already stored.
ARBEITSAGENTUR_PUBLISHED_SINCE_DAYS = _int_env("ARBEITSAGENTUR_PUBLISHED_SINCE_DAYS", 1)
ARBEITSAGENTUR_REQUEST_DELAY = 0.3  # politeness only; the API imposes no rate limit

# --- Application packs (Step C.1) ---
# The answers themselves are personal (salary expectation, permit status, notice
# period) and this repository is public, so only the PATH lives here. Copy
# application_answers.json.example to this file and fill it in; it is gitignored,
# like candidate_profile.json.
APPLICATION_ANSWERS_PATH = "application_answers.json"

# Pre-made CVs, picked by keyword on the job title — no generation, no LLM.
# First match wins, so put the specific keywords first. Files are resolved inside
# CV_LIBRARY_DIR. Left empty deliberately: the routing is yours to define, and a
# guessed mapping would send the wrong CV, which is worse than being asked to pick.
#   e.g. [("data engineer", "cv_data_engineering.pdf"), ("data", "cv_data_science.pdf")]
CV_LIBRARY_DIR = "cv"
CV_KEYWORD_ROUTES = []
CV_DEFAULT = None  # e.g. "cv_general.pdf"

# --- Auto apply ---
# Only the owner's per-job click queues anything; these settings bound what a
# queued job may do.
# ATSs where the worker may press the final submit. Everything else stops before it
# and reports ready_to_submit for the owner to press.
AUTO_SUBMIT_ATS = {"greenhouse", "lever", "ashby"}
# iCIMS forces an account per employer and fights automation: applied to by hand.
# Outcomes the owner may retry with another click.
AUTO_APPLY_REQUEUE_STATES = {"ready_to_submit", "verify", "failed"}
# Single source of truth; db.supabase_utils validates writes against it.
AUTO_APPLY_STATUSES = ("queued", "running", "applied", "ready_to_submit", "verify",
                       "dead", "email", "failed")
# A standing cover letter reused as-is; the worker never writes motivation text.
GENERIC_COVER_LETTER_PATH = os.environ.get("GENERIC_COVER_LETTER_PATH") or None
AUTO_APPLY_WORK_DIR = os.environ.get("AUTO_APPLY_WORK_DIR") or os.path.join(
    os.path.expanduser("~"), ".job-scraper", "apply-work")
AUTO_APPLY_TIMEOUT_MINUTES = 15
AUTO_APPLY_MAX_TURNS = 80
AUTO_APPLY_MAX_COST_USD = 3.0
PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp@0.0.83"


def _find_claude_bin() -> str:
    """Locate the claude executable: env, PATH, the VS Code extension, then the bare name.

    The extension ships its own native binary, which is often the only copy on a
    machine where claude is not on PATH. Several versions can sit side by side, so
    the newest by modification time wins: folder-name versions would need a parse
    that breaks on pre-release suffixes, and mtime tracks the latest install.
    """
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    found = shutil.which("claude")
    if found:
        return found
    pattern = os.path.join(os.path.expanduser("~"), ".vscode", "extensions",
                           "anthropic.claude-code-*", "resources", "native-binary",
                           "claude.exe")
    matches = glob.glob(pattern)
    if matches:
        return max(matches, key=os.path.getmtime)
    return "claude"


CLAUDE_BIN = _find_claude_bin()

# Extra origins the browser may reach per ATS, beyond the employer's own page
# (captcha, asset and upload hosts). Only these three were verified with
# --allowed-origins on 2026-10-18; other ATSs run without the browser fence
# (the worker decides that from this dict's keys).
AUTO_APPLY_ORIGINS = {
    "greenhouse": ["https://*.greenhouse.io", "https://*.recaptcha.net",
                   "https://*.gstatic.com", "https://*.amazonaws.com"],
    "lever": ["https://*.lever.co", "https://*.hcaptcha.com", "https://hcaptcha.com"],
    "ashby": ["https://*.ashbyhq.com", "https://*.ashbyprd.com",
              "https://*.recaptcha.net", "https://*.gstatic.com",
              "https://*.amazonaws.com"],
}

# Where a confirmation page may live for `applied` to be believed: the ATS's own
# hosts only. AUTO_APPLY_ORIGINS also lists captcha/asset/S3 hosts anyone can use.
AUTO_APPLY_SUBMIT_ORIGINS = {
    "greenhouse": ["https://*.greenhouse.io"],
    "lever": ["https://*.lever.co"],
    "ashby": ["https://*.ashbyhq.com"],
}

# Sender domains the verification-code mailbox trusts per ATS, on top of the
# apply URL's own host. Passed to the mail_codes server so a phished mail from
# anywhere else can never feed the worker a code or a link.
AUTO_APPLY_MAIL_DOMAINS = {
    "greenhouse": ["greenhouse.io", "greenhouse-mail.io"],
    "lever": ["lever.co", "hire.lever.co"],
    "ashby": ["ashbyhq.com"],
    "workday": ["myworkday.com", "myworkdayjobs.com"],
    "successfactors": ["successfactors.com", "successfactors.eu", "sap.com"],
    "personio": ["personio.de", "personio.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "teamtailor": ["teamtailor.com"],
    "join": ["join.com"],
}

# --- Email alerts for strong matches ---
# The pipeline runs unattended four times a day; without a nudge, a job scored at
# 09:00 is not seen until the evening, and being early to a posting is most of the
# advantage. One mail per run, only when that run produced a match at or above the
# threshold — silence means there was nothing worth the interruption.
#
# Credentials come from the environment (GitHub secrets / .env), never from here:
# this repository is public. Any SMTP provider works — Gmail with an App Password,
# Outlook, Resend, SendGrid, Postmark.
EMAIL_ALERTS_ENABLED = _bool_env("EMAIL_ALERTS_ENABLED", False)
EMAIL_ALERT_MIN_SCORE = _int_env("EMAIL_ALERT_MIN_SCORE", 70)
# Lower bar for English-language ads: both interviews so far came from English ads
# scored 58, which the general bar never mailed. Never higher than the general bar.
EMAIL_ALERT_MIN_SCORE_ENGLISH = _int_env("EMAIL_ALERT_MIN_SCORE_ENGLISH", 55)
# How many jobs the mail lists in full before falling back to a count. The mail is
# a nudge to open the queue, not the queue itself.
EMAIL_ALERT_MAX_JOBS = _int_env("EMAIL_ALERT_MAX_JOBS", 10)
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = _int_env("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
# Inbox the mail_codes server reads verification codes from (laptop-only, used by
# the auto-apply worker). Same mailbox as SMTP_USER; IMAP_PASSWORD falls back to the
# SMTP app password because Gmail uses one app password for both.
IMAP_HOST = os.environ.get("IMAP_HOST") or "imap.gmail.com"
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD") or SMTP_PASSWORD
# Default the envelope to the authenticated mailbox: for the single-user case they
# are the same address, and most providers reject a From they do not own anyway.
EMAIL_FROM = os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_USER")
EMAIL_TO = os.environ.get("EMAIL_TO")

# --- Manual Jobs (any source) ---
MANUAL_JOBS_PATH = "manual_jobs.json"

# --- Candidate Profile ---
# Untracked and gitignored: it holds residence-permit status, salary strategy and contract
# deadlines. Copy candidate_profile.json.example to this path and fill it in.
CANDIDATE_PROFILE_PATH = "candidate_profile.json"

# --- Profile onboarding (the Set up page) ---
# One call reads the CV and drafts the profile. The cheap screening model is
# enough for extraction; point ONBOARDING_MODEL at LLM_MODEL if the parsed CV
# comes back thin.
ONBOARDING_MODEL = os.environ.get("ONBOARDING_MODEL") or LLM_SCREEN_MODEL
# The last draft, kept so that editing answers later needs no second call. It
# holds the parsed CV, so it is gitignored like the profile itself.
ONBOARDING_DRAFT_PATH = "onboarding_draft.json"

# --- Processing Limits ---
SCRAPING_SOURCES = ["linkedin", "arbeitsagentur", "ats", "xing", "stepstone"]
# Indeed was removed in Step A.0: every search came back 403/401 from GitHub-hosted
# runners for at least twelve weeks, yielding zero jobs while the run stayed green.
# About a quarter of screened jobs pass, so a full screen of 100 yields ~25. A
# passer past this cap is not scored that run and is screened again the next.
JOBS_TO_SCORE_PER_RUN = _int_env("JOBS_TO_SCORE_PER_RUN", 30)
# Screened and scored only after every other source's unscored jobs. Nearly all
# German-language, and German-language ads have produced no interview so far.
SCORE_LAST_PROVIDERS = ["arbeitsagentur"]
JOBS_TO_CUSTOMIZE_PER_RUN = 3  # only for running resume.custom_resume_generator by hand
# The queue suggests a tailored CV (Write my CV) at or above this score. Nothing
# is tailored automatically: the scheduled pipeline has no tailoring step.
RESUME_CUSTOMIZATION_MIN_SCORE = 55
# Score each tailored CV as well. Off: the model scoring a CV it rewrote for that
# posting measures echo, not fit, and the figure is only ever recorded beside the
# real score (score_breakdown.custom_cv_score), never in place of it.
RESCORE_WITH_CUSTOM_RESUME = _bool_env("RESCORE_WITH_CUSTOM_RESUME", False)

# --- Apply-queue gating on interview odds ---
# The recommendation downgrade is relative to the run, not absolute: an absolute
# floor of 0.10 fired on every job ever scored (observed max p_as_is = 0.09), so
# nothing could reach apply_now. Only the weakest slice of each run is downgraded.
P_INTERVIEW_PERCENTILE_FLOOR = 40  # downgrade jobs below this percentile of the run's p_after_fixes
P_INTERVIEW_MIN_BATCH = 10         # below this many scored jobs, percentiles are noise -> absolute fallback
P_INTERVIEW_ABSOLUTE_FLOOR = 0.10  # the old absolute rule, kept only as the small-batch fallback
# The percentile is taken over this run plus the most recent scored rows, because
# a four-times-a-day run scores ~8 jobs and never reached P_INTERVIEW_MIN_BATCH on
# its own. Rows, not jobs: screen-only rows have no odds and are skipped.
P_INTERVIEW_REFERENCE_ROWS = 400
MAX_JOBS_PER_SEARCH = {
    # Raised from 5: every run dropped 11-38 new LinkedIn postings at this cap,
    # and LinkedIn carries nearly all the English-language ads (213 of 218).
    "linkedin": _int_env("LINKEDIN_MAX_JOBS_PER_SEARCH", 10),
    # Higher than LinkedIn's: a JSON API with no politeness delays and no blocking,
    # so the only cost of a larger number is the screening pass downstream.
    "arbeitsagentur": _int_env("ARBEITSAGENTUR_MAX_JOBS_PER_SEARCH", 25),
    # Every new Xing job costs one page fetch with a 3-8 s pause, so 10 per query
    # keeps a 14-query run near ten minutes.
    "xing": _int_env("XING_MAX_JOBS_PER_SEARCH", 10),
    "stepstone": _int_env("STEPSTONE_MAX_JOBS_PER_SEARCH", 10),
}

# =================================================================
# 3. ADVANCED SYSTEM SETTINGS (Modify with Caution)
# =================================================================
LLM_MAX_RPM = 10
LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 10
LLM_DAILY_REQUEST_BUDGET = 0
# Self-imposed pacing, not a provider limit. At 8s this added ~11 minutes of pure
# sleep to an 86-job backfill; 2s is still well inside Sonnet's rate limits and
# keeps consecutive calls inside the 5-minute prompt-cache window.
LLM_REQUEST_DELAY_SECONDS = 2

# Offset of the last results page fetched; pages are 10 apart. 1 meant page one
# only, so every run fetched exactly 28 queries x 10 = 280 and stopped there.
# 10 adds the second page. Env-overridable, so a run that starts getting blocked
# can be dialled back to 0 without a commit.
LINKEDIN_MAX_START = _int_env("LINKEDIN_MAX_START", 10)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15
# Jobs whose detail fetch still fails (usually a 429) are retried once more after
# the rest of the query's jobs, following this cooldown, instead of being dropped.
DEFERRED_RETRY_COOLDOWN_SECONDS = 90

JOB_EXPIRY_DAYS = 30
JOB_CHECK_DAYS = 3
JOB_DELETION_DAYS = 60
JOB_CHECK_LIMIT = 50
ACTIVE_CHECK_TIMEOUT = 20
ACTIVE_CHECK_MAX_RETRIES = 2
ACTIVE_CHECK_RETRY_DELAY = 10

