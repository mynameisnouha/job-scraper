-- Run this once in the Supabase SQL editor.
-- Soft dismissal for the apply queue: a job you looked at and decided against.
--
-- Deliberately NOT a delete, and deliberately not `status` — the row stays as
-- evidence. A job the scorer ranked highly that you rejected on sight is the
-- disagreement worth measuring, and it is only visible if the row survives.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS dismissed_at timestamptz,
    ADD COLUMN IF NOT EXISTS dismissal_reason text;

-- The apply queue filters on this on every load.
CREATE INDEX IF NOT EXISTS jobs_dismissed_at_idx
    ON public.jobs (dismissed_at)
    WHERE dismissed_at IS NULL;
