import pytest

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


class TestCapOverrides:
    """One-off backfills need caps the steady state must not inherit. Editing the
    committed defaults leaves a window where a scheduled trigger picks them up, so
    the overrides are env vars scoped to a single run."""

    def test_the_default_is_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("JOBS_TO_SCORE_PER_RUN", raising=False)
        assert config._int_env("JOBS_TO_SCORE_PER_RUN", 15) == 15

    def test_an_env_value_wins(self, monkeypatch):
        monkeypatch.setenv("JOBS_TO_SCORE_PER_RUN", "150")
        assert config._int_env("JOBS_TO_SCORE_PER_RUN", 15) == 150

    def test_an_empty_value_is_the_default_not_zero(self, monkeypatch):
        """GitHub passes "" for an omitted input; that must not mean "score nothing"."""
        monkeypatch.setenv("JOBS_TO_SCORE_PER_RUN", "")
        assert config._int_env("JOBS_TO_SCORE_PER_RUN", 15) == 15

    def test_a_typo_raises_rather_than_running_unbounded(self, monkeypatch):
        monkeypatch.setenv("JOBS_TO_SCORE_PER_RUN", "one hundred")
        with pytest.raises(ValueError):
            config._int_env("JOBS_TO_SCORE_PER_RUN", 15)
