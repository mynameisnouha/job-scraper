"""The custom-resume rescore must alert on jobs it lifts over the bar.

For a long time it did not: only the initial scoring phase called notify, so a
job whose *custom* score crossed the threshold — the common case, since the
custom resume is written for jobs that already looked promising — was scored,
stored, and never mentioned. It surfaced as "a 78/100 in the queue I never got
an email about".
"""
from unittest.mock import MagicMock, patch

import config
from scoring import score_jobs


def _breakdown(score):
    """Just enough of a ScoreBreakdown for the alert path."""
    obj = MagicMock()
    obj.overall_score = score
    obj.model_dump.return_value = {"overall_score": score, "one_line_verdict": "v"}
    return obj


def _run(jobs, scores, threshold=70):
    """Run the rescore phase over `jobs`, returning what notify was handed."""
    sent = []

    def fake_notify(matches, min_score=None):
        sent.extend(matches)
        return len(matches)

    with patch.object(config, "EMAIL_ALERT_MIN_SCORE", threshold), \
         patch.object(score_jobs.supabase_utils, "get_jobs_to_rescore", return_value=jobs), \
         patch.object(score_jobs.supabase_utils, "get_customized_resume", return_value={"name": "N"}), \
         patch.object(score_jobs.supabase_utils, "update_job_score", return_value=True), \
         patch.object(score_jobs.supabase_utils, "get_job_urls",
                      return_value={j["job_id"]: f"https://example.com/{j['job_id']}" for j in jobs}), \
         patch.object(score_jobs, "format_resume_to_text", return_value="resume"), \
         patch.object(score_jobs, "finalize_batch_recommendations"), \
         patch.object(score_jobs, "get_resume_score_from_ai",
                      side_effect=[_breakdown(s) for s in scores]), \
         patch.object(score_jobs.notify, "notify_matches", side_effect=fake_notify), \
         patch.object(config, "LLM_REQUEST_DELAY_SECONDS", 0):
        score_jobs.rescore_jobs_with_custom_resume()
    return sent


def _job(job_id, previous_score):
    return {
        "job_id": job_id,
        "job_title": f"Role {job_id}",
        "company": "valantic",
        "customized_resume_id": f"cr-{job_id}",
        "resume_link": None,
        "resume_score": previous_score,
    }


class TestRescoreAlerts:
    def test_alerts_when_the_rescore_crosses_the_threshold(self):
        sent = _run([_job("a", 62)], [78])
        assert [j["job_id"] for j in sent] == ["a"]
        assert sent[0]["resume_score"] == 78

    def test_no_second_alert_for_a_job_already_above_the_bar(self):
        # Mailed at the initial stage; mailing it again teaches the inbox to
        # ignore the alert.
        assert _run([_job("a", 74)], [81]) == []

    def test_silent_when_the_rescore_stays_below_the_bar(self):
        assert _run([_job("a", 40)], [55]) == []

    def test_alert_carries_the_job_url_the_rescore_rpc_omits(self):
        sent = _run([_job("a", 10)], [90])
        assert sent[0]["job_url"] == "https://example.com/a"

    def test_breakdown_is_dumped_not_left_as_a_model(self):
        # notify.build_message does breakdown.get(...); a Pydantic object there
        # would raise inside the composer and swallow the whole alert.
        sent = _run([_job("a", 10)], [90])
        assert isinstance(sent[0]["score_breakdown"], dict)

    def test_only_the_crossing_jobs_are_alerted_in_a_mixed_batch(self):
        sent = _run([_job("a", 10), _job("b", 71), _job("c", 60)], [88, 90, 72])
        assert [j["job_id"] for j in sent] == ["a", "c"]
