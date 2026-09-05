"""The zero-yield guard: a source that fetches nothing fails the run, a source
that fetches plenty and saves nothing new does not.

This is the check that was missing when Indeed returned zero jobs for twelve
weeks while every run stayed green.
"""
import logging

import pytest

import scrape_guard
from scrape_guard import BROKEN, NO_NEW, OK, SourceOutcome


def outcome(source="linkedin", fetched=0, new=0):
    o = SourceOutcome(source)
    o.record_query(fetched=fetched, new=new)
    return o


class TestClassification:
    def test_zero_fetched_is_broken(self):
        assert outcome(fetched=0, new=0).status == BROKEN

    def test_fetched_but_nothing_new_is_not_a_failure(self):
        assert outcome(fetched=85, new=0).status == NO_NEW

    def test_fetched_and_new_is_ok(self):
        assert outcome(fetched=85, new=12).status == OK

    def test_counts_accumulate_across_queries(self):
        o = SourceOutcome("linkedin")
        o.record_query(fetched=10, new=2)
        o.record_query(fetched=7, new=0)
        o.record_query(fetched=0, new=0)
        assert (o.fetched, o.new) == (17, 2)
        assert o.status == OK


class TestExitCode:
    def test_broken_source_exits_non_zero(self):
        assert scrape_guard.report([outcome(fetched=0)]) == 1

    def test_all_duplicates_exits_zero(self):
        assert scrape_guard.report([outcome(fetched=85, new=0)]) == 0

    def test_healthy_source_exits_zero(self):
        assert scrape_guard.report([outcome(fetched=85, new=12)]) == 0

    def test_one_broken_source_among_healthy_ones_still_fails(self):
        results = [
            outcome("linkedin", fetched=85, new=12),
            outcome("arbeitsagentur", fetched=0),
            outcome("ats", fetched=40, new=0),
        ]
        assert scrape_guard.report(results) == 1

    def test_no_sources_at_all_exits_zero(self):
        assert scrape_guard.report([]) == 0


class TestLogLevels:
    """The middle case becomes routine post-dedup, so it must read as routine."""

    def test_broken_logs_at_error(self):
        level, _ = scrape_guard.summarize(outcome(fetched=0))
        assert level == logging.ERROR

    def test_all_duplicates_logs_at_info(self):
        level, message = scrape_guard.summarize(outcome(fetched=85, new=0))
        assert level == logging.INFO
        assert "already in the database" in message
        assert "Normal" in message

    def test_healthy_logs_at_info_with_both_counts(self):
        level, message = scrape_guard.summarize(outcome(fetched=85, new=12))
        assert level == logging.INFO
        assert "85 fetched" in message and "12 new" in message


class TestBlockDiagnostics:
    """Zero fetched is ambiguous on its own: a block and an empty result set both
    parse to zero cards. The failure message carries status and body size so a
    human can tell which — the tool deliberately does not guess."""

    def test_non_2xx_status_and_body_size_are_reported(self):
        o = SourceOutcome("linkedin")
        o.record_attempt("Data Scientist", status_code=403, body_bytes=2481,
                         items_parsed=0, error="HTTPError 403 Client Error")
        level, message = scrape_guard.summarize(o)

        assert level == logging.ERROR
        assert "status=403" in message
        assert "body=2481B" in message
        assert "parsed=0" in message
        assert "'Data Scientist'" in message

    def test_a_200_with_a_large_body_and_no_cards_is_visible_as_such(self):
        o = SourceOutcome("linkedin")
        o.record_attempt("AI Engineer", status_code=200, body_bytes=41022, items_parsed=0)
        _, message = scrape_guard.summarize(o)

        assert "status=200" in message
        assert "body=41022B" in message
        # The reader is given the distinction, not a verdict.
        assert "challenge page or a broken selector" in message
        assert "plausibly an empty result set" in message

    def test_a_200_with_a_small_body_reports_its_size_too(self):
        o = SourceOutcome("linkedin")
        o.record_attempt("Prompt Engineer", status_code=200, body_bytes=312, items_parsed=0)
        _, message = scrape_guard.summarize(o)
        assert "body=312B" in message

    def test_a_request_that_never_got_a_response_says_so(self):
        o = SourceOutcome("linkedin")
        o.record_attempt("NLP Engineer", error="RequestException timed out")
        _, message = scrape_guard.summarize(o)
        assert "status=no-response" in message
        assert "timed out" in message

    def test_diagnostic_lines_are_capped_but_the_remainder_is_counted(self):
        o = SourceOutcome("linkedin")
        for i in range(scrape_guard.MAX_DIAGNOSTIC_LINES + 5):
            o.record_attempt(f"query {i}", status_code=403, body_bytes=100)
        _, message = scrape_guard.summarize(o)

        assert message.count("status=403") == scrape_guard.MAX_DIAGNOSTIC_LINES
        assert "and 5 more request(s)" in message

    def test_an_uninstrumented_source_says_it_cannot_tell(self):
        _, message = scrape_guard.summarize(outcome("careers_future", fetched=0))
        assert "No HTTP responses were recorded" in message

    def test_diagnostics_are_not_printed_for_healthy_sources(self):
        o = SourceOutcome("linkedin")
        o.record_attempt("Data Scientist", status_code=200, body_bytes=41022, items_parsed=25)
        o.record_query(fetched=25, new=3)
        _, message = scrape_guard.summarize(o)
        assert "body=" not in message


class TestReportLogging:
    def test_report_logs_each_source_and_names_the_broken_ones(self, caplog):
        with caplog.at_level(logging.INFO):
            code = scrape_guard.report([
                outcome("linkedin", fetched=85, new=12),
                outcome("arbeitsagentur", fetched=0),
            ])

        assert code == 1
        text = caplog.text
        assert "linkedin: 85 fetched, 12 new." in text
        assert "arbeitsagentur: 0 fetched" in text
        assert "broken source(s): arbeitsagentur" in text
