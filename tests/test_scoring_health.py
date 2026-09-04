import check_scoring_health as csh

COMPLETE = {
    "competitive_context": {"p_first_round_interview": {"as_is": 0.1, "after_fixes": 0.2}},
    "dimension_scores": {"must_have_coverage": 70},
    "one_line_verdict": "Worth applying.",
}
DEGRADED = {"overall_score": 60, "reasoning": "…"}
SCREENED = {"overall_score": 25, "screen_only": True, "reasoning": "Screened out"}


class TestClassify:
    def test_complete(self):
        assert csh.classify(COMPLETE) == "complete"

    def test_degraded_when_a_required_field_is_missing(self):
        assert csh.classify(DEGRADED) == "degraded"

    def test_screened_out_is_not_a_failure(self):
        """Screened jobs never get a full scoring pass — absence is correct there."""
        assert csh.classify(SCREENED) == "screened_out"

    def test_empty_breakdown(self):
        assert csh.classify({}) == "unscored"
        assert csh.classify(None) == "unscored"

    def test_partial_output_still_counts_as_degraded(self):
        partial = dict(COMPLETE)
        partial.pop("one_line_verdict")
        assert csh.classify(partial) == "degraded"

    def test_empty_values_count_as_missing(self):
        """An empty dict satisfies the schema but carries no information."""
        hollow = {"competitive_context": {}, "dimension_scores": {}, "one_line_verdict": ""}
        assert csh.classify(hollow) == "degraded"


class TestMissingFields:
    def test_lists_every_absent_field(self):
        assert csh.missing_fields(DEGRADED) == [
            "competitive_context", "dimension_scores", "one_line_verdict"]

    def test_empty_when_complete(self):
        assert csh.missing_fields(COMPLETE) == []
