-- Run this once in the Supabase SQL editor.
-- Postings you delete outright, and the tombstone that keeps them deleted.
--
-- Distinct from a dismissal (add_dismissal.sql), and the difference is what the
-- row means. A dismissal is a DECISION about a real job you considered and
-- rejected — the row stays, because a high-scoring job you rejected on sight is
-- the scorer and you disagreeing, and that disagreement is measurable. A delete
-- is for postings that should never have been in the corpus at all: agency
-- reposts, duplicates the dedup missed, a mis-scraped listing, something in the
-- wrong field entirely. Keeping those as "decisions" poisons every statistic
-- built on dismissals.
--
-- The tombstone is why this is a table rather than a DELETE. Drop the row alone
-- and the next scrape finds the posting missing from the dedup set, re-inserts
-- it, screens it and scores it — so a delete would cost money and then hand the
-- posting straight back. The scrapers read these keys alongside the live ones,
-- so a deleted posting is dropped before any model sees it.
CREATE TABLE IF NOT EXISTS public.deleted_jobs (
    job_id text PRIMARY KEY,
    company text,
    job_title text,
    -- Normalised company|title, as sources/dedup.py produces it. Stored rather
    -- than recomputed so the same posting re-scraped from another source, with
    -- a different id, is still recognised.
    dedup_key text,
    reason text,
    note text,
    deleted_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deleted_jobs_dedup_key_idx
    ON public.deleted_jobs (dedup_key);

-- Added with the automatic low-score purge. A purge removes ~1000 rows at a
-- time, and those rows carry the only record of what the cheap screen rejected:
-- score_jobs.py deliberately stores a breakdown even for screened-out jobs so
-- the corpus-wide language picture describes the whole scrape rather than only
-- the postings that passed. Keeping these three fields on the tombstone means a
-- purge costs the descriptions and the reasoning, not the distribution.
ALTER TABLE public.deleted_jobs
    ADD COLUMN IF NOT EXISTS resume_score integer,
    ADD COLUMN IF NOT EXISTS german_required text,
    ADD COLUMN IF NOT EXISTS jd_language text;
