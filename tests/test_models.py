import json

import pytest
from pydantic import ValidationError

from models import (
    Resume, Education, Experience, Project, Certification, Links,
    SummaryOutput, SkillsOutput, ExperienceListOutput, SingleExperienceOutput,
    ProjectListOutput, SingleProjectOutput, ValidationResponse, ScoreBreakdown,
    CompetitiveContext, DimensionScores, InterviewProbability,
)

# The v2 fields that are now mandatory. Kept here so the older tests stay
# focused on what they were written to check.
REQUIRED_V2_FIELDS = {
    "dimension_scores": {
        "must_have_coverage": 70, "evidence_strength": 60, "nice_to_have_coverage": 50,
        "seniority_fit": 65, "environment_fit": 70, "domain_fit": 40, "differentiation": 55,
    },
    "competitive_context": {
        "estimated_applicant_volume": "150-300",
        "modal_competitor": "MSc CS with 2 years industry ML",
        "candidate_percentile": 60,
        "p_first_round_interview": {"as_is": 0.2, "after_fixes": 0.3},
    },
    "one_line_verdict": "Worth applying once the CV names the LLM work.",
}


class TestScoreBreakdownRequiredFields:
    """
    Regression: these fields used to be optional-with-default, so the JSON
    schema never marked them required and ~1 in 3 scored jobs came back without
    competitive_context — leaving nothing to calibrate against.
    """

    def test_schema_marks_them_required(self):
        required = ScoreBreakdown.model_json_schema().get("required", [])
        assert "competitive_context" in required
        assert "dimension_scores" in required
        assert "one_line_verdict" in required

    def test_breakdown_without_competitive_context_is_rejected(self):
        fields = dict(REQUIRED_V2_FIELDS)
        fields.pop("competitive_context")
        with pytest.raises(ValidationError):
            ScoreBreakdown(
                overall_score=70, skills_match_score=70, experience_score=70,
                education_score=70, language_fit="Full match", recommendation="apply_now",
                reasoning="…", **fields,
            )

    def test_interview_probability_requires_both_estimates(self):
        with pytest.raises(ValidationError):
            InterviewProbability(as_is=0.2)

    def test_competitive_context_nests_the_probability(self):
        cc = CompetitiveContext(**REQUIRED_V2_FIELDS["competitive_context"])
        assert cc.p_first_round_interview.after_fixes == 0.3

    def test_every_dimension_must_be_scored(self):
        partial = dict(REQUIRED_V2_FIELDS["dimension_scores"])
        partial.pop("differentiation")
        with pytest.raises(ValidationError):
            DimensionScores(**partial)


class TestInterviewProbabilityNormalization:
    """The model emits both 0.15 and 15 for '15%' — coerce, don't lose the score."""

    def test_percentages_are_converted(self):
        p = InterviewProbability(as_is=15, after_fixes=25)
        assert p.as_is == 0.15
        assert p.after_fixes == 0.25

    def test_fractions_pass_through(self):
        p = InterviewProbability(as_is=0.15, after_fixes=0.25)
        assert p.as_is == 0.15

    def test_out_of_range_is_clamped(self):
        assert InterviewProbability(as_is=250, after_fixes=-4).as_is == 1.0
        assert InterviewProbability(as_is=250, after_fixes=-4).after_fixes == 0.0

    def test_zero_stays_zero(self):
        assert InterviewProbability(as_is=0, after_fixes=0).as_is == 0.0


class TestModels:
    def test_resume_defaults(self):
        r = Resume()
        assert r.name == ""
        assert r.email == ""
        assert r.skills == []
        assert r.education == []
        assert r.experience == []
        assert r.projects == []
        assert r.certifications == []
        assert r.languages == []

    def test_resume_with_data(self):
        r = Resume(
            name="John Doe",
            email="john@example.com",
            skills=["Python", "Docker"],
            education=[Education(degree="BSc", institution="MIT")],
            experience=[Experience(job_title="Engineer", company="Acme")],
        )
        assert r.name == "John Doe"
        assert len(r.skills) == 2
        assert r.experience[0].job_title == "Engineer"
        assert r.education[0].institution == "MIT"

    def test_education_defaults(self):
        e = Education()
        assert e.degree == ""
        assert e.field_of_study == ""
        assert e.institution == ""

    def test_experience_defaults(self):
        e = Experience()
        assert e.job_title == ""
        assert e.company == ""
        assert e.description == ""

    def test_project_defaults(self):
        p = Project()
        assert p.name == ""
        assert p.technologies == []

    def test_certification_defaults(self):
        c = Certification()
        assert c.name == ""
        assert c.issuer == ""

    def test_links_defaults(self):
        l = Links()
        assert l.linkedin == ""
        assert l.github == ""
        assert l.portfolio == ""

    def test_summary_output(self):
        s = SummaryOutput(summary="Test summary")
        assert s.summary == "Test summary"

    def test_skills_output(self):
        s = SkillsOutput(skills=["Python", "Java"])
        assert len(s.skills) == 2

    def test_validation_response(self):
        v = ValidationResponse(is_valid=True, reason="OK")
        assert v.is_valid is True
        assert v.reason == "OK"

    def test_score_breakdown_defaults(self):
        s = ScoreBreakdown(
            overall_score=75,
            skills_match_score=80,
            experience_score=70,
            education_score=85,
            language_fit="Full match",
            recommendation="apply",
            reasoning="Strong skills match with some experience gaps.",
            **REQUIRED_V2_FIELDS,
        )
        assert s.overall_score == 75
        assert s.skills_match_score == 80
        assert s.experience_score == 70
        assert s.key_matching_skills == []
        assert s.key_gaps == []
        assert s.recommendation == "apply"

    def test_score_breakdown_full(self):
        s = ScoreBreakdown(
            overall_score=90,
            skills_match_score=95,
            experience_score=80,
            education_score=90,
            language_fit="Full match",
            key_matching_skills=["Python", "ML", "SQL"],
            key_gaps=["Kubernetes"],
            recommendation="strong_apply",
            reasoning="Excellent fit for data science role.",
            **REQUIRED_V2_FIELDS,
        )
        assert len(s.key_matching_skills) == 3
        assert s.recommendation == "strong_apply"

    def test_score_breakdown_serialization(self):
        s = ScoreBreakdown(
            overall_score=60,
            skills_match_score=50,
            experience_score=60,
            education_score=70,
            language_fit="Partial - B1 German may suffice",
            key_matching_skills=["Python"],
            key_gaps=["Deep Learning"],
            recommendation="consider",
            reasoning="Some relevant skills but missing key DL experience.",
            **REQUIRED_V2_FIELDS,
        )
        data = json.loads(s.model_dump_json())
        assert data["overall_score"] == 60
        assert data["recommendation"] == "consider"
        assert "Python" in data["key_matching_skills"]
        # Nested models must serialize to the same JSON shape the DB and
        # calibration.py already read.
        assert data["competitive_context"]["p_first_round_interview"]["as_is"] == 0.2
