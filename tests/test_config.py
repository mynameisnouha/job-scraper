import config


class TestConfig:
    """Tests for config module values (not env-dependent ones)."""

    def test_supabase_table_name(self):
        assert config.SUPABASE_TABLE_NAME == "jobs"

    def test_customized_resumes_table_name(self):
        assert config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME == "customized_resumes"

    def test_base_resume_path(self):
        assert config.BASE_RESUME_PATH == "resume.json"

    def test_scraping_sources_default(self):
        assert isinstance(config.SCRAPING_SOURCES, list)

    def test_linkedin_search_queries(self):
        assert isinstance(config.LINKEDIN_SEARCH_QUERIES, list)
        assert len(config.LINKEDIN_SEARCH_QUERIES) > 0

    def test_linkedin_job_type(self):
        assert config.LINKEDIN_JOB_TYPE == "F"

    def test_jobs_to_score_per_run(self):
        assert config.JOBS_TO_SCORE_PER_RUN > 0

    def test_jobs_to_customize_per_run(self):
        assert config.JOBS_TO_CUSTOMIZE_PER_RUN >= 0

    def test_job_expiry_days(self):
        assert config.JOB_EXPIRY_DAYS > 0

    def test_job_deletion_days(self):
        assert config.JOB_DELETION_DAYS > config.JOB_EXPIRY_DAYS

    def test_rate_limit_settings(self):
        assert config.LLM_MAX_RPM > 0
        assert config.LLM_MAX_RETRIES > 0
        assert config.REQUEST_TIMEOUT > 0
