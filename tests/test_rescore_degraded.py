import rescore_degraded
import supabase_utils

COMPLETE = {
    "competitive_context": {"p_first_round_interview": {"as_is": 0.1, "after_fixes": 0.2}},
    "dimension_scores": {"must_have_coverage": 70},
    "one_line_verdict": "Worth applying.",
}
DEGRADED = {"overall_score": 60, "reasoning": "…"}
SCREENED = {"overall_score": 25, "screen_only": True}


def row(job_id, breakdown):
    return {"job_id": job_id, "job_title": f"Role {job_id}", "company": "Co",
            "description": "desc", "score_breakdown": breakdown}


class TestFindDegraded:
    def test_selects_only_degraded_rows(self, monkeypatch):
        rows = [row("a", COMPLETE), row("b", DEGRADED), row("c", SCREENED), row("d", DEGRADED)]
        monkeypatch.setattr(supabase_utils, "get_scored_jobs_for_health_check", lambda limit: rows)
        assert [j["job_id"] for j in rescore_degraded.find_degraded(10)] == ["b", "d"]

    def test_screened_out_jobs_are_never_rescored(self, monkeypatch):
        """They were deliberately not fully scored — rescoring them wastes budget."""
        monkeypatch.setattr(supabase_utils, "get_scored_jobs_for_health_check",
                            lambda limit: [row("c", SCREENED)])
        assert rescore_degraded.find_degraded(10) == []

    def test_respects_the_limit(self, monkeypatch):
        rows = [row(str(i), DEGRADED) for i in range(10)]
        monkeypatch.setattr(supabase_utils, "get_scored_jobs_for_health_check", lambda limit: rows)
        assert len(rescore_degraded.find_degraded(3)) == 3

    def test_nothing_degraded(self, monkeypatch):
        monkeypatch.setattr(supabase_utils, "get_scored_jobs_for_health_check",
                            lambda limit: [row("a", COMPLETE)])
        assert rescore_degraded.find_degraded(10) == []
