-- Run this once in the Supabase SQL editor.
-- Tracks what actually happened after applying, so scoring accuracy can be
-- measured against real outcomes (interview/rejection/offer) instead of guessed.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS application_stage text,
    ADD COLUMN IF NOT EXISTS stage_updated_at timestamptz,
    ADD COLUMN IF NOT EXISTS rejection_reason text,
    ADD COLUMN IF NOT EXISTS outcome_notes text;

-- Backfill: any job already marked applied gets an initial stage so it shows
-- up in the outcome-tracking UI right away.
UPDATE public.jobs
SET application_stage = 'applied',
    stage_updated_at = application_date
WHERE status = 'applied' AND application_stage IS NULL;
