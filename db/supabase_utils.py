from supabase import create_client, Client
import config # Import configuration
from sources import dedup  # normalization shared with the scrapers; see get_existing_jobs_from_supabase
from typing import Optional, Any, Dict, List
from models import Resume
import json
import datetime # Import datetime module
import logging # Import logging

# --- Initialize Supabase Client ---
# Ensure URL and Key are provided
if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
    raise ValueError("Supabase URL and Key must be set in environment variables or config.")

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

# --- Supabase Functions ---
def get_existing_jobs_from_supabase(batch_size: int = 1000) -> tuple[set, set]:
    """
    Fetches all existing job IDs and company-title pairs from the Supabase 'jobs' table.
    Returns:
        - A set of job_ids
        - A set of 'company|job_title' keys (both lowercased for consistency)
    """
    existing_ids = set()
    existing_company_title_keys = set()
    offset = 0

    try:
        while True:
            response = (
                supabase.table(config.SUPABASE_TABLE_NAME)
                .select("job_id, company, job_title")
                .range(offset, offset + batch_size - 1)
                .execute()
            )

            data = response.data

            if not data:
                break  # No more data to fetch

            for item in data:
                job_id = item.get("job_id")
                company = item.get("company")
                job_title = item.get("job_title")

                if job_id:
                    existing_ids.add(str(job_id))

                if company and job_title:
                    # dedup's normalization, not a bare lower(): the same posting
                    # arrives as "YPOG GmbH"/"AI/ML Engineer (m/w/d)" from one source
                    # and "YPOG"/"AI/ML Engineer" from another, and a raw lowercase
                    # key treats those as two different jobs. See dedup.py.
                    key = (dedup.normalize_company(company), dedup.normalize_title(job_title))
                    if all(key):
                        existing_company_title_keys.add(key)

            offset += batch_size

        logging.info(f"Fetched {len(existing_ids)} job IDs and {len(existing_company_title_keys)} company-title pairs.")

    except Exception as e:
        logging.error(f"Error fetching existing jobs from Supabase: {e}")

    # Postings deleted by hand count as "already seen" for every scraper. Without
    # this a delete undoes itself: the row is gone, so the next scrape treats the
    # posting as new, re-inserts it, and pays to screen and score something that
    # was explicitly thrown away.
    deleted_ids, deleted_keys = get_deleted_job_keys()
    if deleted_ids or deleted_keys:
        logging.info(f"Plus {len(deleted_ids)} deleted posting(s) to keep out.")
    return existing_ids | deleted_ids, existing_company_title_keys | deleted_keys

def save_jobs_to_supabase(jobs_data: list):
    """
    Saves or updates a list of job data dictionaries to the Supabase table using upsert.
    This avoids duplicate key errors by updating existing records based on job_id.
    """
    if not jobs_data:
        logging.warning("No job data provided to save/update.")
        return

    # Columns that exist in the Supabase 'jobs' table schema
    known_columns = {
        "job_id", "company", "job_title", "level", "location", "description",
        "provider", "posted_at", "job_url", "resume_score", "resume_score_stage",
        "is_active", "status", "job_state", "scraped_at", "last_checked",
        "customized_resume_id", "resume_link", "score_breakdown", "alt_sources",
        "program_type",
    }

    processed_jobs_data = []
    for job in jobs_data:
        if 'job_id' in job and job['job_id'] is not None:
             job['job_id'] = str(job['job_id'])
             # Strip any keys that aren't in the schema to avoid PGRST204 errors
             clean_job = {k: v for k, v in job.items() if k in known_columns}
             processed_jobs_data.append(clean_job)
        else:
            logging.warning(f"Job data missing job_id. Skipping: {job}")


    if not processed_jobs_data:
        logging.warning("No valid job data remaining after processing.")
        return

    logging.info(f"Attempting to upsert {len(processed_jobs_data)} jobs to Supabase...")

    try:
        # Use table name from config
        # Use upsert instead of insert. It will insert new rows
        # or update existing rows if a job_id conflict occurs based on the primary key.
        # Ensure 'job_id' is the primary key or has a unique constraint in your Supabase table.
        # By default, supabase-py's upsert updates the row on conflict.
        try:
            data, count = supabase.table(config.SUPABASE_TABLE_NAME).upsert(processed_jobs_data).execute()
        except Exception as inner_e:
            # Column not added yet (supabase_setup/add_program_type.sql). Losing the
            # tag is recoverable — the queue re-derives it from the title — losing
            # the whole run's postings is not.
            if "program_type" not in str(inner_e):
                raise
            logging.warning("program_type column missing in Supabase — saving jobs without it.")
            for job in processed_jobs_data:
                job.pop("program_type", None)
            data, count = supabase.table(config.SUPABASE_TABLE_NAME).upsert(processed_jobs_data).execute()

        # Check the actual response structure from your Supabase client version for upsert
        # It might differ slightly from insert's response structure
        if data and isinstance(data, tuple) and len(data) > 1:
             logging.info(f"Successfully upserted/updated {len(processed_jobs_data)} jobs. Supabase response count: {count}")
        else:
             logging.info(f"Attempted to upsert {len(processed_jobs_data)} jobs. Supabase response: {data}")

    except Exception as e:
        logging.error(f"Error upserting data to Supabase: {e}")


def get_jobs_to_score(limit: int) -> list:
    """
    Fetches jobs from the Supabase 'jobs' table that need scoring.
    Filters by is_active = true and resume_score = null.
    Selects only necessary fields (job_id, job_title, description).
    Orders by scraped_at ascending to process older jobs first.
    """
    if limit <= 0:
        logging.warning("Limit for jobs to score must be positive.")
        return []

    try:
        logging.info(f"Fetching up to {limit} jobs needing scoring...")

        # Select fields needed for scoring
        def _query(columns: str):
            return supabase.table(config.SUPABASE_TABLE_NAME)\
                           .select(columns)\
                           .eq("is_active", True)\
                           .is_("resume_score", None)\
                           .order("scraped_at", desc=False)\
                           .limit(limit)\
                           .execute()

        # scraped_at is not used for scoring. It is here because the dashboard
        # filters these rows to "found today", and without the column that test
        # failed for every row — so the Unscored table was structurally always
        # empty and the stat above it always read 0.
        base_cols = "job_id, job_title, company, description, level, scraped_at"
        try:
            response = _query(base_cols + ", program_type")
        except Exception as inner_e:
            if "program_type" not in str(inner_e):
                raise
            # Column not added yet (supabase_setup/add_program_type.sql). Programmes
            # are then scored as standard roles rather than not at all.
            logging.warning("program_type column missing in Supabase — scoring without it.")
            response = _query(base_cols)

        if response.data:
            logging.info(f"Successfully fetched {len(response.data)} jobs to score.")
            return response.data
        else:
            logging.info("No jobs found needing scoring at this time.")
            return []

    except Exception as e:
        logging.error(f"Error fetching jobs to score from Supabase: {e}")
        return []


def get_jobs_needing_backfill(limit: int) -> list:
    """
    Fetches jobs that need job_url backfill OR have old-style scores (rescore).
    Returns jobs where job_url IS NULL OR (resume_score IS NOT NULL AND resume_score_stage IS NULL).
    Orders by scraped_at ascending.
    """
    if limit <= 0:
        logging.warning("Limit for backfill must be positive.")
        return []

    try:
        logging.info(f"Fetching up to {limit} jobs needing backfill or rescore...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .select("job_id, job_title, company, description, level, provider, job_url, resume_score, resume_score_stage")\
                           .eq("is_active", True)\
                           .or_("job_url.is.null,resume_score_stage.is.null")\
                           .order("scraped_at", desc=False)\
                           .limit(limit)\
                           .execute()

        if response.data:
            logging.info(f"Successfully fetched {len(response.data)} jobs needing backfill.")
            return response.data
        else:
            logging.info("No jobs found needing backfill at this time.")
            return []

    except Exception as e:
        logging.error(f"Error fetching jobs for backfill from Supabase: {e}")
        return []


def get_top_scored_jobs_to_apply(limit: int) -> list:
    """
    Fetches the top-scored jobs from Supabase that are ready for application.
    Filters by is_active = true, resume_score is not null, and status is null.
    Orders by resume_score descending.
    Selects fields needed for the application process.
    """
    if limit <= 0:
        logging.warning("Limit for jobs to apply must be positive.")
        return []

    try:
        logging.info(f"Fetching up to {limit} top-scored jobs to apply for...")

        def _query(columns: str, exclude_dismissed: bool = True):
            query = supabase.table(config.SUPABASE_TABLE_NAME)\
                            .select(columns)\
                            .eq("is_active", True)\
                            .eq("status", "new")\
                            .not_.is_("resume_score", None)
            # Dismissed jobs stay in the table but never come back to the queue.
            if exclude_dismissed:
                query = query.is_("dismissed_at", None)
            return query.order("resume_score", desc=True)\
                        .limit(limit)\
                        .execute()

        base_cols = "job_id, job_title, company, resume_score, job_url, provider, posted_at, scraped_at"
        try:
            response = _query(base_cols + ", score_breakdown, why_me_pitch, dismissed_at, program_type")
        except Exception as inner_e:
            err = str(inner_e)
            if "program_type" in err:
                # Column not added yet (supabase_setup/add_program_type.sql). The queue
                # falls back to classifying the title itself; see apply_queue.program_type.
                logging.warning("program_type column missing in Supabase — the queue will "
                                "tag programmes from the title instead.")
                response = _query(base_cols + ", score_breakdown, why_me_pitch, dismissed_at")
            elif "dismissed_at" in err:
                logging.warning("Dismissal columns missing (run supabase_setup/add_dismissal.sql). "
                                "Fetching without them — dismissed jobs will keep reappearing.")
                try:
                    response = _query(base_cols + ", score_breakdown, why_me_pitch",
                                      exclude_dismissed=False)
                except Exception as retry_e:
                    if "score_breakdown" in str(retry_e) or "why_me_pitch" in str(retry_e):
                        response = _query(base_cols, exclude_dismissed=False)
                    else:
                        raise
            elif "score_breakdown" in err or "why_me_pitch" in err:
                logging.warning("Optional column(s) missing (run supabase_setup/add_score_breakdown.sql "
                                "and add_why_me_pitch.sql). Fetching without them.")
                response = _query(base_cols + ", dismissed_at")
            else:
                raise

        if response.data:
            logging.info(f"Successfully fetched {len(response.data)} top-scored jobs to apply for.")
            return response.data
        else:
            logging.info("No top-scored jobs found ready for application at this time.")
            return []

    except Exception as e:
        logging.error(f"Error fetching top-scored jobs to apply for from Supabase: {e}")
        return []

def get_job_with_description(job_id: str) -> Optional[Dict[str, Any]]:
    """One job including its full description.

    The apply-queue query deliberately leaves `description` out — it is the
    largest column in the table and rendering a list of them would pull megabytes
    for nothing. Anything that has to reason about what a posting actually says
    (CV tailoring, the gap interview) needs it, so it is fetched one row at a time
    here instead of widening the queue query for every caller.
    """
    try:
        response = supabase.table(config.SUPABASE_TABLE_NAME)                           .select("job_id, job_title, company, location, level, "
                                   "description, job_url, resume_score, score_breakdown, "
                                   "provider, why_me_pitch")                           .eq("job_id", job_id)                           .limit(1)                           .execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"Error fetching job {job_id} with description: {e}")
        return None


def get_job_urls(job_ids: List[str]) -> Dict[str, str]:
    """job_id -> job_url for a handful of jobs, in one query.

    The rescore RPC does not return job_url, and the email alert wants a link
    per job. One batched select beats a per-job fetch of the full row, whose
    description column is the largest in the table.
    """
    if not job_ids:
        return {}
    try:
        response = supabase.table(config.SUPABASE_TABLE_NAME)                            .select("job_id, job_url")                            .in_("job_id", list(job_ids))                            .execute()
        return {row["job_id"]: row.get("job_url") or "" for row in (response.data or [])}
    except Exception as e:
        logging.error(f"Error fetching job URLs: {e}")
        return {}


def get_applied_jobs(limit: int) -> list:
    """
    Fetches jobs already marked as applied (status = 'applied'), most recent first.
    Selects fields needed for display on the dashboard.
    """
    if limit <= 0:
        logging.warning("Limit for applied jobs must be positive.")
        return []

    try:
        logging.info(f"Fetching up to {limit} applied jobs...")

        def _query(columns: str):
            return supabase.table(config.SUPABASE_TABLE_NAME)\
                           .select(columns)\
                           .eq("status", "applied")\
                           .order("application_date", desc=True)\
                           .limit(limit)\
                           .execute()

        base_cols = "job_id, job_title, company, resume_score, job_url, provider, posted_at, scraped_at, application_date"
        response = _query(base_cols)

        if response.data:
            logging.info(f"Successfully fetched {len(response.data)} applied jobs.")
            return response.data
        else:
            logging.info("No applied jobs found.")
            return []

    except Exception as e:
        logging.error(f"Error fetching applied jobs from Supabase: {e}")
        return []


def get_top_scored_jobs_for_resume_generation(limit: int) -> list:
    """
    Fetches the top-scored jobs from Supabase using the RPC 'get_top_scored_jobs_custom_sort'.
    p_page_number is set to 1 and p_page_size is set to the limit.
    Selects fields needed for the application process.
    """
    if limit <= 0:
        logging.warning("Limit for jobs to apply must be positive.")
        return []

    try:
        logging.info(f"Fetching up to {limit} top-scored jobs to apply for using RPC 'get_top_scored_jobs_custom_sort'...")
        response = supabase.rpc(
                "get_jobs_for_resume_generation_custom_sort",
                {"p_page_number": 1, "p_page_size": limit}
            ).execute()

        if response.data:
            logging.info(f"Successfully fetched {len(response.data)} top-scored jobs to apply for via RPC.")
            return response.data
        else:
            # Check for RPC specific errors if any, or just log general empty data
            if hasattr(response, 'error') and response.error:
                logging.error(f"Error calling RPC 'get_top_scored_jobs_custom_sort': {response.error.message}")
            else:
                logging.info("No top-scored jobs found ready for application at this time via RPC.")
            return []

    except Exception as e:
        logging.error(f"Error fetching top-scored jobs to apply for from Supabase RPC: {e}")
        return []

def get_jobs_to_rescore(limit: int) -> list:
    """
    Fetches jobs from Supabase that are ready for re-scoring with a custom resume.
    Filters by is_active = true, resume_link is not null, and resume_score_stage = 'initial'.
    Orders by resume_score descending.
    Selects fields needed for the re-scoring process.
    """
    if limit <= 0:
        logging.warning("Limit for jobs to rescore must be positive.")
        return []

    try:
        logging.info(f"Fetching up to {limit} jobs for re-scoring via RPC...")
        # Note: We updated the RPC to also return customized_resume_id
        response = supabase.rpc(
            "get_jobs_for_rescore", 
            {"p_limit_val": limit}   
        ).execute()

        if hasattr(response, 'data') and response.data is not None:
            if response.data: # Check if list is not empty
                logging.info(f"Successfully fetched {len(response.data)} jobs for re-scoring via RPC.")
                return response.data
            else:
                logging.info("No jobs found meeting re-scoring criteria via RPC at this time (empty list returned).")
                return []
        elif hasattr(response, 'error') and response.error: # Handle explicit error attribute
             logging.error(f"Error calling RPC get_jobs_for_rescore: {response.error}")
             return []
        else: # Fallback for unexpected response structure
            logging.warning(f"Unexpected response structure from RPC call: {response}")
            return []


    except Exception as e:
        logging.error(f"Exception calling RPC get_jobs_for_rescore: {e}", exc_info=True)
        return []

def update_job_score(job_id: str, score: int, resume_score_stage: str = "initial", score_breakdown: Optional[dict] = None) -> bool:
    """
    Updates the 'resume_score', 'resume_score_stage', and optionally 'score_breakdown'
    for a specific job_id in the Supabase 'jobs' table.
    Returns True on success, False on failure.
    """
    if not job_id or score is None:
        logging.error(f"Invalid input for updating job score: job_id={job_id}, score={score}")
        return False

    if resume_score_stage not in ["initial", "custom"]:
        logging.error(f"Invalid resume_score_stage: {resume_score_stage}. Must be 'initial' or 'custom'.")
        return False

    try:
        logging.info(f"Updating score for job_id {job_id} to {score} and stage to {resume_score_stage}...")
        update_payload = {
            "resume_score": score,
            "resume_score_stage": resume_score_stage
        }
        if score_breakdown:
            update_payload["score_breakdown"] = score_breakdown
        try:
            response = supabase.table(config.SUPABASE_TABLE_NAME)\
                               .update(update_payload)\
                               .eq("job_id", job_id)\
                               .execute()
        except Exception as inner_e:
            # Column may not exist yet (run supabase_setup/add_score_breakdown.sql).
            # Retry without the breakdown so the score itself is never lost.
            if score_breakdown and "score_breakdown" in str(inner_e):
                logging.warning("score_breakdown column missing in Supabase — saving score without breakdown. "
                                "Run supabase_setup/add_score_breakdown.sql to enable it.")
                update_payload.pop("score_breakdown", None)
                response = supabase.table(config.SUPABASE_TABLE_NAME)\
                                   .update(update_payload)\
                                   .eq("job_id", job_id)\
                                   .execute()
            else:
                raise

        # Check if the update was successful (response structure might vary)
        # A common pattern is checking if data is returned or count is non-zero
        if hasattr(response, 'data') and response.data:
             logging.info(f"Successfully updated score for job_id {job_id}.")
             return True
        elif hasattr(response, 'count') and response.count is not None and response.count > 0:
             logging.info(f"Successfully updated score for job_id {job_id} (count={response.count}).")
             return True
        elif not hasattr(response, 'data') and not hasattr(response, 'count'):
             # Handle cases where the response might not have data/count but didn't error
             logging.warning(f"Update score for job_id {job_id} executed, but response structure unclear: {response}")
             return True # Assume success if no exception occurred
        else:
             logging.warning(f"Update score for job_id {job_id} might have failed or job not found. Response: {response}")
             return False


    except Exception as e:
        logging.error(f"Error updating score for job_id {job_id} in Supabase: {e}")
        return False


def update_job_pitch(job_id: str, pitch: str) -> bool:
    """Saves the generated 'why me' pitch for a job. Non-fatal if the column doesn't exist yet."""
    if not job_id or not pitch:
        return False
    try:
        supabase.table(config.SUPABASE_TABLE_NAME)\
                .update({"why_me_pitch": pitch})\
                .eq("job_id", job_id)\
                .execute()
        logging.info(f"Saved why-me pitch for job_id {job_id}.")
        return True
    except Exception as e:
        if "why_me_pitch" in str(e):
            logging.warning("why_me_pitch column missing — run supabase_setup/add_why_me_pitch.sql to enable pitches.")
        else:
            logging.error(f"Error saving pitch for job_id {job_id}: {e}")
        return False


def update_job_url(job_id: str, job_url: str) -> bool:
    """Updates the job_url for a specific job in the Supabase 'jobs' table."""
    if not job_id or not job_url:
        logging.error(f"Invalid input for updating job_url: job_id={job_id}, job_url={job_url}")
        return False

    try:
        logging.info(f"Updating job_url for job_id {job_id}...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .update({"job_url": job_url})\
                           .eq("job_id", job_id)\
                           .execute()

        if hasattr(response, 'data') and response.data:
            logging.info(f"Successfully updated job_url for job_id {job_id}.")
            return True
        elif hasattr(response, 'count') and response.count is not None and response.count > 0:
            logging.info(f"Successfully updated job_url for job_id {job_id} (count={response.count}).")
            return True
        elif not hasattr(response, 'data') and not hasattr(response, 'count'):
            logging.warning(f"Update job_url for job_id {job_id} executed, but response structure unclear: {response}")
            return True
        else:
            logging.warning(f"Update job_url for job_id {job_id} might have failed. Response: {response}")
            return False

    except Exception as e:
        logging.error(f"Error updating job_url for job_id {job_id} in Supabase: {e}")
        return False


def mark_job_applied(job_id: str) -> bool:
    """Marks a job as applied: sets status='applied' and application_date to now."""
    if not job_id:
        logging.error("No job_id provided to mark as applied.")
        return False

    try:
        logging.info(f"Marking job {job_id} as applied...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .update({
                               "status": "applied",
                               "application_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           })\
                           .eq("job_id", job_id)\
                           .execute()

        if response.data:
            logging.info(f"Successfully marked job {job_id} as applied.")
            return True
        else:
            logging.warning(f"Mark-applied for job_id {job_id} executed, but no rows seemed to be affected.")
            return False

    except Exception as e:
        logging.error(f"Error marking job {job_id} as applied in Supabase: {e}")
        return False


VALID_APPLICATION_STAGES = {
    "applied", "interview_1", "interview_2", "interview_3", "offer", "rejected", "ghosted",
    # Posting was pulled or turned out to be spam/fake. Not a real outcome — excluded
    # from calibration entirely rather than counted as a rejection.
    "spam_or_removed",
}


def update_application_stage(job_id: str, stage: str, rejection_reason: Optional[str] = None,
                              notes: Optional[str] = None) -> bool:
    """
    Updates the outcome-tracking fields for a job: application_stage, stage_updated_at,
    and optionally rejection_reason / outcome_notes. Non-fatal if the columns don't exist yet
    (run supabase_setup/add_application_outcomes.sql to enable this).
    """
    if not job_id or stage not in VALID_APPLICATION_STAGES:
        logging.error(f"Invalid input for updating application stage: job_id={job_id}, stage={stage}")
        return False

    update_payload = {
        "application_stage": stage,
        "stage_updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if rejection_reason is not None:
        update_payload["rejection_reason"] = rejection_reason
    if notes is not None:
        update_payload["outcome_notes"] = notes

    try:
        logging.info(f"Updating application_stage for job_id {job_id} to '{stage}'...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .update(update_payload)\
                           .eq("job_id", job_id)\
                           .execute()

        if response.data:
            logging.info(f"Successfully updated application_stage for job_id {job_id}.")
            return True
        else:
            logging.warning(f"Update application_stage for job_id {job_id} might have failed or job not found.")
            return False

    except Exception as e:
        if "application_stage" in str(e) or "rejection_reason" in str(e) or "outcome_notes" in str(e):
            logging.warning("Outcome-tracking columns missing — run supabase_setup/add_application_outcomes.sql.")
        else:
            logging.error(f"Error updating application_stage for job_id {job_id} in Supabase: {e}")
        return False


def get_scored_jobs_for_health_check(limit: int = 500) -> list:
    """
    Fetches scored jobs with everything needed to judge — and if necessary redo —
    their scoring. Used by check_scoring_health.py and rescore_degraded.py.
    """
    try:
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .select("job_id, job_title, company, level, description, "
                                   "scraped_at, resume_score, resume_score_stage, score_breakdown")\
                           .not_.is_("score_breakdown", None)\
                           .eq("is_active", True)\
                           .order("scraped_at", desc=True)\
                           .limit(limit)\
                           .execute()
        return response.data or []
    except Exception as e:
        logging.error(f"Error fetching scored jobs for health check: {e}")
        return []


def get_score_breakdowns(limit: int = 500) -> list:
    """
    Job ids and their score breakdowns, newest first. Nothing else.

    Deliberately leaner than get_scored_jobs_for_health_check, which selects the
    full description too: the tailoring harvest walks hundreds of rows to count
    how often each gap recurs, and pulling a few hundred job descriptions to read
    one JSON field off each would be the expensive part of a cheap operation.
    """
    try:
        response = supabase.table(config.SUPABASE_TABLE_NAME)                           .select("job_id, job_title, company, score_breakdown")                           .not_.is_("score_breakdown", None)                           .order("scraped_at", desc=True)                           .limit(limit)                           .execute()
        return response.data or []
    except Exception as e:
        logging.error(f"Error fetching score breakdowns: {e}")
        return []


def get_breakdowns_above_score(min_score: int, page_size: int = 500) -> list:
    """Every scored job at or above `min_score`, with its breakdown. Paged.

    Used by the market panel on the archetypes page, which needs the whole
    population rather than the top N: the question there is what share of the
    reachable market demands C1 German, and a limit would silently answer it
    about the highest-scoring slice instead.
    """
    rows: list = []
    offset = 0
    try:
        while True:
            response = (
                supabase.table(config.SUPABASE_TABLE_NAME)
                .select("job_id, resume_score, score_breakdown")
                .gte("resume_score", min_score)
                .not_.is_("score_breakdown", None)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
    except Exception as e:
        logging.error(f"Error fetching breakdowns above score {min_score}: {e}")
    return rows


def mark_job_closed(job_id: str) -> bool:
    """
    Marks a posting you never applied to as no longer accepting candidates:
    job_state='closed' and is_active=False, so it drops out of the apply queue
    and gets swept up by job_manager's inactive-job cleanup.

    Deliberately does NOT touch `status`, so it can never be confused with an
    application you actually sent.
    """
    if not job_id:
        logging.error("No job_id provided to mark as closed.")
        return False

    try:
        logging.info(f"Marking job {job_id} as no longer accepting candidates...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .update({"job_state": "closed", "is_active": False})\
                           .eq("job_id", job_id)\
                           .execute()
        if response.data:
            logging.info(f"Successfully marked job {job_id} as closed.")
            return True
        logging.warning(f"Mark-closed for job_id {job_id} affected no rows.")
        return False
    except Exception as e:
        logging.error(f"Error marking job {job_id} as closed: {e}")
        return False


VALID_SKIP_REASONS = {
    "not_interested", "wrong_seniority", "location", "german_level",
    "visa_sponsorship", "salary_too_low", "already_applied_elsewhere",
    "duplicate_posting", "other",
}


def dismiss_job(job_id: str, reason: Optional[str] = None) -> bool:
    """
    Takes a job out of the apply queue without applying to it.

    A soft dismissal: the row stays, `status` stays `new`, and `is_active` is
    untouched. Only `dismissed_at` changes, which is what the queue filters on.
    Keeping the row is the point — a high-scoring job dismissed on sight is the
    scorer and the human disagreeing, and that is a measurable signal.

    Needs supabase_setup/add_dismissal.sql; fails soft (and loudly) without it.
    """
    if not job_id:
        logging.error("No job_id provided to dismiss.")
        return False
    if reason is not None and reason not in VALID_SKIP_REASONS:
        logging.error(f"Invalid dismissal reason '{reason}' for job {job_id}.")
        return False

    payload = {"dismissed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if reason is not None:
        payload["dismissal_reason"] = reason

    try:
        logging.info(f"Dismissing job {job_id} (reason={reason or 'none given'})...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)                           .update(payload)                           .eq("job_id", job_id)                           .execute()
        if response.data:
            logging.info(f"Successfully dismissed job {job_id}.")
            return True
        logging.warning(f"Dismiss for job_id {job_id} affected no rows.")
        return False
    except Exception as e:
        if "dismissed_at" in str(e) or "dismissal_reason" in str(e):
            logging.warning("Dismissal columns missing — run supabase_setup/add_dismissal.sql.")
        else:
            logging.error(f"Error dismissing job {job_id}: {e}")
        return False


# Why a posting should never have been in the corpus. Deliberately NOT the skip
# vocabulary: a skip is a decision about a real job ("wrong seniority", "German
# level"), and mixing the two would put junk rows into every statistic built on
# why she turns jobs down.
VALID_DELETE_REASONS = {
    "not_relevant", "agency_repost", "duplicate_posting", "expired",
    "mis_scraped", "other",
    # Written by maintenance.purge_low_scores, never offered as a button: the UI
    # list is what YOU can say about a posting, and "it scored badly" is the
    # pipeline's judgement rather than yours.
    "below_score_floor",
}


def get_deleted_job_keys() -> tuple[set, set]:
    """Ids and dedup keys of postings deleted by hand.

    Returns empty sets when the table is absent, so a scrape on a database that
    has not run add_deleted_jobs.sql behaves exactly as it did before rather
    than failing. The cost of that is a deleted posting coming back, which is
    visible and fixable; the cost of the alternative is no scrape at all.
    """
    ids: set = set()
    keys: set = set()
    try:
        response = supabase.table("deleted_jobs").select("job_id, dedup_key").execute()
        for row in response.data or []:
            if row.get("job_id"):
                ids.add(str(row["job_id"]))
            raw = row.get("dedup_key") or ""
            if "|" in raw:
                company, _, title = raw.partition("|")
                if company and title:
                    keys.add((company, title))
    except Exception as e:
        logging.warning(f"Could not read deleted_jobs ({e}); deleted postings may "
                        "reappear. Run supabase_setup/add_deleted_jobs.sql.")
    return ids, keys


def delete_job(job: Dict[str, Any], reason: Optional[str] = None,
               note: Optional[str] = None) -> bool:
    """Remove a posting from the corpus for good, and record that it was removed.

    The tombstone is written BEFORE the row is deleted, and the delete is skipped
    if the tombstone fails. The other order loses the posting and its dedup key
    together, which is the one outcome worth engineering against: the row is gone
    from the queue, nothing remembers the decision, and the next scrape re-adds
    the posting and pays to score it again.

    This is not a dismissal. Use dismiss_job for a job you considered and turned
    down - that row is evidence. Use this for postings that should not be in the
    corpus at all: agency reposts, duplicates the dedup missed, a mis-scraped
    listing. The score, breakdown and description go with it and do not come back.
    """
    job_id = str(job.get("job_id") or "")
    if not job_id:
        logging.error("No job_id provided to delete.")
        return False
    if reason is not None and reason not in VALID_DELETE_REASONS:
        logging.error(f"Invalid delete reason '{reason}' for job {job_id}.")
        return False

    company = job.get("company") or ""
    title = job.get("job_title") or ""
    key = (dedup.normalize_company(company), dedup.normalize_title(title))
    tombstone = {
        "job_id": job_id,
        "company": company,
        "job_title": title,
        "dedup_key": f"{key[0]}|{key[1]}" if all(key) else "",
        "reason": reason,
        "note": (note or "").strip() or None,
    }

    # Kept because the bulk purge deletes ~1000 rows at a time, and screened-out
    # rows carry the only record of what the cheap screen rejected - the sample
    # score_jobs.py deliberately stores so the corpus-wide language picture is
    # not just the postings that passed. Three fields keep the distribution
    # answerable after the descriptions are gone.
    breakdown = job.get("score_breakdown")
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except (ValueError, TypeError):
            breakdown = {}
    breakdown = breakdown if isinstance(breakdown, dict) else {}
    stats = {
        "resume_score": job.get("resume_score"),
        "german_required": breakdown.get("german_required"),
        "jd_language": breakdown.get("jd_language"),
    }

    try:
        supabase.table("deleted_jobs").upsert({**tombstone, **stats}).execute()
    except Exception as e:
        # Those three columns arrived after the table did. Falling back to the
        # core payload keeps deleting working on a database that has not run the
        # newer migration - losing the statistics, never the tombstone, because
        # a delete without a tombstone is the one that undoes itself.
        if any(col in str(e) for col in stats):
            logging.warning("deleted_jobs is missing the stats columns — recording the "
                            "deletion without them. Re-run supabase_setup/add_deleted_jobs.sql.")
            try:
                supabase.table("deleted_jobs").upsert(tombstone).execute()
            except Exception as inner:
                logging.error(f"Could not record the deletion of {job_id} ({inner}). "
                              "The posting was NOT deleted.")
                return False
        else:
            logging.error(f"Could not record the deletion of {job_id} ({e}). The posting "
                          "was NOT deleted - run supabase_setup/add_deleted_jobs.sql first.")
            return False

    try:
        response = (
            supabase.table(config.SUPABASE_TABLE_NAME)
            .delete()
            .eq("job_id", job_id)
            .execute()
        )
        if response.data:
            logging.info(f"Deleted job {job_id} (reason={reason or 'none given'}).")
            return True
        logging.warning(f"Delete for job_id {job_id} affected no rows.")
        return False
    except Exception as e:
        logging.error(f"Error deleting job {job_id}: {e}")
        return False


def undismiss_job(job_id: str) -> bool:
    """Puts a dismissed job back in the queue — the undo for a mis-keyed skip."""
    if not job_id:
        logging.error("No job_id provided to undismiss.")
        return False

    try:
        logging.info(f"Restoring dismissed job {job_id} to the queue...")
        response = supabase.table(config.SUPABASE_TABLE_NAME)                           .update({"dismissed_at": None, "dismissal_reason": None})                           .eq("job_id", job_id)                           .execute()
        if response.data:
            return True
        logging.warning(f"Undismiss for job_id {job_id} affected no rows.")
        return False
    except Exception as e:
        logging.error(f"Error undismissing job {job_id}: {e}")
        return False


def get_skip_reason_counts() -> Dict[str, int]:
    """
    How often each skip reason was recorded, most common first.

    The other half of "what is stopping you": rejections are what employers
    said no to, skips are what you said no to. Empty when the dismissal
    migration has not been run — the columns simply are not there yet.
    """
    try:
        response = supabase.table(config.SUPABASE_TABLE_NAME)                           .select("dismissal_reason")                           .not_.is_("dismissed_at", None)                           .execute()
    except Exception as e:
        logging.warning(f"Could not read skip reasons (run supabase_setup/add_dismissal.sql?): {e}")
        return {}
    counts: Dict[str, int] = {}
    for row in response.data or []:
        reason = (row.get("dismissal_reason") or "").strip() or "unspecified"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def get_scrape_pulse(hours: int = 24, bar: int = 70) -> Optional[Dict[str, Any]]:
    """
    When the scraper last landed, and what the last day brought.

    Read once per page load for the sidebar, so it asks only for the two
    columns it needs. None when the query fails — the sidebar then says nothing
    rather than something wrong.
    """
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=hours)).isoformat()
    try:
        latest = supabase.table(config.SUPABASE_TABLE_NAME)                         .select("scraped_at, provider")                         .not_.is_("scraped_at", None)                         .order("scraped_at", desc=True)                         .limit(1)                         .execute()
        recent = supabase.table(config.SUPABASE_TABLE_NAME)                         .select("resume_score, provider")                         .gte("scraped_at", since)                         .execute()
    except Exception as e:
        logging.warning(f"Could not read the scrape pulse: {e}")
        return None
    rows = recent.data or []
    return {
        "last_scraped_at": (latest.data or [{}])[0].get("scraped_at"),
        "new": len(rows),
        "cleared": sum(1 for r in rows if (r.get("resume_score") or 0) >= bar),
        "sources": sorted({r.get("provider") for r in rows if r.get("provider")}),
    }


def get_applied_jobs_with_outcomes(limit: int = 999) -> list:
    """
    Fetches all applied jobs with their outcome-tracking fields and score_breakdown,
    for the outcome-logging UI and calibration analysis. Falls back to fewer columns
    if the outcome-tracking migration hasn't been run yet.
    """
    def _query(columns: str):
        return supabase.table(config.SUPABASE_TABLE_NAME)\
                       .select(columns)\
                       .eq("status", "applied")\
                       .order("application_date", desc=True)\
                       .limit(limit)\
                       .execute()

    base_cols = ("job_id, job_title, company, resume_score, score_breakdown, job_url, "
                 "why_me_pitch, application_date, application_stage, stage_updated_at, "
                 "rejection_reason, outcome_notes")
    try:
        response = _query(base_cols)
        return response.data or []
    except Exception as e:
        err = str(e)
        # why_me_pitch is listed alongside the outcome columns rather than given
        # its own branch: both come from optional migrations, and the recovery is
        # identical — drop back to the columns that certainly exist. Without it
        # here, a project missing only that migration would fall through to the
        # generic handler and get an empty page instead of a degraded one.
        if ("application_stage" in err or "rejection_reason" in err
                or "outcome_notes" in err or "why_me_pitch" in err):
            logging.warning("Outcome-tracking columns missing — run supabase_setup/add_application_outcomes.sql. "
                             "Fetching without them.")
            fallback_cols = "job_id, job_title, company, resume_score, score_breakdown, job_url, application_date"
            try:
                response = _query(fallback_cols)
                return response.data or []
            except Exception as e2:
                logging.error(f"Error fetching applied jobs (fallback) from Supabase: {e2}")
                return []
        logging.error(f"Error fetching applied jobs with outcomes from Supabase: {e}")
        return []


def verify_job_score_update(job_id: str, expected_score: int, expected_stage: str) -> bool:
    """Fetches a job and verifies resume_score and resume_score_stage were persisted correctly."""
    if not job_id:
        return False
    try:
        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .select("job_id, resume_score, resume_score_stage")\
                           .eq("job_id", job_id)\
                           .limit(1)\
                           .execute()
        if response.data:
            actual = response.data[0]
            got_score = actual.get("resume_score")
            got_stage = actual.get("resume_score_stage")
            if got_score == expected_score and got_stage == expected_stage:
                logging.info(f"✓ Verified: job_id={job_id} resume_score={got_score} resume_score_stage={got_stage}")
                return True
            else:
                logging.warning(f"✗ Mismatch for job_id={job_id}: expected score={expected_score}, got={got_score}; expected stage={expected_stage}, got={got_stage}")
                return False
        else:
            logging.warning(f"Job {job_id} not found during verification read-back.")
            return False
    except Exception as e:
        logging.error(f"Error verifying score for job_id {job_id}: {e}")
        return False


def upload_customized_resume_to_storage(file_content: bytes, destination_path: str) -> Optional[str]:
    """
    Uploads the generated resume PDF (as bytes) to Supabase Storage.

    Args:
        file_content: The resume content in bytes.
        destination_path: The desired path and filename within the bucket
                          (e.g., "personalized_resumes/resume_job_12345.pdf").
                          Ensure this path is unique per job/resume.

    Returns:
        The destination path of the uploaded file, or None if upload fails.
    """
    if not file_content:
        logging.error("Cannot upload empty file content.")
        return None
    if not config.SUPABASE_STORAGE_BUCKET:
        logging.error("Supabase storage bucket name not configured.")
        return None

    try:
        logging.info(f"Uploading resume to Supabase Storage at path: {destination_path}")

        # Use upsert=True if you want to overwrite if a file with the same name exists,
        # otherwise False (or omit) to potentially get an error if it exists.
        # Ensure your destination_path includes job_id or similar for uniqueness.
        supabase.storage.from_(config.SUPABASE_STORAGE_BUCKET)\
            .upload(
                path=destination_path,
                file=file_content,
                file_options={"content-type": "application/pdf", "upsert": "true"} # Set upsert based on desired behavior
            )

        logging.info(f"Successfully uploaded resume to path: {destination_path}")
        return destination_path

    except Exception as e:
        # Supabase client might raise specific exceptions, catch broadly for now
        logging.error(f"Error uploading file to Supabase Storage: {e}")
        # Attempt to remove partially uploaded file if possible/needed (more complex error handling)
        # try:
        #     supabase.storage.from_(config.SUPABASE_STORAGE_BUCKET).remove([destination_path])
        # except:
        #     logging.warning(f"Could not clean up potentially failed upload at {destination_path}")
        return None

def upload_text_to_storage(text_content: str, destination_path: str) -> Optional[str]:
    """
    Uploads a plain-text file (e.g. a compact resume export) to Supabase Storage.
    Mirrors upload_customized_resume_to_storage but for text/plain content instead of PDF bytes —
    used while the ReportLab PDF layout isn't producing the intended design, so a token-minimal
    text export can be pasted into another tool to build the CV manually.
    """
    if not text_content or not text_content.strip():
        logging.error("Cannot upload empty text content.")
        return None
    if not config.SUPABASE_STORAGE_BUCKET:
        logging.error("Supabase storage bucket name not configured.")
        return None

    try:
        logging.info(f"Uploading text file to Supabase Storage at path: {destination_path}")
        supabase.storage.from_(config.SUPABASE_STORAGE_BUCKET).upload(
            path=destination_path,
            file=text_content.encode("utf-8"),
            file_options={"content-type": "text/plain; charset=utf-8", "upsert": "true"}
        )
        logging.info(f"Successfully uploaded text file to path: {destination_path}")
        return destination_path
    except Exception as e:
        logging.error(f"Error uploading text file to Supabase Storage: {e}")
        return None


def update_job_with_resume_link(job_id: str, customized_resume_id: str,  new_status: Optional[str] = "resume_generated") -> bool:
    """
    Updates the job record in the Supabase table with the resume link and optionally a new status.

    Args:
        job_id: The unique ID of the job to update.
        customized_resume_id: The id the generated resume in Supabase customized_resumes table.
        new_status: The status to set for the job after processing (e.g., 'resume_generated').
                    Set to None to only update the link without changing status.

    Returns:
        True if the update was successful, False otherwise.
    """
    if not job_id or not customized_resume_id:
        logging.error("Job ID and Customized Resume id are required for updating the job.")
        return False

    try:
        update_data = {"customized_resume_id": customized_resume_id}
        # if new_status:
        #     update_data["job_state"] = new_status # Assuming 'status' is your column name

        logging.info(f"Updating job {job_id} with resume link, resume id and status '{new_status or 'unchanged'}'...")

        response = supabase.table(config.SUPABASE_TABLE_NAME)\
                           .update(update_data)\
                           .eq("job_id", job_id)\
                           .execute()

        # Check if the update affected any rows (response.data might contain updated rows)
        if response.data:
            logging.info(f"Successfully updated job {job_id}.")
            return True
        else:
            # This might happen if the job_id didn't exist or matched 0 rows
            logging.warning(f"Update query executed for job {job_id}, but no rows seemed to be affected.")
            # Depending on strictness, you might return False here
            return False # Treat as failure if no row was confirmed updated

    except Exception as e:
        logging.error(f"Error updating job {job_id} in Supabase: {e}")
        return False

def save_customized_resume(resume_data: 'Resume', resume_path: str) -> Optional[Any]: # Return type changed
    """
    Saves a customized resume to the Supabase 'customized_resumes' table.

    Args:
        resume_data: A Resume object (Pydantic model) containing the resume details.
        resume_path: The path of the uploaded resume in storage.

    Returns:
        The ID (typically string UUID or integer) of the inserted resume if successful, None otherwise.
    """

    if not resume_path:
        logging.error("Resume Path is required for saving the resume.")
        return False

    if not resume_data:
        logging.error("No resume data provided to save.")
        return None

    if not hasattr(config, 'SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME') or \
       not config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME:
        logging.error("SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME is not defined in config.py")
        return None

    try:
        # Convert Pydantic model to dict for Supabase
        if hasattr(resume_data, 'model_dump'):
            data_to_insert = resume_data.model_dump(exclude_none=True)
        else:
            data_to_insert = resume_data.dict(exclude_none=True)

        data_to_insert['resume_link'] = resume_path

        logging.info(
            f"Saving customized resume for email: {getattr(resume_data, 'email', 'N/A')} "
            f"with path '{resume_path}' to table '{config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME}'"
        )

        response = supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)\
                           .insert(data_to_insert)\
                           .execute()

        if response.data and len(response.data) > 0:
            inserted_record = response.data[0]
            if 'id' in inserted_record:
                resume_id = inserted_record['id']
                logging.info(
                    f"Successfully saved customized resume for {getattr(resume_data, 'email', 'N/A')} "
                    f"with ID: {resume_id}."
                )
                return resume_id
            else:
                logging.warning(
                    f"Customized resume for {getattr(resume_data, 'email', 'N/A')} saved, "
                    f"but 'id' key not found in the response data. Full record: {inserted_record}"
                )
                return None
        else:
            error_message = "Unknown error"
            if hasattr(response, 'error') and response.error:
                error_message = response.error
                logging.error(
                    f"Failed to save customized resume for {getattr(resume_data, 'email', 'N/A')}. "
                    f"Supabase Error: {error_message}"
                )
            elif hasattr(response, 'message') and response.message:
                error_message = response.message
                logging.error(
                    f"Failed to save customized resume for {getattr(resume_data, 'email', 'N/A')}. "
                    f"Supabase API Error: {error_message}"
                )
            else:
                logging.warning(
                    f"Customized resume for {getattr(resume_data, 'email', 'N/A')} might not have been saved "
                    f"or ID not returned. Response data is empty or missing. Response: {response}"
                )
            return None

    except Exception as e:
        logging.error(
            f"Error saving customized resume for {getattr(resume_data, 'email', 'N/A')} to Supabase: {e}",
            exc_info=True
        )
        return None

def get_customized_resume(resume_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetches a customized resume record from Supabase by ID.
    """
    if not resume_id:
        return None
    
    try:
        logging.info(f"Fetching customized resume data from database for ID: {resume_id}")
        response = supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)\
            .select("*")\
            .eq("id", resume_id)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logging.error(f"Error fetching customized resume {resume_id}: {e}")
        return None


def get_existing_customized_resume_for_company(company: str, exclude_job_id: str = None) -> Optional[Any]:
    """
    Checks if any job from the same company already has a customized resume.
    Returns the customized_resume_id if found, None otherwise.
    """
    if not company:
        return None

    try:
        query = supabase.table(config.SUPABASE_TABLE_NAME)\
            .select("customized_resume_id")\
            .eq("company", company)\
            .not_.is_("customized_resume_id", None)
        if exclude_job_id:
            query = query.neq("job_id", exclude_job_id)
        response = query.limit(1).execute()

        if response.data and len(response.data) > 0:
            resume_id = response.data[0].get("customized_resume_id")
            if resume_id:
                logging.info(f"Found existing customized resume for company '{company}': {resume_id}")
                return resume_id
        return None
    except Exception as e:
        logging.error(f"Error checking existing customized resume for company '{company}': {e}")
        return None


# --- Base Resume Functions ---
# These functions handle storing and retrieving the user's base resume
# securely via Supabase, instead of committing sensitive files to the repo.

def download_resume_from_storage(file_name: str = "resume.pdf") -> Optional[bytes]:
    """
    Downloads the user's resume PDF from the 'resumes' Supabase Storage bucket.

    Args:
        file_name: The name of the resume file in the storage bucket.

    Returns:
        The file content as bytes, or None if download fails.
    """
    bucket_name = config.SUPABASE_RESUME_STORAGE_BUCKET
    if not bucket_name:
        logging.error("Resume storage bucket name not configured (SUPABASE_RESUME_STORAGE_BUCKET).")
        return None

    try:
        logging.info(f"Downloading '{file_name}' from Supabase Storage bucket '{bucket_name}'...")
        file_bytes = supabase.storage.from_(bucket_name).download(file_name)

        if file_bytes:
            logging.info(f"Successfully downloaded '{file_name}' ({len(file_bytes)} bytes).")
            return file_bytes
        else:
            logging.warning(f"Downloaded empty content for '{file_name}' from bucket '{bucket_name}'.")
            return None

    except Exception as e:
        logging.error(f"Error downloading '{file_name}' from Supabase Storage: {e}")
        return None


def save_base_resume(resume_data: dict) -> bool:
    """
    Saves (upserts) the parsed base resume JSON to the 'base_resume' table.
    Deletes any existing rows first to ensure only one base resume exists.

    Args:
        resume_data: The parsed resume data as a dictionary.

    Returns:
        True if saved successfully, False otherwise.
    """
    if not resume_data:
        logging.error("No resume data provided to save.")
        return False

    table_name = config.SUPABASE_BASE_RESUME_TABLE_NAME
    try:
        # Delete any existing base resume rows (there should only be one)
        logging.info(f"Clearing existing base resume data from '{table_name}'...")
        supabase.table(table_name).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

        # Insert the new base resume
        logging.info(f"Saving parsed base resume to '{table_name}'...")
        response = supabase.table(table_name).insert({
            "resume_data": resume_data
        }).execute()

        if response.data and len(response.data) > 0:
            logging.info(f"Successfully saved base resume to '{table_name}'.")
            return True
        else:
            logging.warning(f"Base resume insert returned no data. Response: {response}")
            return False

    except Exception as e:
        logging.error(f"Error saving base resume to Supabase: {e}", exc_info=True)
        return False


def cleanup_orphaned_customized_resumes(batch_size: int = 1000) -> int:
    """
    Deletes customized_resumes rows (and their storage PDFs) that no job references any more.
    The jobs.customized_resume_id FK is ON DELETE SET NULL, so purging an old job (job_manager's
    14-day cleanup) does NOT delete the customized_resumes row or its uploaded PDF — they'd
    otherwise accumulate forever. This finds and removes those orphans.
    Returns the number of orphaned resumes deleted.
    """
    try:
        # 1. Collect every customized_resume_id still referenced by a job.
        referenced_ids = set()
        offset = 0
        while True:
            response = (
                supabase.table(config.SUPABASE_TABLE_NAME)
                .select("customized_resume_id")
                .not_.is_("customized_resume_id", None)
                .range(offset, offset + batch_size - 1)
                .execute()
            )
            if not response.data:
                break
            referenced_ids.update(row["customized_resume_id"] for row in response.data if row.get("customized_resume_id"))
            offset += batch_size

        # 2. Collect every customized_resumes row.
        all_resumes = []
        offset = 0
        while True:
            response = (
                supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)
                .select("id, resume_link")
                .range(offset, offset + batch_size - 1)
                .execute()
            )
            if not response.data:
                break
            all_resumes.extend(response.data)
            offset += batch_size

        orphaned = [row for row in all_resumes if row["id"] not in referenced_ids]
        if not orphaned:
            logging.info("No orphaned customized resumes found.")
            return 0

        # 3. Delete the storage PDFs first (best-effort — don't block the DB cleanup on storage errors).
        orphaned_paths = [row["resume_link"] for row in orphaned if row.get("resume_link")]
        if orphaned_paths and config.SUPABASE_STORAGE_BUCKET:
            try:
                supabase.storage.from_(config.SUPABASE_STORAGE_BUCKET).remove(orphaned_paths)
                logging.info(f"Removed {len(orphaned_paths)} orphaned resume PDF(s) from storage.")
            except Exception as e:
                logging.warning(f"Could not remove some orphaned resume PDFs from storage: {e}")

        # 4. Delete the orphaned rows.
        orphaned_ids = [row["id"] for row in orphaned]
        supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)\
                .delete()\
                .in_("id", orphaned_ids)\
                .execute()

        logging.info(f"Deleted {len(orphaned_ids)} orphaned customized resume(s) whose job no longer exists.")
        return len(orphaned_ids)

    except Exception as e:
        logging.error(f"Error cleaning up orphaned customized resumes: {e}", exc_info=True)
        return 0


def get_base_resume() -> Optional[dict]:
    """
    Fetches the base resume JSON data from the 'base_resume' table.

    Returns:
        The resume data as a dictionary, or None if not found or on error.
    """
    table_name = config.SUPABASE_BASE_RESUME_TABLE_NAME
    try:
        logging.info(f"Fetching base resume from '{table_name}'...")
        response = supabase.table(table_name)\
            .select("resume_data")\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if response.data and len(response.data) > 0:
            resume_data = response.data[0].get("resume_data")
            if resume_data:
                logging.info("Successfully fetched base resume data from Supabase.")
                return resume_data
            else:
                logging.warning("Base resume row found but 'resume_data' is empty.")
                return None
        else:
            logging.warning("No base resume found in Supabase. Please run the 'Parse Resume' workflow first.")
            return None

    except Exception as e:
        logging.error(f"Error fetching base resume from Supabase: {e}", exc_info=True)
        return None

