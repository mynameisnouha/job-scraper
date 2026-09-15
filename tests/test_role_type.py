"""Graduate / trainee programme classification, and everything that reads it."""
import pytest

from sources import role_type
from sources import scraper
from review import apply_queue
from scoring import notify


class TestTitleClassifier:
    @pytest.mark.parametrize("title", [
        "Trainee Data Science & Management (m/w/d)",
        "Traineeprogramm Quantitative Models and Data Analytics (m/w/d)",
        "Trainee Fokus Data Science (m/w/d) - Vorstandsbereich Deutschland",
        "Analytics - Future Leaders Graduate Program",
        "AGGP2027 – Graduate Program in Quality Department",
        "Graduate Programme Data & AI",
        "Absolventenprogramm Data & AI",
        "Nachwuchskräfteprogramm IT (m/w/d)",
        "AI Residency Program",
        "Machine Learning Residency",
        "Rotational Program - Data",
    ])
    def test_programmes_are_recognised(self, title):
        assert role_type.program_type_of(title) == role_type.GRADUATE_PROGRAM

    @pytest.mark.parametrize("title", [
        "Junior Data Scientist",
        "Data Scientist (m/w/d)",
        "Trainer Machine Learning",           # not "trainee"
        "International Data Engineer",
        "Graduate Data Scientist",            # a graduate-level role, not a programme
        "",
        None,
    ])
    def test_standard_roles_are_not(self, title):
        assert role_type.program_type_of(title) is None

    def test_a_praktikum_inside_a_programme_is_still_a_praktikum(self):
        """The Arbeitsagentur PRAKTIKUM_TRAINEE category mixes both; the internship
        check runs first and wins."""
        title = "Praktikum im Traineeprogramm Data (m/w/d)"
        assert scraper.is_internship_role(title)
        assert role_type.program_type_of(title) == role_type.GRADUATE_PROGRAM

    @pytest.mark.parametrize("title", [
        "DUALES STUDIUM - DATA SCIENCE / KI (M/W/D)",
        "Dual Student Data Engineering",
        "Ausbildung Fachinformatiker Daten- und Prozessanalyse",
    ])
    def test_study_placements_count_as_internships(self, title):
        assert scraper.is_internship_role(title)


class TestStoredJobs:
    def test_the_column_wins(self):
        assert role_type.is_program({"job_title": "Data Scientist",
                                     "program_type": "graduate_program"})

    def test_the_screen_verdict_is_read_from_the_breakdown(self):
        """A title like 'Analytics Associate' hides the programme; the screen
        model saw the cohort and intake and wrote it into the breakdown."""
        assert role_type.is_program({"job_title": "Analytics Associate",
                                     "score_breakdown": {"program_type": "graduate_program"}})

    def test_rows_written_before_the_column_fall_back_to_the_title(self):
        assert role_type.is_program({"job_title": "Trainee Data Analytics (m/w/d)"})
        assert not role_type.is_program({"job_title": "Data Analyst (m/w/d)"})


class TestQueueFilter:
    program = {"job_title": "Trainee Data Science", "program_type": "graduate_program"}
    role = {"job_title": "Data Scientist", "program_type": None}

    def test_programs_only(self):
        assert apply_queue.matches_role_type(self.program, "programs")
        assert not apply_queue.matches_role_type(self.role, "programs")

    def test_roles_only(self):
        assert apply_queue.matches_role_type(self.role, "roles")
        assert not apply_queue.matches_role_type(self.program, "roles")

    def test_all(self):
        assert apply_queue.matches_role_type(self.role, "all")
        assert apply_queue.matches_role_type(self.program, "all")

    def test_every_label_has_a_key(self):
        assert set(apply_queue.ROLE_TYPES) == set(apply_queue.ROLE_TYPE_LABELS)


class TestAlertMail:
    def test_programmes_are_tagged_with_their_intake(self):
        jobs = [{"job_id": "1", "job_title": "Trainee Data Science (m/w/d)", "company": "Openbank",
                 "resume_score": 78, "job_url": "https://x",
                 "score_breakdown": {"program_type": "graduate_program",
                                     "program_intake": "April 2027"}},
                {"job_id": "2", "job_title": "ML Engineer", "company": "Beta",
                 "resume_score": 72, "score_breakdown": {}}]
        _, text, body_html = notify.build_message(jobs, min_score=70)
        assert "Trainee Data Science (m/w/d) [Programme, intake April 2027] — Openbank" in text
        assert "ML Engineer — Beta" in text
        assert "[Programme, intake April 2027]" in body_html

    def test_no_intake_no_clause(self):
        jobs = [{"job_id": "1", "job_title": "Trainee Data", "company": "A",
                 "resume_score": 80, "score_breakdown": {"program_type": "graduate_program"}}]
        _, text, _ = notify.build_message(jobs, min_score=70)
        assert "Trainee Data [Programme] — A" in text
