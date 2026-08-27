-- Run this once in the Supabase SQL editor.
-- Stores the generated 3-4 sentence "why me" pitch for strong-match jobs (score >= 70,
-- recommendation strong_apply/apply). Shown on the dashboard; use it as the
-- Easy-Apply message / email intro / skeleton of an Anschreiben.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS why_me_pitch text;
