# Job Scraper

An automated job pipeline for the German market: it finds postings, scores each
one against your CV with an LLM, ranks them, and gives you a keyboard-driven
queue to work through. Built for volume — the goal is applications sent per
week, not a prettier database.

Four times a day, unattended, it:

```
scrape  →  dedup  →  screen (cheap LLM)  →  score (full LLM)  →  rank  →  alert
LinkedIn DE                 hard gates          0-100 + breakdown      email
Arbeitsagentur              + rough band                               if 70+
```

Everything lands in Supabase. You then open the Streamlit app and work the
queue: read the verdict, open the posting, mark applied or skip with a reason.

**More:** [Configuration](#configuration) · [CV tailoring](tailor/README.md) · [CV archetypes](clustering/README.md)

---

## Quick start

About 15 minutes. You need Python 3.11+, a free [Supabase](https://supabase.com)
project and an API key for one LLM provider (Anthropic by default). Run every
command from the repository root.

**1. Install**

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

**2. Fill in `.env`**

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (Supabase → Project Settings →
API), plus your LLM key. Another provider than Anthropic? See
[Switching LLM provider](#switching-llm-provider).

**3. Create the database**

In the Supabase SQL editor, run `supabase_setup/init.sql`, then every other file
in `supabase_setup/` in the order listed under
[Database](#database). All are safe to re-run.

**4. Choose what to search for** *(optional)*

The defaults search for data / ML / AI roles in Germany. To change that, edit the
search queries and location in [`config.py`](config.py), section 2.

**5. Set up your profile**

```bash
streamlit run ui_app.py
```

The app opens on **Set up**. Its status strip shows what is still missing, and
each red item says how to fix it. Then upload your CV, answer the questions
(most are pre-filled from it) and press **Save**. That costs one cheap model
call; changing an answer later costs nothing.

**6. Find and score jobs**

```bash
python -m sources.scraper          # find jobs   (--source linkedin | arbeitsagentur)
python -m scoring.score_jobs       # screen + score
```

Back in the app, **Jobs to apply** now has your queue.

To run this unattended four times a day, push the repository to GitHub, add your
`.env` values as secrets, and paste the line the **Set up** page gives you into
the `CANDIDATE_PROFILE_JSON` secret: see
[GitHub Actions](#github-actions).

---

## Configuration

Secrets go in `.env` (copied from `.env.example`, which lists every variable).
Search queries, caps and thresholds go in [`config.py`](config.py): section 2,
*User preferences*, is the part meant to be edited; section 3 is calibrated to
avoid being blocked. Your profile and form answers are written by the app's
**Set up** page.

### Database

Run `supabase_setup/init.sql` first, then these, in this order. All are
idempotent: re-running one is always safe, and it is the fix if a column is
reported missing.

| Migration | Adds |
|---|---|
| `add_score_breakdown.sql` | Full LLM breakdown alongside the numeric score |
| `add_why_me_pitch.sql` | Pitch column (no longer generated; older rows still have it) |
| `add_application_outcomes.sql` | Interview / rejection / offer tracking |
| `add_dismissal.sql` | Skip, with a reason |
| `add_deleted_jobs.sql` | Hard delete, with a tombstone so a job stays deleted |
| `add_alt_sources.sql` | Cross-source dedup |
| `add_program_type.sql` | Graduate / trainee programme tag |
| `raise_customization_threshold.sql` | Tailoring picks only jobs scored 70+ at the database level |
| `add_apply_url.sql` | Employer apply link and ATS type |
| `add_companies.sql` | Companies whose career pages are polled directly |

### Switching LLM provider

Any model [LiteLLM supports](https://docs.litellm.ai/docs/providers) works. Set the
provider's key and both models, for example in `.env`:

```
OPENAI_API_KEY=sk-...
LLM_MODEL=openai/gpt-4o
LLM_SCREEN_MODEL=openai/gpt-4o-mini
```

On GitHub Actions the workflows pass `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GROQ_API_KEY` and `LLM_API_KEY` through; to change models there, edit the
defaults in `config.py`. The CV tailor's judge model is set separately
(`TAILOR_JUDGE_MODEL`).

### Email alerts

Off by default. When enabled, the scoring run sends one mail at the end listing
the jobs *that run* scored at or above `EMAIL_ALERT_MIN_SCORE` (default 70).
English-language ads have a lower bar, `EMAIL_ALERT_MIN_SCORE_ENGLISH` (default
55, never above the general one), because both interviews so far came from
English ads scored 58. Below the general bar a job recommended *skip* is never
mailed. Set the English value equal to the general one to turn the lower bar
off. A run with nothing above the bar sends nothing. A job is scored once, so no
job is ever alerted twice, and an unreachable mail server is logged rather than
failing the run.

Any SMTP provider works. For Gmail, turn on 2-Step Verification and create an
[App Password](https://myaccount.google.com/apppasswords) — the account password
is always refused.

```
EMAIL_ALERTS_ENABLED=true
EMAIL_ALERT_MIN_SCORE=70
EMAIL_ALERT_MIN_SCORE_ENGLISH=55   # English-language ads; set to 70 to turn off
EMAIL_ALERT_MAX_JOBS=10            # jobs listed in full before falling back to a count
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587                      # 465 for implicit TLS; anything else uses STARTTLS
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_app_password
EMAIL_FROM=you@gmail.com           # defaults to SMTP_USER
EMAIL_TO=you@gmail.com             # comma-separated for several recipients
```

Check it before trusting a scheduled run:

```bash
python -m scoring.notify --test
```

### GitHub Actions

The scheduled pipeline runs on GitHub Actions. Fork or push the repository, enable
Actions, and add the values below under **Settings → Secrets and variables → Actions**.
`run_all.yml` then runs the whole pipeline four times a day.

Secrets:

| Secret | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | Yes | |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | |
| `ANTHROPIC_API_KEY` | For the default models | Or `OPENAI_API_KEY`, `GROQ_API_KEY`, `LLM_API_KEY`, `GEMINI_FIRST_API_KEY` |
| `CANDIDATE_PROFILE_JSON` | Yes | The full contents of your `candidate_profile.json` |
| `TAILOR_FACTS_JSON` | No | The full contents of `profile_facts.json` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` | For email alerts | |

Variables (not secret; all optional):

| Variable | Notes |
|---|---|
| `EMAIL_ALERTS_ENABLED` | `true` to turn on alerts |
| `EMAIL_ALERT_MIN_SCORE`, `EMAIL_ALERT_MIN_SCORE_ENGLISH` | Alert thresholds |
| `PURGE_MIN_SCORE`, `PURGE_LIMIT` | Housekeeping limits |

Workflows:

| Workflow | What it does |
|---|---|
| `run_all.yml` | The whole pipeline, 4×/day: scrape (one runner per source, in parallel) → score → housekeeping → dashboard |
| `scrape_jobs.yml` / `score_jobs.yml` / `job_manager.yml` | The individual stages, for running one by hand |
| `parse_resume.yml` | Re-parse your CV after you upload a new one |
| `backfill.yml` | Widen the date window for one source to catch up |
| `rescore_existing.yml` / `rescore_degraded.yml` | Re-score stored jobs after a scorer change |
| `dashboard.yml` | Rebuild the static dashboard |

Runs never overlap, and a failed scrape leg does not stop scoring — whatever
landed still deserves to be looked at.

> **This repository may be public.** Never commit `.env`, your personal JSON files,
> `cv/`, `output/` or `dashboard.html`. All are in `.gitignore`; keep them there.

---

## Auto apply

An agent can fill in and submit application forms for jobs you pick. Nothing is queued except by your click on **Auto apply** in the app (the `⋯` menu of a job).

One-time setup:

1. Run `supabase_setup/add_auto_apply.sql` in the Supabase SQL editor.
2. `pip install -r requirements-apply.txt`
3. Set in `.env` (names only here, see `.env.example`):
   - `ATS_EMAIL`, `ATS_PASSWORD`: an email and a password used only for job portals. Make the password unique.
   - `IMAP_PASSWORD`: a Gmail app password, used to read confirmation codes. Falls back to `SMTP_PASSWORD`.
   - `CLAUDE_BIN`: path to `claude.exe`, if it is not found automatically.
   - `GENERIC_COVER_LETTER_PATH` (optional): a cover letter file for forms that ask for one.

Usage:

1. Click **Auto apply** on a job. The button is greyed out with the reason when the job cannot be queued.
2. Run `python -m apply.runner`. Options: `--parallel`, `--max`, `--headless`, `--model`, `--dry-run`.
3. Finish the ones that stopped before submit with `python -m apply.review <job_id>`.

Greenhouse, Lever and Ashby are submitted automatically after your click. Every other ATS stops before the final submit, for you to finish. iCIMS is never automated. Questions the agent could not answer are listed on the queue page under *Questions the agent could not answer*; add the answer to `application_answers.json` under `extra`.

## Commands

| Command | What it does |
|---|---|
| `python -m sources.scraper` | Scrape all sources (`--source <name>` for one) |
| `python -m scoring.score_jobs` | Screen and score new jobs, then send the alert mail |
| `streamlit run ui_app.py` | The app: apply queue, applications, calibration, CVs |
| `python -m maintenance.job_manager` | Housekeeping: expire, mark closed, delete old |
| `python -m resume.resume_parser` | Re-parse your CV after uploading a new one |
| `python -m scoring.notify --test` | Send a test alert mail |
| `python -m clustering.run` | Group postings into CV archetypes |
| `python -m clustering.cv_fit` | Compare your CV against each archetype |
| `python -m maintenance.check_scoring_health` | Is the scorer still producing the fields calibration needs? |
| `python -m maintenance.dedup_existing` | Collapse duplicates already stored (dry run; `--apply` to write) |
| `python -m maintenance.rescore_degraded` | Re-score rows missing calibration fields |
| `python -m maintenance.mark_applied <job_id>` | Mark applied without opening the app |
| `python -m maintenance.detect_ats` | Find which companies publish a career feed (`--dry-run` to preview) |
| `python -m maintenance.backfill_apply_url` | Fill the employer apply link on stored jobs (dry run; `--apply` to write) |
| `python -m apply.resolve_link --login` | Once: log into LinkedIn in a browser profile kept outside the repo (`~/.job-scraper/browser`) |
| `python -m apply.resolve_link <job_id>` | Find a LinkedIn job's employer link in that browser (also the *Find employer link* button). No LLM; never clicks Easy Apply |

## Tests

```bash
python -m pytest tests/ -q
```

No credentials needed — every external call is faked, including the Streamlit
UI, which is rendered headlessly.

---

## Project structure

Eight packages, each answering one question. Only three Python files sit at the
root, and each is there for a reason.

```
config.py          the file you edit: search queries, caps, thresholds (no secrets — it is committed)
models.py          the shared Pydantic schemas; both the CV side and the scoring side speak them
ui_app.py          the Streamlit entry point, so `streamlit run ui_app.py` needs no path juggling

*.json.example     templates for your personal files (copy without .example; the copies are gitignored)
.env.example       template for your credentials

sources/           where jobs come from
    scraper.py         LinkedIn + the per-source runners behind `--source`
    arbeitsagentur.py  Bundesagentur für Arbeit Jobsuche API
    dedup.py           canonical job identity across sources
    role_type.py       tags graduate / trainee programmes by title
    scrape_guard.py    per-source accounting; fails a run that fetched nothing
    user_agents.py     user-agent pool

scoring/           posting → ranked, explained score
    score_jobs.py      the screening pass and the full scoring pass
    llm_client.py      LiteLLM wrapper: rate limits, retries, daily budget
    availability.py    today's date and your start date, in the form prompts need
    manual_jobs.py     jobs you added by hand, scored like the rest
    notify.py          email alert when a run turns up a strong match

resume/            your CV
    resume_parser.py             PDF → structured data
    custom_resume_generator.py   older per-job tailoring (not scheduled; Write my CV replaces it)
    pdf_generator.py             ReportLab layout (parked — nothing calls it yet)

tailor/            one posting → a CV and cover letter (see tailor/README.md)
    facts.py           the fact base: what is true about you, atomised and tiered
    harvest.py         facts the scorer already noticed, offered for confirmation
    interview.py       asks about gaps the posting needs and the base cannot answer
    writer.py          composes CV + Anschreiben, every line citing facts
    verify.py          deterministic honesty checks — citations, numbers, phrasing
    judge.py           the hiring manager: objections, never a score
    loop.py            write → verify → judge → revise, and when to stop
    documents.py       the document shapes, and rendering them to text
    store.py           saved applications, and the non-claim personal details
    settings.py        models and loop limits

clustering/        postings → a few CV archetypes (see clustering/README.md)
    corpus.py          gates the scored jobs down to the ones worth a CV
    schema.py          the closed vocabulary a posting is normalised into
    extract.py         one cheap LLM call per posting, cached by job_id
    features.py        blocked, weighted, rarity-scaled feature space
    cluster.py         PCA + k-means, with bootstrap stability and per-job margins
    report.py          centroids read back as shares and lift over the corpus
    cv_fit.py          scores your CV against each archetype: what is there, what is not
    results.py         loads a finished run for the Streamlit page
    settings.py        thresholds and worker count
    run.py             `python -m clustering.run`

review/            everything behind the Streamlit app
    apply_queue.py       ordering, cursor, date windows, skip reasons
    job_view.py          a score breakdown → the few lines worth reading
    application_pack.py  assembles one application's folder
    application_view.py  the "Where I applied" records
    calibration.py       is the scorer predictive? (Brier, interview rate)
    market.py            what the market asks for
    theme.py             palette, type and HTML fragments for the app
    dashboard.py         the static HTML dashboard

db/                all Supabase access — nothing else talks to the database
    supabase_utils.py

maintenance/       standalone commands over the stored corpus
    job_manager.py, dedup_existing.py, rescore_existing.py, rescore_degraded.py,
    check_scoring_health.py, mark_applied.py, purge_low_scores.py, purge_freelance.py

supabase_setup/    schema + idempotent migrations
.github/workflows/ the scheduled pipeline and one-off jobs
tests/             credential-free test suite
```

Generated at run time and gitignored: `output/` (application packs, dedup
backups), `dashboard.html`, `tailor/cache/`, `clustering/cache/cv_profile.json`.

Modules are run as `python -m <package>.<module>` from the repository root, which
is also what the workflows do.

## Licence

MIT — see [LICENSE](LICENSE).

## Acknowledgements

[LiteLLM](https://docs.litellm.ai/) for provider-agnostic LLM calls,
[Supabase](https://supabase.com/) for storage,
[Streamlit](https://streamlit.io/) for the UI,
[Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) and
[pdfplumber](https://github.com/jsvine/pdfplumber) for parsing.

## Disclaimer

For personal use. Scraping LinkedIn may be against their Terms of Service — use
this responsibly and at your own risk.
