-- Run this once in the Supabase SQL editor. Safe to re-run.
-- Companies whose career pages are polled directly (sources/ats). One row per
-- employer, keyed by the same normalized name dedup uses (sources/dedup.py).
CREATE TABLE IF NOT EXISTS public.companies (
    id bigserial PRIMARY KEY,
    name text,
    name_normalized text UNIQUE NOT NULL,
    ats_type text,
    ats_slug text,
    careers_url text,
    poll_enabled boolean NOT NULL DEFAULT false,
    last_polled_at timestamptz,
    last_probe_at timestamptz,
    last_probe_result text,
    notes text
);

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS company_id bigint REFERENCES public.companies(id);

-- Optional, after `python -m maintenance.detect_ats` has filled the table: link the
-- jobs already stored. Unmatched rows stay null rather than being guessed.
-- UPDATE public.jobs j SET company_id = c.id
-- FROM public.companies c
-- WHERE j.company_id IS NULL AND lower(j.company) = lower(c.name);
