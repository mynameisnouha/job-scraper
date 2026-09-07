"""Application packs (Step C.1).

The value here is the sixty seconds not spent hunting for the same five answers,
so the tests are mostly about what happens when a piece is missing: a pack with
four of five parts is still worth having, provided it says which part is absent.
"""
import json
import os
from datetime import date

import pytest

from review import application_pack

JOB = {
    "job_id": "arbeitsagentur_123-S",
    "company": "Statista Q GmbH",
    "job_title": "(Junior) Data Engineer (m/w/d)",
    "job_url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/123-S",
    "why_me_pitch": "I build data pipelines that other people rely on.",
    "score_breakdown": {
        "salary_band": "65.000 – 95.000 EUR (jahresgehalt)",
        "fixable_before_applying": [
            {"gap": "dbt not on CV", "fix": "Add the dbt project to the skills line",
             "effort_minutes": 20},
        ],
        "key_gaps": ["No Kafka in production"],
    },
}

ANSWERS = {
    "gehaltsvorstellung": "55.000 – 65.000 EUR",
    "eintrittstermin": "ab 01.03.2027",
    "kuendigungsfrist": "keine",
    "aufenthaltstitel": "Aufenthaltserlaubnis, kein Sponsoring nötig",
    "relocation": "umzugsbereit innerhalb Deutschlands",
    "extra": {"Referenzen": "auf Anfrage"},
}


class TestCvRouting:
    ROUTES = [("data engineer", "cv_engineering.pdf"), ("data", "cv_data_science.pdf")]

    def test_the_first_matching_keyword_wins(self):
        chosen = application_pack.choose_cv("(Junior) Data Engineer (m/w/d)",
                                            routes=self.ROUTES, library_dir="cv")
        assert chosen == os.path.join("cv", "cv_engineering.pdf")

    def test_a_later_keyword_matches_when_the_first_does_not(self):
        chosen = application_pack.choose_cv("Data Scientist", routes=self.ROUTES, library_dir="cv")
        assert chosen == os.path.join("cv", "cv_data_science.pdf")

    def test_matching_is_case_insensitive(self):
        assert application_pack.choose_cv("SENIOR DATA ENGINEER", routes=self.ROUTES,
                                          library_dir="cv") is not None

    def test_no_match_and_no_default_returns_nothing(self):
        """Sending the wrong CV is worse than being asked to pick one."""
        assert application_pack.choose_cv("Pflegefachkraft", routes=self.ROUTES,
                                          library_dir="cv", default=None) is None

    def test_the_default_is_used_when_set(self):
        chosen = application_pack.choose_cv("Pflegefachkraft", routes=self.ROUTES,
                                            library_dir="cv", default="cv_general.pdf")
        assert chosen == os.path.join("cv", "cv_general.pdf")

    def test_an_empty_route_list_is_not_an_error(self):
        assert application_pack.choose_cv("anything", routes=[], library_dir="cv") is None


class TestStatedSalary:
    def test_a_band_with_figures_is_surfaced(self):
        assert application_pack.stated_salary(JOB["score_breakdown"]) is not None

    def test_the_scorers_prose_placeholder_is_not_a_band(self):
        """salary_band carries 'Not stated — cannot assess...' when there is no figure."""
        assert application_pack.stated_salary(
            {"salary_band": "Not stated — cannot assess Blue Card threshold"}) is None

    def test_a_missing_band_is_none(self):
        assert application_pack.stated_salary({}) is None


class TestAnswersDocument:
    def test_every_standing_answer_is_rendered(self):
        text = application_pack.build_answers_md(JOB, JOB["score_breakdown"], ANSWERS)
        assert "Gehaltsvorstellung" in text and "55.000 – 65.000 EUR" in text
        assert "Eintrittstermin" in text and "ab 01.03.2027" in text
        assert "Kündigungsfrist" in text
        assert "Aufenthaltstitel" in text
        assert "Umzugsbereitschaft" in text

    def test_a_stated_band_is_shown_against_the_standing_figure(self):
        text = application_pack.build_answers_md(JOB, JOB["score_breakdown"], ANSWERS)
        assert "This posting states a salary band" in text
        assert "65.000 – 95.000 EUR" in text

    def test_no_band_means_no_section(self):
        text = application_pack.build_answers_md(JOB, {}, ANSWERS)
        assert "This posting states a salary band" not in text

    def test_extras_are_included(self):
        text = application_pack.build_answers_md(JOB, {}, ANSWERS)
        assert "Referenzen" in text and "auf Anfrage" in text

    def test_missing_answers_produce_a_template_that_says_so(self):
        text = application_pack.build_answers_md(JOB, {}, {})
        assert "No `application_answers.json` found" in text
        assert "_not set_" in text


class TestChecklist:
    def test_fixable_items_become_tickable_tasks(self):
        text = application_pack.build_checklist_md(JOB, JOB["score_breakdown"])
        assert "- [ ] dbt not on CV → Add the dbt project" in text

    def test_known_gaps_are_listed_separately_from_tasks(self):
        text = application_pack.build_checklist_md(JOB, JOB["score_breakdown"])
        assert "be ready to answer" in text
        assert "No Kafka in production" in text

    def test_a_job_with_no_fixables_still_gets_a_checklist(self):
        text = application_pack.build_checklist_md(JOB, {})
        assert "No CV-fixable gaps" in text
        assert "Marked applied in the dashboard" in text


class TestBuildPack:
    def test_the_folder_is_named_by_company_and_date(self, tmp_path):
        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS,
                                             today=date(2026, 9, 6))
        assert result["path"].endswith(os.path.join("Statista_Q_GmbH_2026-09-06"))

    def test_every_part_is_written(self, tmp_path):
        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        assert set(result["files"]) >= {"answers.md", "checklist.md", "why_me.txt"}
        written = os.listdir(result["path"])
        assert "answers.md" in written and "why_me.txt" in written

    def test_a_missing_pitch_is_a_warning_not_a_failure(self, tmp_path):
        job = dict(JOB, why_me_pitch=None)
        result = application_pack.build_pack(job, root=str(tmp_path), answers=ANSWERS)

        assert "why_me.txt" not in result["files"]
        assert any("pitch" in w for w in result["warnings"])
        assert "answers.md" in result["files"]      # the rest still landed

    def test_an_unroutable_cv_is_a_warning_not_a_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(application_pack.config, "CV_KEYWORD_ROUTES", [])
        monkeypatch.setattr(application_pack.config, "CV_DEFAULT", None)
        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        assert any("No CV route matched" in w for w in result["warnings"])

    def test_a_routed_cv_is_copied_in(self, tmp_path, monkeypatch):
        library = tmp_path / "cv"
        library.mkdir()
        (library / "cv_engineering.pdf").write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(application_pack.config, "CV_LIBRARY_DIR", str(library))
        monkeypatch.setattr(application_pack.config, "CV_KEYWORD_ROUTES",
                            [("data engineer", "cv_engineering.pdf")])

        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        assert "cv_engineering.pdf" in result["files"]
        assert (tmp_path / result["path"].split(os.sep)[-1] / "cv_engineering.pdf").exists() or \
               os.path.exists(os.path.join(result["path"], "cv_engineering.pdf"))

    def test_a_routed_but_absent_cv_names_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(application_pack.config, "CV_LIBRARY_DIR", str(tmp_path / "nope"))
        monkeypatch.setattr(application_pack.config, "CV_KEYWORD_ROUTES",
                            [("data engineer", "cv_engineering.pdf")])
        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        assert any("cv_engineering.pdf" in w and "missing" in w for w in result["warnings"])

    def test_rebuilding_overwrites_rather_than_failing(self, tmp_path):
        application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        again = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        assert "answers.md" in again["files"]


class TestLoadAnswers:
    def test_a_missing_file_is_empty_not_an_error(self, tmp_path):
        assert application_pack.load_answers(str(tmp_path / "absent.json")) == {}

    def test_malformed_json_is_empty_not_an_error(self, tmp_path):
        path = tmp_path / "answers.json"
        path.write_text("{not json", encoding="utf-8")
        assert application_pack.load_answers(str(path)) == {}

    def test_a_real_file_is_read(self, tmp_path):
        path = tmp_path / "answers.json"
        path.write_text(json.dumps(ANSWERS), encoding="utf-8")
        assert application_pack.load_answers(str(path))["eintrittstermin"] == "ab 01.03.2027"

    def test_the_shipped_example_is_valid_json_and_covers_every_field(self):
        """The example is the only instruction anyone gets — it must parse."""
        with open("application_answers.json.example", encoding="utf-8") as handle:
            example = json.load(handle)
        for key, _ in application_pack.ANSWER_FIELDS:
            assert key in example, f"{key} missing from the example file"


class TestPlaceholderDetection:
    """An unedited example value reads as a filled-in field at a glance. Three of
    them survived the first real edit of the answers file, one untouched entirely."""

    @pytest.mark.parametrize("value", [
        "<e.g. keine — derzeit nicht in ungekündigter Festanstellung>",
        "55.000 EUR>",                       # trailing bracket left behind
        "<your permit status",
        "e.g. Vollzeit",
        "",
        "   ",
        None,
    ])
    def test_example_text_is_detected(self, value):
        assert application_pack.is_placeholder(value) is True

    @pytest.mark.parametrize("value", [
        "55.000 – 65.000 EUR brutto/Jahr, verhandelbar je nach Aufgabenumfang",
        "ab 01.03.2027; für Teilzeit/Werkstudent früher verfügbar",
        "keine",
    ])
    def test_a_real_answer_is_not_flagged(self, value):
        assert application_pack.is_placeholder(value) is False

    def test_unfilled_answers_are_listed_by_label(self):
        answers = dict(ANSWERS, kuendigungsfrist="<e.g. keine>")
        unfilled = application_pack.unfilled_answers(answers)
        assert any("Kündigungsfrist" in item for item in unfilled)
        assert not any("Gehaltsvorstellung" in item for item in unfilled)

    def test_placeholder_extras_are_listed_too(self):
        answers = dict(ANSWERS, extra={"Referenzen": "<available on request>"})
        assert application_pack.unfilled_answers(answers) == ["extra: Referenzen"]

    def test_the_document_shouts_rather_than_rendering_it_as_an_answer(self):
        answers = dict(ANSWERS, kuendigungsfrist="<e.g. keine>")
        text = application_pack.build_answers_md(JOB, {}, answers)
        assert "STILL THE EXAMPLE TEXT — do not send" in text

    def test_the_pack_warns_about_them(self, tmp_path):
        answers = dict(ANSWERS, kuendigungsfrist="<e.g. keine>")
        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=answers)
        assert any("Still example text" in w for w in result["warnings"])

    def test_a_fully_filled_file_produces_no_such_warning(self, tmp_path):
        result = application_pack.build_pack(JOB, root=str(tmp_path), answers=ANSWERS)
        assert not any("example text" in w for w in result["warnings"])
