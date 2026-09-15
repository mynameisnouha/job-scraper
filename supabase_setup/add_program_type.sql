-- Run this once in the Supabase SQL editor.
-- Tags structured graduate / trainee programmes so the scorer can judge them as
-- programmes (intake date, eligibility window, over-qualification risk) and the
-- queue can filter them. Set at scrape time from the title (sources/scraper.py);
-- NULL means a standard role. The pipeline runs without this column — the tag is
-- then silently dropped on save and the queue falls back to the title.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS program_type text;
COMMENT ON COLUMN public.jobs.program_type IS
    'graduate_program for structured graduate/trainee intakes, NULL for standard roles';
