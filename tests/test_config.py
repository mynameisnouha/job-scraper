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


class TestAutoApplyConfig:
    def test_origins_cover_verified_ats_only(self):
        assert set(config.AUTO_APPLY_ORIGINS) == {"greenhouse", "lever", "ashby"}
        assert config.AUTO_APPLY_ORIGINS["lever"] == [
            "https://*.lever.co", "https://*.hcaptcha.com", "https://hcaptcha.com"]

    def test_statuses_shared_with_supabase_utils(self):
        from db import supabase_utils
        assert supabase_utils.AUTO_APPLY_STATUSES == config.AUTO_APPLY_STATUSES
        assert len(config.AUTO_APPLY_STATUSES) == 8

    def test_claude_bin_env_wins(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_BIN", "/x/claude")
        assert config._find_claude_bin() == "/x/claude"

    def test_claude_bin_path_then_extension_then_bare(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_BIN", raising=False)
        monkeypatch.setattr(config.shutil, "which", lambda n: "/usr/bin/claude")
        assert config._find_claude_bin() == "/usr/bin/claude"
        monkeypatch.setattr(config.shutil, "which", lambda n: None)
        monkeypatch.setattr(config.glob, "glob", lambda p: ["/a/old.exe", "/a/new.exe"])
        monkeypatch.setattr(config.os.path, "getmtime", lambda p: 2 if "new" in p else 1)
        assert config._find_claude_bin() == "/a/new.exe"
        monkeypatch.setattr(config.glob, "glob", lambda p: [])
        assert config._find_claude_bin() == "claude"


class _Stream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kw):
        self.calls.append(kw)


def test_console_streams_are_switched_to_utf8():
    out, err = _Stream(), _Stream()
    config._utf8_console((out, err))
    assert out.calls == err.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_streams_without_reconfigure_are_left_alone():
    config._utf8_console((object(), None))  # pytest capture objects, closed streams


def test_a_cp1252_pipe_no_longer_crashes_on_unicode():
    import io
    raw = io.BytesIO()
    pipe = io.TextIOWrapper(raw, encoding="cp1252")
    config._utf8_console((pipe,))
    pipe.write("café � → done")
    pipe.flush()
    assert raw.getvalue().decode("utf-8") == "café � → done"
