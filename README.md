# Job Scraper

An automated job pipeline for the German market: it finds postings, scores each
one against your CV with an LLM, ranks them, and gives you a keyboard-driven
queue to work through. Built for volume — the goal is applications sent per
week, not a prettier database.

Four times a day, unattended, it:

```
scrape  →  dedup  →  screen (cheap LLM)  →  score (full LLM)  →  rank  →  alert
LinkedIn DE                 hard gates          0-100 + breakdown      email
Arbeitsagentur              + rough band        + "why me" pitch       if 70+
```

Everything lands in Supabase. You then open the Streamlit app and work the
queue: read the verdict, open the posting, mark applied or skip with a reason.

---

## Features

### Finding jobs

- **Two sources.** LinkedIn (Germany) and the Bundesagentur für Arbeit Jobsuche
  API — the second is free for employers to post to, which is why it carries the
  Mittelstand that never reaches LinkedIn. ([sources/scraper.py](sources/scraper.py),
  [sources/arbeitsagentur.py](sources/arbeitsagentur.py))
- **Cross-source dedup.** The same opening arrives as "YPOG GmbH / AI/ML Engineer
  (m/w/d)" from one source and "YPOG / AI/ML Engineer" from another. Company and
  title are normalised so it stays one row, and the employer-nearest URL wins —
  applying direct beats applying through an aggregator. ([sources/dedup.py](sources/dedup.py))
- **A source can't die quietly.** Every run accounts for each posting it fetched:
  already stored, filtered (with the reason), or saved. A source that returns
  nothing at all fails the run instead of showing green. ([sources/scrape_guard.py](sources/scrape_guard.py))
- **Manual jobs.** Drop a posting you found yourself into `manual_jobs.json` and
  it gets scored like any other. ([scoring/manual_jobs.py](scoring/manual_jobs.py))
- **Graduate and trainee programmes.** Structured intakes (Traineeprogramm,
  graduate programme, AI residency) are searched for by name in both languages,
  tagged from the title at scrape time, and scored as what they are: the intake
  date is checked against your availability, eligibility windows ("max. 2 Jahre
  Berufserfahrung") become a gate, and your experience counts as differentiation
  in a pool of fresh graduates rather than as a seniority gap. Arbeitsagentur
  files half of them under its Praktikum/Trainee category, which used to be
  dropped wholesale; a programme there is now kept while a Praktikum is not.
  The queue can show programmes only, roles only, or both.
  ([sources/role_type.py](sources/role_type.py))

### Scoring

- **Two-stage scoring, so the spend goes where it matters.** A cheap model
  screens every new job for hard gates and a rough band; only what passes gets a
  full scoring call. Failures are stored with a capped score and never looked at
  again. ([scoring/score_jobs.py](scoring/score_jobs.py))
- **A breakdown, not just a number.** Each job gets 0–100 plus what to lead with,
  what they'll push back on, what to fix before applying, estimated hours of
  effort, interview odds, and a recommendation. ([review/job_view.py](review/job_view.py))
- **A "why me" pitch** is written for strong matches — 3–4 sentences mapping your
  evidence to their top requirements, ready for the Anschreiben.
- **Interview odds are graded on a curve.** The weakest slice of each run is
  downgraded relative to that run, because an absolute floor fired on every job
  ever scored and nothing could reach "apply now".
- **Any LLM provider.** Gemini, OpenAI, Anthropic, Groq, Ollama and ~400 more
  through LiteLLM, with rate limiting, backoff and a daily request budget.
  ([scoring/llm_client.py](scoring/llm_client.py))

### Working the queue — `streamlit run ui_app.py`

Five screens, grouped in the sidebar by how often you open them: *Jobs to
apply* and *Write my CV* every day, *Where I applied* every week, *Is the score
right?* and *Which CV to use* every month. The look — palette, type, the pill
controls and surface cards — lives in [review/theme.py](review/theme.py) and
[.streamlit/config.toml](.streamlit/config.toml); the pages compose HTML
fragments from there rather than styling inline.

- **Jobs to apply.** Every card leads with the verdict: the score as a disc
  with its band ("Strong", "Worth it", "Marginal", "Long shot" — set against
  the observed distribution, not a school scale) and its place in the corpus,
  then the one-line verdict and the gates as chips, German always first. Sort
  by best match, least effort or newest; a strip under the controls says what
  the filters are hiding ("Older than 24 hours · 194", "Score under 70 · 12")
  and each chip drops that filter with one click, so a short queue never reads
  as an empty market.
- **One at a time.** The default view: one job, driven from the keyboard —
  `a` applied, `s` skip, `o` open the posting, `p` build the application pack,
  `c` tailor a CV for it, `j`/`k` to move — with the pitch, the context and the
  key legend in a side rail. The cursor is anchored to a job, not a position,
  so it survives the queue refreshing underneath you.
  ([review/apply_queue.py](review/apply_queue.py))
- **Skips carry a reason** — asked at the moment of the decision, as chips
  (`1`–`9` from the keyboard) — because "I skipped every job needing C1" is a
  finding, not bookkeeping. One-step undo, and the row is never deleted.
- **Found timestamps.** Under 24 hours old a job shows the clock time and how
  long ago; past that, just the date. Being early to a posting is most of the
  advantage.
- **Application packs.** One button writes a folder with the answers every German
  form asks for (Gehaltsvorstellung, earliest start, permit status, notice
  period), a checklist, the pitch, and the right CV. No LLM, no generation — it
  collects what already exists. ([review/application_pack.py](review/application_pack.py))
- **Where I applied.** Each application is a record, not a form: stage as a
  timeline, how long it has been quiet, what you led with and what they pushed
  back on. Tabs split open from needs-chasing from resolved; stage, reason and
  notes are edited behind an *Update* button. Applications silent for 30+ days
  are offered for closing out, never closed automatically.
  ([review/application_view.py](review/application_view.py))
- **Is the score right?** Leads with the answer — "+7pt overconfident: it
  promises 31% and delivers 24%" — then interview rate by score band and the
  blockers: rejection reasons and your own skip reasons on one chart, because
  the two together are the real filter. Unresolved applications are excluded —
  you haven't heard back yet, which is not the same as a no.
  ([review/calibration.py](review/calibration.py))
- **Which CV to use.** The postings worth applying to, grouped into a handful
  of role archetypes — one base CV each, instead of one generic CV for
  everything. Per archetype: how many postings, the best score, the skills to
  lead with ranked by how over-represented they are, what the roles actually
  are, and the covered jobs. Reads the artefacts written by
  `python -m clustering.run`; the page never clusters or calls an LLM itself.
  ([clustering/](clustering/))
- **Your CV against each archetype.** `python -m clustering.cv_fit` maps your CV
  onto the same skill vocabulary as the postings, then reports per archetype what
  you cover, what is true of you but missing from the CV, and what is genuinely
  absent — each gap weighted by how many postings actually ask for it.
  ([clustering/cv_fit.py](clustering/cv_fit.py))
- **Write my CV.** Reachable from any job in the queue via **Tailor a CV for
  this** in the overflow menu (or `c`). Three steps with their state on a bar —
  fill the gaps, write and argue, read and send. Pick one posting and get a tailored CV and Anschreiben,
  written only from a fact base about you — every line cites the fact it rests
  on, so nothing can be invented — then argued over by a simulated hiring manager
  until the objections stop being new. Asks you about gaps first, and keeps the
  answers. ([tailor/](tailor/))

### Staying on top of it

- **Email alerts.** One mail at the end of a run listing the jobs that run scored
  at or above your threshold. Nothing clears the bar, no mail. Off by default —
  see [Email alerts](#email-alerts). ([scoring/notify.py](scoring/notify.py))
- **Housekeeping.** Old postings expire, closed ones are marked removed, and
  long-dead rows are deleted. ([maintenance/job_manager.py](maintenance/job_manager.py))
- **A static dashboard** is published as a workflow artifact if you want a
  read-only view without running Streamlit. ([review/dashboard.py](review/dashboard.py))
- **Resume customization** tailors your CV per job and exports a compact text
  version to Supabase Storage, ready to paste into a layout tool.
  ([resume/custom_resume_generator.py](resume/custom_resume_generator.py))

---

## Setup

You need a [Supabase](https://supabase.com) project (free tier is enough) and an
API key for one LLM provider.

**1. Install**

```bash
pip install -r requirements.txt
```

**2. Create the database**

In the Supabase SQL editor, run `supabase_setup/init.sql`, then each migration in
`supabase_setup/` (they are idempotent and safe to re-run):

```
add_score_breakdown.sql       full LLM breakdown alongside the numeric score
add_why_me_pitch.sql          the generated pitch for strong matches
add_application_outcomes.sql  interview / rejection / offer tracking
add_dismissal.sql             soft skip, with a reason
add_deleted_jobs.sql          hard delete, with a tombstone so it stays deleted
                              (re-run it: the low-score purge added three columns)
add_alt_sources.sql           cross-source dedup
add_program_type.sql          graduate / trainee programme tag
raise_customization_threshold.sql
```

**3. Configure credentials**

Copy `.env.example` to `.env` and fill in Supabase and your LLM key. Nothing
secret belongs in `config.py` — that file is committed.

**4. Add your CV**

Upload your resume PDF to the `resumes` bucket in Supabase Storage, then:

```bash
python -m resume.resume_parser
```

This parses it into structured data the scorer uses.

**5. Fill in your profile and answers**

```bash
cp candidate_profile.json.example candidate_profile.json
cp application_answers.json.example application_answers.json
```

Both are gitignored — they hold permit status, salary strategy and deadlines.
The profile shapes scoring; the answers fill application packs. Point
`CV_KEYWORD_ROUTES` in `config.py` at your CV files if you keep more than one.

**6. Tune the search**

In `config.py`, edit the search queries, location and per-run caps. Leave the
rate-limiting and retry values alone — they are calibrated to avoid getting
blocked.

**7. Run it**

Locally:

```bash
python -m sources.scraper        # find jobs   (--source linkedin | arbeitsagentur)
python -m scoring.score_jobs     # screen + score
python -m maintenance.job_manager  # housekeeping
streamlit run ui_app.py          # work the queue
```

Every command runs from the repository root — the packages are found relative to
it, and so are `output/`, `cv/` and the JSON config files.

On GitHub Actions: add your `.env` values as repository secrets, enable Actions,
and `run_all.yml` runs the whole pipeline four times a day on its own.

---

## Email alerts

Off by default. When enabled, `scoring/score_jobs.py` sends one mail at the end of a run
listing the jobs *that run* scored at or above `EMAIL_ALERT_MIN_SCORE` (default
70). A run with nothing above the bar sends nothing. A job is scored once, so no
job is ever alerted twice, and an unreachable mail server is logged rather than
failing the run.

Any SMTP provider works. For Gmail, turn on 2-Step Verification and create an
[App Password](https://myaccount.google.com/apppasswords) — the account password
is always refused.

```
EMAIL_ALERTS_ENABLED=true
EMAIL_ALERT_MIN_SCORE=70
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587                 # 465 for implicit TLS; anything else uses STARTTLS
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_app_password
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@gmail.com        # comma-separated for several recipients
```

Check it before trusting a scheduled run:

```bash
python -m scoring.notify --test
```

For GitHub Actions, add the `SMTP_*` and `EMAIL_FROM`/`EMAIL_TO` values as
repository **secrets** and the two `EMAIL_ALERT*` values as repository
**variables** (Settings → Secrets and variables → Actions).

---

## Automation

| Workflow | What it does |
|---|---|
| `run_all.yml` | The whole pipeline, 4×/day: scrape (one runner per source, in parallel) → score → housekeeping → dashboard |
| `scrape_jobs.yml` / `score_jobs.yml` / `job_manager.yml` | The individual stages, for running one by hand |
| `parse_resume.yml` | Re-parse your CV after you upload a new one |
| `hourly_resume_customization.yml` | Tailor CVs for high-scoring jobs |
| `backfill.yml` | Widen the date window for one source to catch up |
| `rescore_existing.yml` / `rescore_degraded.yml` | Re-score stored jobs after a scorer change |
| `dashboard.yml` | Rebuild the static dashboard |

Runs never overlap, and a failed scrape leg does not stop scoring — whatever
landed still deserves to be looked at.

## Maintenance scripts

```bash
python -m maintenance.check_scoring_health   # is the scorer still producing the fields calibration needs?
python -m maintenance.dedup_existing         # collapse duplicates already stored (dry run; --apply to write)
python -m maintenance.rescore_degraded       # re-score rows missing calibration fields
python -m maintenance.mark_applied <job_id>  # mark applied without opening the UI
```

## Tests

```bash
python -m pytest tests/ -q
```

447 tests, no credentials needed — every external call is faked, including the
Streamlit UI, which is rendered headlessly.

## Project structure

Eight packages, each answering one question. Only three Python files sit at the
root, and each is there for a reason.

```
config.py          the file you edit: search queries, caps, thresholds (no secrets — it is committed)
models.py          the shared Pydantic schemas; both the CV side and the scoring side speak them
ui_app.py          the Streamlit entry point, so `streamlit run ui_app.py` needs no path juggling

sources/           where jobs come from
    scraper.py         LinkedIn + the per-source runners behind `--source`
    arbeitsagentur.py  Bundesagentur für Arbeit Jobsuche API
    dedup.py           canonical job identity across sources
    scrape_guard.py    per-source accounting; fails a run that fetched nothing
    user_agents.py     user-agent pool

scoring/           posting → ranked, explained score
    score_jobs.py      the screening pass, the full scoring pass, the pitches
    llm_client.py      LiteLLM wrapper: rate limits, retries, daily budget
    manual_jobs.py     jobs you added by hand, scored like the rest
    notify.py          email alert when a run turns up a strong match

resume/            your CV
    resume_parser.py             PDF → structured data
    custom_resume_generator.py   per-job tailoring → compact text export
    pdf_generator.py             ReportLab layout (parked — nothing calls it yet)

tailor/            one posting → a CV and cover letter (see tailor/README.md)
    facts.py           the fact base: what is true about you, atomised and tiered
    interview.py       asks about gaps the posting needs and the base cannot answer
    writer.py          composes CV + Anschreiben, every line citing facts
    verify.py          deterministic honesty checks — citations, numbers, phrasing
    judge.py           the hiring manager: objections, never a score
    loop.py            write → verify → judge → revise, and when to stop
    documents.py       the document shapes, and rendering them to text
    store.py           saved applications, and the non-claim personal details

clustering/        postings → a few CV archetypes (see clustering/README.md)
    corpus.py          gates the scored jobs down to the ones worth a CV
    schema.py          the closed vocabulary a posting is normalised into
    extract.py         one cheap LLM call per posting, cached by job_id
    features.py        blocked, weighted, rarity-scaled feature space
    cluster.py         PCA + k-means, with bootstrap stability and per-job margins
    report.py          centroids read back as shares and lift over the corpus
    cv_fit.py          scores your CV against each archetype: what is there, what is not
    results.py         loads a finished run for the Streamlit page
    run.py             `python -m clustering.run`

review/            everything behind the Streamlit app
    apply_queue.py       ordering, cursor, date windows, skip reasons
    job_view.py          a score breakdown → the few lines worth reading
    application_pack.py  assembles one application's folder
    calibration.py       is the scorer predictive? (Brier, interview rate)
    dashboard.py         the static HTML dashboard

db/                all Supabase access — nothing else talks to the database
    supabase_utils.py

maintenance/       standalone commands over the stored corpus
    job_manager.py, dedup_existing.py, rescore_existing.py,
    rescore_degraded.py, check_scoring_health.py, mark_applied.py

supabase_setup/    schema + idempotent migrations
tests/             credential-free test suite
```

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
