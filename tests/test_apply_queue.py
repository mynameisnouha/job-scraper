from datetime import datetime, timedelta, timezone

from review import apply_queue


def job(job_id, score=None, effort=None, **extra):
    breakdown = dict(extra.pop("score_breakdown", {}) or {})
    if effort is not None:
        breakdown["application_effort_hours"] = effort
    return {"job_id": job_id, "resume_score": score, "score_breakdown": breakdown, **extra}


class TestSortJobs:
    def test_score_mode_puts_the_best_first(self):
        jobs = [job("a", 40), job("b", 91), job("c", 70)]
        assert [j["job_id"] for j in apply_queue.sort_jobs(jobs, "score")] == ["b", "c", "a"]

    def test_unscored_jobs_sink_rather_than_crash(self):
        jobs = [job("a", None), job("b", 50)]
        assert [j["job_id"] for j in apply_queue.sort_jobs(jobs, "score")] == ["b", "a"]

    def test_effort_mode_puts_the_cheapest_first(self):
        jobs = [job("a", 90, effort=4), job("b", 50, effort=0.5), job("c", 70, effort=2)]
        assert [j["job_id"] for j in apply_queue.sort_jobs(jobs, "effort")] == ["b", "c", "a"]

    def test_effort_ties_break_on_score(self):
        jobs = [job("a", 60, effort=1), job("b", 88, effort=1)]
        assert [j["job_id"] for j in apply_queue.sort_jobs(jobs, "effort")] == ["b", "a"]

    def test_jobs_without_an_estimate_sort_last_not_free(self):
        """A missing estimate is unknown effort, not zero effort."""
        jobs = [job("no_estimate", 95), job("cheap", 50, effort=3)]
        assert [j["job_id"] for j in apply_queue.sort_jobs(jobs, "effort")] == \
               ["cheap", "no_estimate"]

    def test_unknown_mode_falls_back_to_score(self):
        jobs = [job("a", 10), job("b", 20)]
        assert [j["job_id"] for j in apply_queue.sort_jobs(jobs, "nonsense")] == ["b", "a"]

    def test_a_junk_effort_value_is_treated_as_missing(self):
        assert apply_queue.effort_hours(job("a", 50, score_breakdown={
            "application_effort_hours": "about a day"})) is None


class TestCursor:
    def test_clamps_inside_the_queue(self):
        assert apply_queue.clamp_cursor(-3, 5) == 0
        assert apply_queue.clamp_cursor(9, 5) == 4
        assert apply_queue.clamp_cursor(2, 5) == 2

    def test_empty_queue_parks_at_zero(self):
        assert apply_queue.clamp_cursor(4, 0) == 0

    def test_resume_follows_the_job_not_the_position(self):
        """
        The queue refreshes underneath you. Anchoring on the index would silently
        move the cursor to a different job than the one last looked at.
        """
        before = [job("a", 90), job("b", 80), job("c", 70)]
        assert apply_queue.resume_cursor(before, "c", fallback=2) == 2
        # A new, higher-scoring job arrives and pushes everything down one.
        after = [job("new", 99)] + before
        assert apply_queue.resume_cursor(after, "c", fallback=2) == 3

    def test_resume_falls_back_when_the_job_is_gone(self):
        """Applied or skipped, the row leaves the queue — hold the position."""
        jobs = [job("a", 90), job("b", 80), job("c", 70)]
        assert apply_queue.resume_cursor(jobs, "applied_already", fallback=1) == 1

    def test_fallback_is_clamped_too(self):
        assert apply_queue.resume_cursor([job("a", 1)], None, fallback=17) == 0
        assert apply_queue.resume_cursor([], None, fallback=17) == 0


class TestVocabulary:
    def test_every_skip_reason_has_a_label(self):
        assert set(apply_queue.SKIP_REASONS) == set(apply_queue.SKIP_REASON_LABELS)

    def test_shortcuts_are_unique_single_keys(self):
        keys = [key for key, _ in apply_queue.SHORTCUTS]
        assert len(keys) == len(set(keys))
        assert all(len(key) == 1 for key in keys)


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def found(delta, **extra):
    return job("x", 80, scraped_at=(NOW - delta).isoformat(), **extra)


class TestDateWindow:
    def test_every_window_has_a_label(self):
        assert set(apply_queue.DATE_WINDOW_KEYS) == set(apply_queue.DATE_WINDOW_LABELS)

    def test_window_keeps_what_is_inside_it_and_drops_the_rest(self):
        assert apply_queue.within_window(found(timedelta(hours=3)), "24h", NOW)
        assert not apply_queue.within_window(found(timedelta(days=2)), "24h", NOW)
        assert apply_queue.within_window(found(timedelta(days=2)), "3d", NOW)

    def test_any_time_keeps_everything_including_undated_jobs(self):
        assert apply_queue.within_window(found(timedelta(days=400)), "all", NOW)
        assert apply_queue.within_window(job("x", 80), "all", NOW)

    def test_an_undated_job_is_not_smuggled_into_a_bounded_window(self):
        """No stamp is not the same as fresh — only 'Any time' shows those."""
        assert not apply_queue.within_window(job("x", 80), "24h", NOW)
        assert not apply_queue.within_window(job("x", 80, scraped_at="not a date"), "7d", NOW)

    def test_clock_skew_counts_as_brand_new(self):
        assert apply_queue.within_window(found(timedelta(minutes=-5)), "24h", NOW)

    def test_a_naive_stamp_is_read_as_utc(self):
        naive = job("x", 80, scraped_at="2026-09-07T11:00:00")
        assert apply_queue.within_window(naive, "24h", NOW)


class TestFormatFound:
    def test_under_a_day_shows_the_hour_and_the_elapsed_time(self):
        label = apply_queue.format_found(found(timedelta(hours=3)), NOW)
        assert label.endswith("3h ago")
        assert ":" in label.split(" · ")[0]

    def test_minutes_while_it_is_still_that_fresh(self):
        assert apply_queue.format_found(found(timedelta(minutes=42)), NOW).endswith("42m ago")
        assert apply_queue.format_found(found(timedelta(seconds=20)), NOW).endswith("just now")

    def test_past_a_day_only_the_date_survives(self):
        """The hour stops meaning anything once you are a day late to a posting."""
        expected = (NOW - timedelta(days=3)).astimezone().strftime("%Y-%m-%d")
        assert apply_queue.format_found(found(timedelta(days=3)), NOW) == expected
        assert "ago" not in apply_queue.format_found(found(timedelta(hours=25)), NOW)

    def test_no_stamp_means_no_label_rather_than_a_guess(self):
        assert apply_queue.format_found(job("x", 80), NOW) is None
        assert apply_queue.format_found(job("x", 80, scraped_at="whenever"), NOW) is None
