from score_jobs import format_resume_to_text


class TestFormatResume:
    def test_empty_resume(self):
        result = format_resume_to_text(None)
        assert result == "Resume data is not available."

        result = format_resume_to_text({})
        assert result == "Resume data is not available."

    def test_basic_info(self):
        resume = {
            "name": "Alice",
            "email": "alice@test.com",
            "phone": "123-456-7890",
            "location": "Berlin",
        }
        result = format_resume_to_text(resume)
        assert "Name: Alice" in result
        assert "Email: alice@test.com" in result
        assert "Phone: 123-456-7890" in result
        assert "Location: Berlin" in result

    def test_skills(self):
        resume = {"skills": ["Python", "Docker", "Kubernetes"]}
        result = format_resume_to_text(resume)
        assert "Python" in result
        assert "Docker" in result
        assert "Kubernetes" in result

    def test_experience(self):
        resume = {
            "experience": [{
                "job_title": "Engineer",
                "company": "TechCo",
                "start_date": "2020",
                "end_date": "2023",
                "description": "Built stuff",
            }]
        }
        result = format_resume_to_text(resume)
        assert "Engineer" in result
        assert "TechCo" in result
        assert "2020" in result
        assert "2023" in result

    def test_education(self):
        resume = {
            "education": [{
                "degree": "MSc",
                "field_of_study": "CS",
                "institution": "Uni",
                "start_year": "2018",
                "end_year": "2020",
            }]
        }
        result = format_resume_to_text(resume)
        assert "MSc" in result
        assert "CS" in result
        assert "Uni" in result

    def test_projects(self):
        resume = {
            "projects": [{
                "name": "MyApp",
                "description": "Cool app",
                "technologies": ["React", "Node"],
            }]
        }
        result = format_resume_to_text(resume)
        assert "MyApp" in result
        assert "Cool app" in result
        assert "React" in result
        assert "Node" in result

    def test_certifications(self):
        resume = {
            "certifications": [{
                "name": "AWS Certified",
                "issuer": "Amazon",
                "year": "2023",
            }]
        }
        result = format_resume_to_text(resume)
        assert "AWS Certified" in result
        assert "Amazon" in result
        assert "2023" in result

    def test_languages(self):
        resume = {"languages": ["English", "German"]}
        result = format_resume_to_text(resume)
        assert "English" in result
        assert "German" in result


# --- Batch-relative P(interview) gate (step 0.1) ------------------------------

import json

import pytest

import config
import score_jobs
from models import ScoreBreakdown

BASE = {
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
        "p_first_round_interview": {"as_is": 0.05, "after_fixes": 0.09},
    },
    "one_line_verdict": "Worth applying.",
}


def make_breakdown(p_after, p_as_is=None, recommendation="apply_now", cap_applied=None):
    data = json.loads(json.dumps(BASE))
    data["recommendation"] = recommendation
    data["competitive_context"]["p_first_round_interview"] = {
        "as_is": p_as_is if p_as_is is not None else p_after,
        "after_fixes": p_after,
    }
    breakdown = ScoreBreakdown(**data)
    breakdown.calibration_check["cap_applied"] = cap_applied
    return breakdown


class TestInterviewOddsDowngrade:
    def test_bottom_of_the_batch_is_downgraded(self):
        # p_after_fixes = 0.01 .. 0.12. The 40th percentile of that (linear
        # interpolation, the numpy default) is 0.054, so the five weakest are
        # downgraded and the seven strongest survive — under the old absolute
        # 0.10 rule, all twelve would have been killed.
        batch = [make_breakdown(round(0.01 * i, 2)) for i in range(1, 13)]
        changed = score_jobs.apply_interview_odds_downgrade(batch)

        assert changed == [0, 1, 2, 3, 4]
        assert all(b.recommendation == "skip" for b in batch[:5])
        assert all(b.recommendation == "apply_now" for b in batch[5:])

    def test_raw_probabilities_are_left_untouched(self):
        batch = [make_breakdown(round(0.01 * i, 2)) for i in range(1, 13)]
        score_jobs.apply_interview_odds_downgrade(batch)

        p = batch[0].competitive_context.p_first_round_interview
        assert p.as_is == 0.01 and p.after_fixes == 0.01
        assert batch[0].calibration_check["p_interview_downgrade"]["from"] == "apply_now"

    def test_capped_jobs_downgrade_to_gate_negotiable(self):
        batch = [make_breakdown(round(0.01 * i, 2)) for i in range(1, 13)]
        batch[0].calibration_check["cap_applied"] = 55
        score_jobs.apply_interview_odds_downgrade(batch)

        assert batch[0].recommendation == "apply_if_gate_negotiable"
        assert batch[1].recommendation == "skip"

    def test_already_negative_recommendations_are_not_touched(self):
        batch = [make_breakdown(round(0.01 * i, 2)) for i in range(1, 13)]
        batch[0].recommendation = "apply_if_gate_negotiable"
        batch[1].recommendation = "skip"
        changed = score_jobs.apply_interview_odds_downgrade(batch)

        assert changed == [2, 3, 4]

    def test_small_batch_falls_back_to_the_absolute_threshold(self):
        # Five jobs is below P_INTERVIEW_MIN_BATCH, so percentiles are noise and
        # the old absolute rule on p_as_is applies instead.
        batch = [
            make_breakdown(0.30, p_as_is=0.05),
            make_breakdown(0.40, p_as_is=0.09),
            make_breakdown(0.50, p_as_is=0.10),
            make_breakdown(0.60, p_as_is=0.25),
            make_breakdown(0.70, p_as_is=0.40),
        ]
        changed = score_jobs.apply_interview_odds_downgrade(batch)

        assert changed == [0, 1]
        assert [b.recommendation for b in batch] == [
            "skip", "skip", "apply_now", "apply_now", "apply_now",
        ]
        assert batch[0].calibration_check["p_interview_downgrade"]["rule"] == "absolute"

    def test_empty_batch_is_a_no_op(self):
        assert score_jobs.apply_interview_odds_downgrade([]) == []


class TestPercentile:
    def test_matches_linear_interpolation(self):
        values = [0.0, 1.0, 2.0, 3.0, 4.0]
        assert score_jobs._percentile(values, 0) == 0.0
        assert score_jobs._percentile(values, 100) == 4.0
        assert score_jobs._percentile(values, 50) == 2.0
        assert score_jobs._percentile(values, 40) == pytest.approx(1.6)

    def test_single_value(self):
        assert score_jobs._percentile([0.42], 40) == 0.42


class TestCapApplied:
    """cap_applied must always be overwritten: the model states its own claimed cap,
    and on uncapped jobs that claim used to survive into the stored breakdown."""

    def _score(self, monkeypatch, payload):
        monkeypatch.setattr(
            score_jobs.primary_client, "generate_content",
            lambda *a, **k: json.dumps(payload),
        )
        job = {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME",
               "level": "Entry", "description": "Build models."}
        return score_jobs.get_resume_score_from_ai("resume text", job)

    def test_none_when_no_cap_fires(self, monkeypatch):
        payload = json.loads(json.dumps(BASE))
        payload["german_required"] = "none"
        payload["calibration_check"] = {"cap_applied": "55 (startup, thin evidence)"}
        breakdown = self._score(monkeypatch, payload)

        assert breakdown.calibration_check["cap_applied"] is None

    def test_our_cap_wins_when_one_fires(self, monkeypatch):
        payload = json.loads(json.dumps(BASE))
        payload["german_required"] = "C1-fluent"
        payload["calibration_check"] = {"cap_applied": "no cap"}
        breakdown = self._score(monkeypatch, payload)

        assert breakdown.calibration_check["cap_applied"] == 55
        assert breakdown.overall_score <= 55


class TestTailoringQueueLog:
    def test_counts_jobs_above_the_threshold(self, monkeypatch):
        monkeypatch.setattr(config, "RESUME_CUSTOMIZATION_MIN_SCORE", 55)
        batch = [make_breakdown(0.2) for _ in range(3)]
        batch[0].overall_score = 54
        batch[1].overall_score = 55
        batch[2].overall_score = 80

        assert score_jobs.log_tailoring_queue_size(batch) == 2
