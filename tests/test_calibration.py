import calibration


def job(stage=None, score=None, p=None, reason=None):
    breakdown = {}
    if p is not None:
        breakdown = {"competitive_context": {"p_first_round_interview": p}}
    return {
        "application_stage": stage,
        "resume_score": score,
        "rejection_reason": reason,
        "score_breakdown": breakdown,
    }


class TestOutcomeClassification:
    def test_interview_stages_count_as_interview(self):
        for stage in ["interview_1", "interview_2", "interview_3", "offer"]:
            assert calibration.got_interview(job(stage)) is True

    def test_rejection_is_resolved_but_not_an_interview(self):
        assert calibration.is_resolved(job("rejected")) is True
        assert calibration.got_interview(job("rejected")) is False

    def test_still_applied_is_unresolved(self):
        """Waiting to hear back is censored data, not a negative outcome."""
        assert calibration.is_resolved(job("applied")) is False
        assert calibration.is_resolved(job(None)) is False


class TestPredictedProbability:
    def test_reads_after_fixes_preferentially(self):
        j = job(p={"as_is": 0.1, "after_fixes": 0.4})
        assert calibration.predicted_interview_probability(j) == 0.4

    def test_falls_back_to_as_is(self):
        j = job(p={"as_is": 0.2})
        assert calibration.predicted_interview_probability(j) == 0.2

    def test_accepts_scalar(self):
        assert calibration.predicted_interview_probability(job(p=0.3)) == 0.3

    def test_percentages_are_normalized(self):
        assert calibration.predicted_interview_probability(job(p=15)) == 0.15

    def test_clamps_out_of_range(self):
        assert calibration.predicted_interview_probability(job(p=250)) == 1.0

    def test_missing_or_garbage_returns_none(self):
        assert calibration.predicted_interview_probability(job()) is None
        assert calibration.predicted_interview_probability(job(p="n/a")) is None
        assert calibration.predicted_interview_probability({"score_breakdown": None}) is None
        assert calibration.predicted_interview_probability({"score_breakdown": "oops"}) is None


class TestBrierScore:
    def test_perfect_prediction_scores_zero(self):
        assert calibration.brier_score([(1.0, True), (0.0, False)]) == 0.0

    def test_always_half_scores_quarter(self):
        assert calibration.brier_score([(0.5, True), (0.5, False)]) == 0.25

    def test_ignores_missing_predictions(self):
        assert calibration.brier_score([(None, True), (0.0, False)]) == 0.0

    def test_none_when_nothing_scoreable(self):
        assert calibration.brier_score([]) is None
        assert calibration.brier_score([(None, True)]) is None


class TestBucketStats:
    def test_rates_computed_per_bucket_over_resolved_only(self):
        jobs = [
            job("interview_1", score=80),
            job("rejected", score=78),
            job("applied", score=82),   # pending — must not count
            job("rejected", score=40),
        ]
        rows = {r["bucket"]: r for r in calibration.bucket_stats(jobs)}
        assert rows["75-84"]["n"] == 2
        assert rows["75-84"]["interviews"] == 1
        assert rows["75-84"]["interview_rate"] == 0.5
        assert rows["<50"]["interview_rate"] == 0.0

    def test_empty_buckets_have_none_rate(self):
        rows = {r["bucket"]: r for r in calibration.bucket_stats([])}
        assert rows["85+"]["n"] == 0
        assert rows["85+"]["interview_rate"] is None

    def test_all_buckets_always_present(self):
        rows = calibration.bucket_stats([])
        assert [r["bucket"] for r in rows] == [b[0] for b in calibration.SCORE_BUCKETS]


class TestRejectionReasons:
    def test_counts_sorted_desc_and_only_rejections(self):
        jobs = [
            job("rejected", reason="german_level"),
            job("rejected", reason="german_level"),
            job("rejected", reason="visa"),
            job("interview_1", reason="ignored"),
        ]
        assert calibration.rejection_reason_counts(jobs) == {"german_level": 2, "visa": 1}

    def test_blank_reason_becomes_unspecified(self):
        assert calibration.rejection_reason_counts([job("rejected", reason="  ")]) == {"unspecified": 1}


class TestSummarize:
    def test_excludes_pending_from_rate(self):
        jobs = [job("interview_1", score=80), job("rejected", score=70), job("applied", score=90)]
        s = calibration.summarize(jobs)
        assert s["total_applied"] == 3
        assert s["pending"] == 1
        assert s["resolved"] == 2
        assert s["interview_rate"] == 0.5

    def test_not_enough_data_flag(self):
        s = calibration.summarize([job("rejected")] * 3)
        assert s["enough_data"] is False
        assert s["min_required"] == calibration.MIN_RESOLVED_FOR_METRICS

    def test_enough_data_flag_flips_at_threshold(self):
        s = calibration.summarize([job("rejected")] * calibration.MIN_RESOLVED_FOR_METRICS)
        assert s["enough_data"] is True

    def test_handles_no_applications(self):
        s = calibration.summarize([])
        assert s["resolved"] == 0
        assert s["interview_rate"] is None
        assert s["brier"] is None
        assert s["enough_data"] is False
