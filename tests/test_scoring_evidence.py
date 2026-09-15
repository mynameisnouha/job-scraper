"""Facts the CV does not state, put in front of the job scorer.

The rubric has always had a rule for tier-4 evidence — a must-have met there
costs half weight rather than full — and nothing could populate it, so every
interview answer scored as "not done". These tests cover what now fills it, and
the two things that must never happen: a lead reaching the scorer, and the
evidence being mistaken for the CV.
"""

import json

from scoring import score_jobs
from tailor.facts import SOURCE_INTERVIEW, SOURCE_SCORER, FactBase


def _base():
    base = FactBase()
    base.add(claim="Built a churn model in PySpark.", tier=2)
    base.add(claim="Scheduled the pipeline with Airflow.", tier=4,
             source=SOURCE_INTERVIEW, context="Daimler Buses, 2026", skills=["airflow"])
    base.add(claim="Kubernetes not shown on CV", tier=5,
             source=SOURCE_SCORER, confirmed=False, seen_in=["j1", "j2"])
    return base


class TestWhatGoesInTheBlock:
    def test_only_what_the_cv_does_not_already_say(self):
        """The scorer is reading the CV; repeating it back costs tokens and says nothing."""
        rendered = _base().render_for_scoring()
        assert "Airflow" in rendered
        assert "PySpark" not in rendered

    def test_a_lead_never_reaches_the_scorer(self):
        """The circularity that would otherwise close.

        A lead came FROM the scorer. Feed it back and the scorer reads its own
        guess as evidence, stops reporting the gap, and the score rises with
        nothing having become true.
        """
        assert "Kubernetes" not in _base().render_for_scoring()

    def test_an_empty_base_produces_no_block(self):
        assert FactBase().render_for_scoring() == ""
        assert FactBase().evidence_not_on_cv() == []

    def test_tier_five_is_never_evidence(self):
        base = FactBase()
        base.add(claim="Never used Kubernetes.", tier=5, source=SOURCE_INTERVIEW)
        assert base.render_for_scoring() == ""


class TestTheBlockInThePrompt:
    def _system_prompt(self, monkeypatch, evidence):
        captured = {}

        def fake(prompt, system_prompt, **kwargs):
            captured["system"] = system_prompt
            return json.dumps(BASE_PAYLOAD)

        monkeypatch.setattr(score_jobs, "EVIDENCE_NOT_ON_CV", evidence)
        monkeypatch.setattr(score_jobs.primary_client, "generate_content", fake)
        job = {"job_id": "j1", "job_title": "ML Engineer", "company": "ACME",
               "level": "Entry", "description": "Airflow, Databricks."}
        score_jobs.get_resume_score_from_ai("MY RESUME TEXT", job)
        return captured["system"]

    def test_the_evidence_is_outside_the_resume(self, monkeypatch):
        """Inside it, as_is odds and fixable_before_applying would both go wrong."""
        system = self._system_prompt(monkeypatch, "[f002] (tier 4) Scheduled with Airflow.")
        resume_block = system[system.index("--- RESUME ---"):system.index("--- END RESUME ---")]
        assert "MY RESUME TEXT" in resume_block
        assert "Airflow" not in resume_block
        assert "EVIDENCE NOT ON THE RESUME" in system
        assert system.index("Scheduled with Airflow") > system.index("--- END RESUME ---")

    def test_no_block_at_all_when_there_is_nothing_to_say(self, monkeypatch):
        """An empty base must not leave a heading promising evidence that isn't there.

        Checked after the resume rather than across the whole prompt: the rubric
        mentions the block by name to say what to do *if* one follows, and that
        sentence is correct whether or not there is anything to put in it.
        """
        system = self._system_prompt(monkeypatch, "")
        tail = system[system.index("--- END RESUME ---"):]
        assert "EVIDENCE NOT ON THE RESUME" not in tail

    def test_the_job_stays_out_of_the_cached_half(self, monkeypatch):
        """The block sits in the system prefix, which must not vary per job."""
        system = self._system_prompt(monkeypatch, "[f002] (tier 4) Scheduled with Airflow.")
        assert "Databricks" not in system, "the JD belongs in the user message"


class TestLoader:
    def test_the_env_var_wins_over_the_local_file(self, monkeypatch):
        """How CI supplies it, mirroring CANDIDATE_PROFILE_JSON."""
        payload = {"facts": [{"id": "f001", "claim": "Ran Airflow nightly.", "tier": 4,
                              "source": SOURCE_INTERVIEW}]}
        monkeypatch.setenv("TAILOR_FACTS_JSON", json.dumps(payload))
        assert "Ran Airflow nightly." in score_jobs._load_tailor_evidence()

    def test_a_missing_fact_base_is_not_an_error(self, monkeypatch, tmp_path):
        """Unlike the candidate profile: no fact base is the normal CI case."""
        monkeypatch.delenv("TAILOR_FACTS_JSON", raising=False)
        from tailor import settings as tailor_settings
        monkeypatch.setattr(tailor_settings, "FACTS_PATH", str(tmp_path / "nope.json"))
        assert score_jobs._load_tailor_evidence() == ""

    def test_a_corrupt_fact_base_does_not_stop_scoring(self, monkeypatch):
        monkeypatch.setenv("TAILOR_FACTS_JSON", "{not json")
        assert score_jobs._load_tailor_evidence() == ""


BASE_PAYLOAD = {
    "overall_score": 70, "skills_match_score": 70, "experience_score": 70,
    "education_score": 70, "language_fit": "Full match", "recommendation": "apply_now",
    "reasoning": "ok", "one_line_verdict": "ok",
    "dimension_scores": {"must_have_coverage": 70, "evidence_strength": 70,
                         "nice_to_have_coverage": 70, "seniority_fit": 70,
                         "environment_fit": 70, "domain_fit": 70, "differentiation": 70},
    "competitive_context": {"estimated_applicant_volume": "100-200",
                            "modal_competitor": "MSc grad",
                            "candidate_percentile": 60,
                            "p_first_round_interview": {"as_is": 0.2, "after_fixes": 0.3}},
}
