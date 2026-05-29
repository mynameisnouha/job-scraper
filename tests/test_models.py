from models import (
    Resume, Education, Experience, Project, Certification, Links,
    SummaryOutput, SkillsOutput, ExperienceListOutput, SingleExperienceOutput,
    ProjectListOutput, SingleProjectOutput, ValidationResponse
)


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
