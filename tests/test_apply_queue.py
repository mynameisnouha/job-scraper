import apply_queue


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
