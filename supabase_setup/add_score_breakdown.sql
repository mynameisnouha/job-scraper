-- Run this once in the Supabase SQL editor.
-- Stores the full LLM scoring breakdown (skills match, gaps, recommendation, reasoning)
-- alongside the numeric resume_score, so the dashboard can show WHY a job got its score.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS score_breakdown jsonb;
