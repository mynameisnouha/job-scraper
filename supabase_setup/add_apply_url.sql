-- Run this once in the Supabase SQL editor. Safe to re-run.
-- The employer's own application form, kept apart from job_url (the posting page).
-- ats_type is the applicant tracking system behind apply_url (sources/ats_detect.py).
-- linkedin_apply_mode says whether a LinkedIn ad applies offsite or via Easy Apply;
-- LinkedIn's guest page never carries the employer URL itself.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS apply_url text,
    ADD COLUMN IF NOT EXISTS ats_type text,
    ADD COLUMN IF NOT EXISTS linkedin_apply_mode text;

CREATE INDEX IF NOT EXISTS jobs_ats_type_idx ON public.jobs (ats_type);
