"""`scraper.py --source X` runs exactly X.

Each matrix leg in run_all.yml is one runner on one machine, so a leg must never
quietly run a source another leg is already scraping.
"""
import pytest

import config
import scrape_guard
import scraper


@pytest.fixture
def ran(monkeypatch):
    """Replace every source runner with a recorder. Returns the call log."""
    calls = []

    def runner(name, fetched=5, new=1):
        def _run():
            calls.append(name)
            outcome = scrape_guard.SourceOutcome(name)
            outcome.record_query(fetched=fetched, new=new)
            return outcome
        return _run

    monkeypatch.setattr(scraper, "SOURCE_RUNNERS", {
        "linkedin": runner("linkedin"),
        "careers_future": runner("careers_future"),
    })
    return calls


class TestSourceDispatch:
    def test_one_source_runs_only_that_source(self, ran):
        assert scraper.main(["--source", "linkedin"]) == 0
        assert ran == ["linkedin"]

    def test_the_other_source_is_selectable_too(self, ran):
        scraper.main(["--source", "careers_future"])
        assert ran == ["careers_future"]

    def test_the_flag_is_repeatable(self, ran):
        scraper.main(["--source", "linkedin", "--source", "careers_future"])
        assert ran == ["linkedin", "careers_future"]

    def test_a_repeated_source_runs_once(self, ran):
        scraper.main(["--source", "linkedin", "--source", "linkedin"])
        assert ran == ["linkedin"]

    def test_an_explicit_source_overrides_the_config(self, ran, monkeypatch):
        """A matrix leg gets the source it asked for, whatever SCRAPING_SOURCES says."""
        monkeypatch.setattr(config, "SCRAPING_SOURCES", ["linkedin"])
        scraper.main(["--source", "careers_future"])
        assert ran == ["careers_future"]

    def test_no_flag_runs_every_configured_source(self, ran, monkeypatch):
        monkeypatch.setattr(config, "SCRAPING_SOURCES", ["linkedin", "careers_future"])
        scraper.main([])
        assert ran == ["linkedin", "careers_future"]

    def test_config_order_is_respected(self, ran, monkeypatch):
        monkeypatch.setattr(config, "SCRAPING_SOURCES", ["careers_future", "linkedin"])
        scraper.main([])
        assert ran == ["careers_future", "linkedin"]

    def test_an_unrunnable_configured_source_is_skipped_not_fatal(self, ran, monkeypatch):
        monkeypatch.setattr(config, "SCRAPING_SOURCES", ["linkedin", "indeed"])
        assert scraper.main([]) == 0
        assert ran == ["linkedin"]

    def test_no_runnable_sources_is_an_error(self, ran, monkeypatch):
        monkeypatch.setattr(config, "SCRAPING_SOURCES", ["indeed"])
        assert scraper.main([]) == 1
        assert ran == []

    def test_an_unknown_source_is_rejected_before_anything_runs(self, ran):
        with pytest.raises(SystemExit) as exit_info:
            scraper.main(["--source", "monster"])
        assert exit_info.value.code == 2
        assert ran == []


class TestExitCodeFromTheGuard:
    def test_a_broken_leg_exits_non_zero(self, monkeypatch):
        def dead():
            return scrape_guard.SourceOutcome("linkedin")  # fetched nothing

        monkeypatch.setattr(scraper, "SOURCE_RUNNERS", {"linkedin": dead})
        assert scraper.main(["--source", "linkedin"]) == 1

    def test_a_leg_with_no_new_jobs_exits_zero(self, monkeypatch):
        def quiet():
            outcome = scrape_guard.SourceOutcome("linkedin")
            outcome.record_query(fetched=40, new=0)
            return outcome

        monkeypatch.setattr(scraper, "SOURCE_RUNNERS", {"linkedin": quiet})
        assert scraper.main(["--source", "linkedin"]) == 0


class TestElapsedTiming:
    def test_each_source_is_timed(self, ran):
        outcomes = scraper.run_sources(["linkedin"])
        assert outcomes[0].elapsed_seconds >= 0
