import os
from dotenv import load_dotenv

load_dotenv()

# --- DO NOT MODIFY THE BELOW SECTION ---

# =================================================================
# 1. CORE SYSTEM CONFIGURATION (Do Not Modify)
# =================================================================
SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_TABLE_NAME: str = "jobs"
SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME = "customized_resumes"
SUPABASE_STORAGE_BUCKET="personalized_resumes"
SUPABASE_RESUME_STORAGE_BUCKET="resumes"
SUPABASE_BASE_RESUME_TABLE_NAME = "base_resume"
BASE_RESUME_PATH = "resume.json"

# API keys — set only the key(s) needed for your chosen provider.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_FIRST_API_KEY")

# =================================================================
# 2. USER PREFERENCES (Editable)
# =================================================================

# --- LLM Settings ---
# Use any model supported by LiteLLM (gemini, openai/gpt-4o-mini, groq/llama-3.3-70b-versatile)
# Full list of supported models & naming: https://docs.litellm.ai/docs/providers
LLM_MODEL = "anthropic/claude-sonnet-4-6"

# Cheap fast model for the pre-scoring screen pass (hard gates + rough band).
# Only jobs that pass the screen get the full (expensive) scoring call.
SCREENING_ENABLED = True
LLM_SCREEN_MODEL = "anthropic/claude-haiku-4-5"
JOBS_TO_SCREEN_PER_RUN = 150
LLM_SCREEN_MAX_RPM = 30
LLM_SCREEN_REQUEST_DELAY = 1

# --- Search Configuration ---
LINKEDIN_SEARCH_QUERIES = [
    "Graduate Program",
    "Data Scientist",
    "Machine Learning Engineer",
    "AI Engineer",
    "NLP Engineer",
    "MLOps Engineer",
    "Computer Vision Engineer",
    "Deep Learning Engineer",
    "LLM Engineer",
    "AI Research Scientist",
    "Machine Learning Scientist",
    "AI Developer",
    "Prompt Engineer",
    "AI Specialist",
    "Data Scientist PySpark",
    "Big Data Scientist",
    "Applied Scientist",
]
LINKEDIN_LOCATION = "Germany"
LINKEDIN_GEO_ID = 101282230      # Singapore: 102454443, Dubai: 100205264
LINKEDIN_JOB_TYPE = "F" # F=Full-time, C=Contract, P=Part-time, T=Temporary, I=Internship
LINKEDIN_JOB_POSTING_DATE = "r86400" # r86400=Past 24h, r604800=Past week
LINKEDIN_F_WT = "1%2C2%2C3" # 1=Onsite, 2=Remote, 3=Hybrid (URL-encoded comma list; single value like "3" also works)
LINKEDIN_F_E = "2%2C3" # Experience level: 1=Internship, 2=Entry level, 3=Associate, 4=Mid-Senior, 5=Director

CAREERS_FUTURE_SEARCH_QUERIES = ["IT Support", "Full Stack Web Developer", "Application Support", "Cybersecurity Analyst", "fresher developer"]
CAREERS_FUTURE_SEARCH_CATEGORIES = ["Information Technology"]
CAREERS_FUTURE_SEARCH_EMPLOYMENT_TYPES = ["Full Time"]

# --- Indeed Configuration ---
INDEED_SEARCH_QUERIES = [
    "Graduate Program",
    "Data Scientist",
    "Machine Learning Engineer",
    "AI Engineer",
    "Data Engineer",
    "NLP Engineer",
    "Computer Vision Engineer",
    "Deep Learning Engineer",
    "LLM Engineer",
    "AI Research Scientist",
    "MLOps Engineer",
    "Machine Learning Scientist",
    "AI Developer",
    "Prompt Engineer",
    "AI Specialist",
    "Python Developer",
]
INDEED_LOCATION = "Germany"

# --- Manual Jobs (any source) ---
MANUAL_JOBS_PATH = "manual_jobs.json"

# --- Candidate Profile ---
# Untracked and gitignored: it holds residence-permit status, salary strategy and contract
# deadlines. Copy candidate_profile.json.example to this path and fill it in.
CANDIDATE_PROFILE_PATH = "candidate_profile.json"

# --- Processing Limits ---
SCRAPING_SOURCES = ["linkedin", "indeed"] # "linkedin", "indeed", "careers_future"
JOBS_TO_SCORE_PER_RUN = 60
JOBS_TO_CUSTOMIZE_PER_RUN = 3
RESUME_CUSTOMIZATION_MIN_SCORE = 70  # Only tailor resumes for strong matches; below this, effort is better spent elsewhere
MAX_JOBS_PER_SEARCH = {
    "linkedin": 5,
    "indeed": 5,
    "careers_future": 10,
}

# =================================================================
# 3. ADVANCED SYSTEM SETTINGS (Modify with Caution)
# =================================================================
LLM_MAX_RPM = 10
LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 10
LLM_DAILY_REQUEST_BUDGET = 0
# Self-imposed pacing, not a provider limit. At 8s this added ~11 minutes of pure
# sleep to an 86-job backfill; 2s is still well inside Sonnet's rate limits and
# keeps consecutive calls inside the 5-minute prompt-cache window.
LLM_REQUEST_DELAY_SECONDS = 2

LINKEDIN_MAX_START = 1 
INDEED_MAX_START = 2   # Indeed pages to paginate beyond the first (needed so dedup can look past page 1)
INDEED_SEARCH_DELAY = 10
INDEED_DETAIL_DELAY = 8
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15

JOB_EXPIRY_DAYS = 30
JOB_CHECK_DAYS = 3
JOB_DELETION_DAYS = 60
JOB_CHECK_LIMIT = 50
ACTIVE_CHECK_TIMEOUT = 20
ACTIVE_CHECK_MAX_RETRIES = 2
ACTIVE_CHECK_RETRY_DELAY = 10

