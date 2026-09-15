"""Harvesting the scorer's observations, and keeping them out of the CV.

The second half matters more than the first. A lead is the scorer's inference
about an absence in a CV, not something the candidate said, so the tests that
earn their keep here are the ones asserting a lead cannot be cited, cannot be
rendered to the writer, and cannot reach a generated document.
"""

from tailor import harvest
from tailor.documents import Application, CoverLetter, Line, TailoredCV
from tailor.facts import SOURCE_SCORER, FactBase
from tailor.verify import verify


def _row(job_id, *gaps):
    return {"job_id": job_id,
            "score_breakdown": {"fixable_before_applying":
                                [{"gap": g, "fix": "name it on the CV"} for g in gaps]}}


def _base_with(*claims):
    base = FactBase()
    for claim in claims:
        base.add(claim=claim, skills=["python"])
    return base


class TestHarvest:
    def test_a_recurring_gap_is_one_lead_with_a_count(self):
        """Phrasing drifts between runs; the gap does not."""
        base = _base_with("Built a churn model in PySpark.")
        harvest.harvest(base, [
            _row("j1", "Kubernetes not shown on CV"),
            _row("j2", "no Kubernetes experience shown"),
            _row("j3", "Kubernetes missing"),
        ])
        leads = base.unconfirmed()
        assert len(leads) == 1
        assert len(leads[0].seen_in) == 3, "each posting counts once"

    def test_different_levels_of_the_same_thing_stay_apart(self):
        """'German B2' and 'German C1' are different questions with different answers."""
        base = _base_with("Studied data science in Stuttgart.")
        harvest.harvest(base, [_row("j1", "German B2 not evidenced"),
                               _row("j2", "German C1 not evidenced")])
        assert len(base.unconfirmed()) == 2

    def test_rerunning_over_the_corpus_counts_nothing_twice(self):
        base = _base_with("Built a churn model in PySpark.")
        rows = [_row("j1", "Airflow not shown"), _row("j2", "Airflow not shown")]
        harvest.harvest(base, rows)
        harvest.harvest(base, rows)
        assert [len(f.seen_in) for f in base.unconfirmed()] == [2]

    def test_a_gap_the_base_already_answers_is_not_asked_again(self):
        """The base holds every interview answer ever given; the CV does not."""
        base = _base_with("Scheduled the nightly Databricks pipeline with Airflow.")
        counts = harvest.harvest(base, [_row("j1", "Airflow not shown on CV")])
        assert base.unconfirmed() == []
        assert counts["skipped"] == 1

    def test_the_panel_has_a_ceiling(self, monkeypatch):
        monkeypatch.setattr(harvest.settings, "MAX_SCORER_LEADS", 2)
        base = _base_with("Built a churn model in PySpark.")
        harvest.harvest(base, [_row("j1", "Kafka missing", "Terraform missing", "Scala missing")])
        assert len(base.unconfirmed()) == 2

    def test_only_fixable_gaps_are_harvested(self):
        """Structural gaps have no answer, so asking about them is noise."""
        assert harvest.observations({
            "structural_gaps": ["needs 5 years, candidate has 2"],
            "key_gaps": ["Kubernetes"],
            "fixable_before_applying": [{"gap": "Airflow not shown", "fix": "add it"}],
        }) == [("Airflow not shown", "add it")]


class TestLeadsAreNotFacts:
    def _lead_base(self):
        base = FactBase()
        base.add(claim="Built a churn model in PySpark.")
        base.add(claim="Airflow not shown on CV", tier=5,
                 source=SOURCE_SCORER, confirmed=False, seen_in=["j1"])
        return base

    def test_the_writer_never_sees_a_lead(self):
        rendered = self._lead_base().render()
        assert "PySpark" in rendered
        assert "Airflow" not in rendered

    def test_a_line_citing_a_lead_fails_verification(self):
        """The check that stops the scorer's guess reaching an employer."""
        base = self._lead_base()
        app = Application(
            cv=TailoredCV(headline="ML Engineer",
                          summary=Line(text="Scheduled pipelines with Airflow.",
                                       fact_ids=["f002"])),
            cover_letter=CoverLetter(subject="x", greeting="Hallo,", closing="Gruesse"),
        )
        problems = verify(app, base).problems
        assert [p.kind for p in problems] == ["unknown_fact"]

    def test_answering_a_lead_makes_it_citable(self):
        base = self._lead_base()
        base.confirm("f002", your_words="I ran it nightly at Daimler.")
        assert "f002" in base.ids()
        assert base.by_id("f002").tier == 4, "true, but not written on the CV"

    def test_a_lead_can_be_dropped_but_a_fact_cannot(self):
        """The base-only-grows rule is about facts. A wrong lead is not one."""
        base = self._lead_base()
        assert base.drop("f002") is True
        assert base.drop("f001") is False, "a confirmed fact is never dropped"
