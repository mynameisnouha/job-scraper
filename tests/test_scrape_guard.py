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
        assert "85 fetched" in message and "12 saved" in message


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
        assert "linkedin: 85 fetched, 12 saved" in text
        assert "arbeitsagentur: 0 fetched" in text
        assert "broken source(s): arbeitsagentur" in text


class TestBucketsPartitionFetched:
    """fetched = already_in_db + filtered_out + saved. A count that means three
    things at once is how a dead source hides, so the buckets must add up."""

    def test_the_four_buckets_sum_to_fetched(self):
        o = SourceOutcome("linkedin")
        o.record_query(fetched=170, new=39, already_in_db=101)
        o.record_filtered("internship", 18)
        o.record_filtered("freelance", 7)
        o.record_filtered("no_description", 5)

        assert o.filtered_total == 30
        assert o.already_in_db + o.filtered_total + o.new == o.fetched
        assert o.unaccounted == 0

    def test_a_leftover_is_surfaced_not_absorbed(self):
        o = SourceOutcome("linkedin")
        o.record_query(fetched=100, new=10, already_in_db=50)
        assert o.unaccounted == 40
        _, message = scrape_guard.summarize(o)
        assert "40 unaccounted" in message

    def test_reasons_accumulate(self):
        o = SourceOutcome("linkedin")
        o.record_filtered("internship")
        o.record_filtered("internship")
        o.record_filtered("freelance", 3)
        assert o.filtered_out == {"internship": 2, "freelance": 3}

    def test_a_zero_count_records_nothing(self):
        o = SourceOutcome("linkedin")
        o.record_filtered("over_per_query_limit", 0)
        assert o.filtered_out == {}

    def test_the_log_line_names_the_reasons(self):
        o = SourceOutcome("linkedin")
        o.record_query(fetched=170, new=39, already_in_db=101)
        o.record_filtered("internship", 30)
        _, message = scrape_guard.summarize(o)

        assert "170 fetched" in message
        assert "39 saved" in message
        assert "101 already in the database" in message
        assert "internship 30" in message


class TestStepSummary:
    """Each matrix leg writes its own row to $GITHUB_STEP_SUMMARY (Step A.1)."""

    def test_one_row_per_source_with_the_counts(self):
        healthy = SourceOutcome("linkedin")
        healthy.record_query(fetched=170, new=39, already_in_db=101)
        healthy.record_filtered("internship", 30)
        healthy.elapsed_seconds = 421.6
        table = scrape_guard.step_summary([healthy])

        assert "| Source | Fetched | In DB | Filtered | Saved | Elapsed | Status |" in table
        assert "| linkedin | 170 | 101 | 30 | 39 | 422s | ok |" in table
        assert "linkedin filtered: internship 30" in table

    def test_a_broken_source_is_called_out_under_the_table(self):
        table = scrape_guard.step_summary([outcome("linkedin", fetched=0)])
        assert "| linkedin | 0 | 0 | 0 | 0 | 0s | BROKEN |" in table
        assert "linkedin fetched nothing" in table

    def test_counters_that_do_not_add_up_are_flagged_in_the_summary(self):
        table = scrape_guard.step_summary([outcome("linkedin", fetched=100, new=10)])
        assert "reached no bucket" in table

    def test_no_sources_still_renders(self):
        assert "No sources ran" in scrape_guard.step_summary([])

    def test_written_to_the_github_file_when_present(self, tmp_path, monkeypatch):
        target = tmp_path / "summary.md"
        target.write_text("### Earlier step" + chr(10), encoding="utf-8")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))

        o = SourceOutcome("linkedin")
        o.record_query(fetched=85, new=12, already_in_db=73)
        assert scrape_guard.write_step_summary([o]) is True
        written = target.read_text(encoding="utf-8")
        assert written.startswith("### Earlier step")   # appended, not clobbered
        assert "| linkedin | 85 | 73 | 0 | 12 |" in written

    def test_silent_outside_actions(self, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        assert scrape_guard.write_step_summary([outcome("linkedin", fetched=1, new=1)]) is False

    def test_an_unwritable_summary_never_fails_the_scrape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "no-such-dir" / "s.md"))
        assert scrape_guard.write_step_summary([outcome("linkedin", fetched=1, new=1)]) is False
