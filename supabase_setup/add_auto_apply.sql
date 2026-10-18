-- Run this once in the Supabase SQL editor. Safe to re-run.
-- Auto-apply queue state, kept on the job row. Only the owner's per-job Auto apply
-- click queues a job; the laptop runner claims it (queued -> running) and writes
-- the outcome back. auto_apply_reason holds the why, or the question on 'verify'.
-- auto_apply_worker names the runner that claimed the row.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS auto_apply_status text,
    ADD COLUMN IF NOT EXISTS auto_apply_reason text,
    ADD COLUMN IF NOT EXISTS auto_apply_updated_at timestamptz,
    ADD COLUMN IF NOT EXISTS auto_apply_cost_usd numeric,
    ADD COLUMN IF NOT EXISTS auto_apply_worker text;

ALTER TABLE public.jobs DROP CONSTRAINT IF EXISTS jobs_auto_apply_status_check;
ALTER TABLE public.jobs
    ADD CONSTRAINT jobs_auto_apply_status_check CHECK (
        auto_apply_status IS NULL OR auto_apply_status IN
        ('queued', 'running', 'applied', 'ready_to_submit', 'verify', 'dead', 'email', 'failed')
    );

CREATE INDEX IF NOT EXISTS jobs_auto_apply_status_idx ON public.jobs (auto_apply_status);
