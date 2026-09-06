-- Step B.3: cross-source dedup.
--
-- The same opening reaches the corpus from several places (LinkedIn and
-- Arbeitsagentur both carry it, and Arbeitsagentur relists it under new reference
-- numbers). Dedup keeps one row; this column records where the other copies were,
-- so the duplicate URLs are still available without a second row competing for a
-- place in the queue.
--
-- Shape: [{"provider": "linkedin", "job_id": "...", "job_url": "...", "scraped_at": "..."}]
--
-- Idempotent — safe to run more than once.

alter table public.jobs
    add column if not exists alt_sources jsonb not null default '[]'::jsonb;

comment on column public.jobs.alt_sources is
    'Other sources carrying this same posting, collapsed by dedup.py. The surviving row keeps its own job_url; these are the alternates.';

-- Lets a lookup ask "is this posting already known under another source's URL?"
create index if not exists idx_jobs_alt_sources on public.jobs using gin (alt_sources);
