"""
Covers the repair pass in get_resume_score_from_ai: when the model omits a
now-mandatory field, one retry is made with the validation errors fed back,
rather than dropping the score and re-scoring the same job forever.
"""
import json

import pytest

import score_jobs
from models import ScoreBreakdown

VALID = {
    "overall_score": 72, "skills_match_score": 75, "experience_score": 65,
    "education_score": 80, "language_fit": "Full match",
    "recommendation": "apply_now", "reasoning": "Good fit.",
    "application_effort_hours": 2.0,
    "dimension_scores": {
        "must_have_coverage": 70, "evidence_strength": 60, "nice_to_have_coverage": 50,
        "seniority_fit": 65, "environment_fit": 70, "domain_fit": 40, "differentiation": 55,
    },
    "competitive_context": {
        "estimated_applicant_volume": "150-300",
        "modal_competitor": "MSc CS, 2y industry ML",
        "candidate_percentile": 60,
        "p_first_round_interview": {"as_is": 0.2, "after_fixes": 0.4},
    },
    "one_line_verdict": "Worth applying.",
}

# The failure seen in production: all the v2 fields silently missing.
MISSING_V2 = {k: v for k, v in VALID.items()
              if k not in ("dimension_scores", "competitive_context", "one_line_verdict")}

JOB = {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME",
       "level": "Entry", "description": "Build models."}


@pytest.fixture
def responses(monkeypatch):
    """Queue of payloads the fake LLM returns, plus a record of calls made."""
    queue, prompts, calls = [], [], []

    def fake_generate(prompt, response_format=None, temperature=None, **kwargs):
        prompts.append(prompt)
        calls.append({"prompt": prompt, **kwargs})
        return json.dumps(queue.pop(0))

    monkeypatch.setattr(score_jobs.primary_client, "generate_content", fake_generate)
    return queue, prompts, calls


class TestPromptCaching:
    def test_stable_content_goes_in_the_cached_system_block(self, responses):
        queue, _, calls = responses
        queue.append(VALID)
        score_jobs.get_resume_score_from_ai("MY RESUME TEXT", JOB)
        call = calls[0]
        assert call["cache_system"] is True
        # Rubric and resume are identical every call — they belong in the cache.
        assert "MY RESUME TEXT" in call["system_prompt"]
        assert "CANDIDATE PROFILE" in call["system_prompt"]

    def test_job_description_stays_out_of_the_cached_block(self, responses):
        """Caching is a prefix match — per-job text in the system block busts it."""
        queue, _, calls = responses
        queue.append(VALID)
        score_jobs.get_resume_score_from_ai("resume", JOB)
        call = calls[0]
        assert "Build models." not in call["system_prompt"]
        assert "Build models." in call["prompt"]
        assert "ML Engineer" in call["prompt"]

    def test_system_block_is_identical_across_jobs(self, responses):
        queue, _, calls = responses
        queue.extend([VALID, VALID])
        job_b = dict(JOB, job_id="j2", job_title="Data Scientist", description="Other work.")
        score_jobs.get_resume_score_from_ai("resume", JOB)
        score_jobs.get_resume_score_from_ai("resume", job_b)
        assert calls[0]["system_prompt"] == calls[1]["system_prompt"]

    def test_repair_call_reuses_the_same_system_block(self, responses):
        queue, _, calls = responses
        queue.extend([MISSING_V2, VALID])
        score_jobs.get_resume_score_from_ai("resume", JOB)
        assert len(calls) == 2
        assert calls[0]["system_prompt"] == calls[1]["system_prompt"]
        assert calls[1]["cache_system"] is True


def test_valid_first_response_makes_no_second_call(responses):
    queue, prompts, calls = responses
    queue.append(VALID)
    result = score_jobs.get_resume_score_from_ai("resume text", JOB)
    assert isinstance(result, ScoreBreakdown)
    assert len(prompts) == 1


def test_missing_fields_trigger_one_repair_call(responses):
    queue, prompts, calls = responses
    queue.extend([MISSING_V2, VALID])
    result = score_jobs.get_resume_score_from_ai("resume text", JOB)
    assert isinstance(result, ScoreBreakdown)
    assert len(prompts) == 2
    assert result.competitive_context.p_first_round_interview.after_fixes == 0.4


def test_repair_prompt_names_the_missing_fields(responses):
    queue, prompts, calls = responses
    queue.extend([MISSING_V2, VALID])
    score_jobs.get_resume_score_from_ai("resume text", JOB)
    repair = prompts[1]
    assert "REJECTED" in repair
    assert "competitive_context" in repair
    assert "dimension_scores" in repair


def test_repair_failing_too_returns_none_rather_than_raising(responses):
    queue, _, calls = responses
    queue.extend([MISSING_V2, MISSING_V2])
    assert score_jobs.get_resume_score_from_ai("resume text", JOB) is None


def test_expected_value_derived_from_the_probability(responses):
    queue, _, calls = responses
    queue.append(VALID)
    result = score_jobs.get_resume_score_from_ai("resume text", JOB)
    # after_fixes 0.4 over 2 hours of effort.
    assert result.expected_value == 0.2


def test_percentage_probabilities_are_normalized_end_to_end(responses):
    """A model answering 40 instead of 0.4 must not blow up the whole score."""
    queue, _, calls = responses
    payload = json.loads(json.dumps(VALID))
    payload["competitive_context"]["p_first_round_interview"] = {"as_is": 20, "after_fixes": 40}
    queue.append(payload)
    result = score_jobs.get_resume_score_from_ai("resume text", JOB)
    assert result.competitive_context.p_first_round_interview.after_fixes == 0.4


def test_failing_hard_gates_are_read_as_models_not_dicts(responses):
    """hard_gates became a typed model; the logging path used to call .get() on it."""
    queue, _, calls = responses
    payload = json.loads(json.dumps(VALID))
    payload["hard_gates"] = [
        {"gate": "working_language", "result": "fail", "detail": "JD is 100% German",
         "negotiable": False, "how": "", "cap_applied": 55},
        {"gate": "availability", "result": "unknown", "detail": "No start date stated",
         "negotiable": True, "how": "Ask the recruiter"},
    ]
    queue.append(payload)
    result = score_jobs.get_resume_score_from_ai("resume", JOB)
    assert [g.gate for g in result.hard_gates] == ["working_language", "availability"]
    assert result.hard_gates[0].result == "fail"


def test_scoring_one_job_no_longer_decides_the_interview_odds_downgrade(responses):
    """The P(interview) gate is relative to the run now (see
    apply_interview_odds_downgrade), so a single call must leave the model's
    recommendation alone — the old absolute 0.10 rule fired on every job ever
    scored and made apply_now unreachable."""
    queue, _, calls = responses
    payload = json.loads(json.dumps(VALID))
    payload["competitive_context"]["p_first_round_interview"] = {"as_is": 0.02, "after_fixes": 0.05}
    queue.append(payload)
    result = score_jobs.get_resume_score_from_ai("resume text", JOB)
    assert result.recommendation == "apply_now"
    assert result.competitive_context.p_first_round_interview.as_is == 0.02
