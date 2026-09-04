import job_view

FULL = {
    "one_line_verdict": "Strong LLM evidence, undercut by A2 German.",
    "reasoning": "A much longer explanation that should not be used when a verdict exists.",
    "differentiators": ["Tier-1 LLM fine-tuning (QLoRA)"],
    "key_matching_skills": ["Python", "PySpark", "Tier-1 LLM fine-tuning (QLoRA)"],
    "disqualifier_matches": ["Requires EU passport"],
    "structural_gaps": ["German A2 cannot reach B2 before deadline"],
    "key_gaps": ["Kubernetes"],
    "fixable_before_applying": [
        {"gap": "GCP not evidenced", "fix": "Add a bullet naming the GCP services used",
         "effort_minutes": 15},
    ],
    "competitive_context": {"p_first_round_interview": {"as_is": 0.03, "after_fixes": 0.11}},
    "application_effort_hours": 1.5,
    "german_required": "C1-fluent",
    "salary_band": "€55,000-65,000",
    "is_agency_or_staffing_firm": True,
}


class TestSummary:
    def test_prefers_the_verdict(self):
        assert job_view.summary(FULL) == "Strong LLM evidence, undercut by A2 German."

    def test_falls_back_to_reasoning(self):
        assert job_view.summary({"reasoning": "Only reasoning here."}) == "Only reasoning here."

    def test_long_reasoning_is_truncated(self):
        out = job_view.summary({"reasoning": "x" * 400})
        assert len(out) <= 240 and out.endswith("…")

    def test_empty_breakdown(self):
        assert job_view.summary({}) == ""
        assert job_view.summary(None) == ""


class TestPros:
    def test_differentiators_come_first(self):
        assert job_view.pros(FULL)[0].startswith("Tier-1 LLM fine-tuning")

    def test_duplicates_removed(self):
        # The QLoRA item appears in both source lists.
        assert len(job_view.pros(FULL)) == 3

    def test_capped(self):
        many = {"key_matching_skills": [f"skill{i}" for i in range(20)]}
        assert len(job_view.pros(many)) == job_view.MAX_ITEMS

    def test_empty(self):
        assert job_view.pros({}) == []


class TestCons:
    def test_ordered_by_severity(self):
        """Disqualifiers first, then structural gaps, then ordinary gaps."""
        assert job_view.cons(FULL) == [
            "Requires EU passport",
            "German A2 cannot reach B2 before deadline",
            "Kubernetes",
        ]

    def test_empty(self):
        assert job_view.cons({}) == []


class TestQuickWins:
    def test_rendered_as_gap_then_fix(self):
        assert job_view.quick_wins(FULL) == [
            "GCP not evidenced → Add a bullet naming the GCP services used"
        ]

    def test_handles_plain_strings(self):
        assert job_view.quick_wins({"fixable_before_applying": ["Just do it"]}) == ["Just do it"]

    def test_handles_missing_fix(self):
        out = job_view.quick_wins({"fixable_before_applying": [{"gap": "No tests shown"}]})
        assert out == ["No tests shown"]

    def test_empty(self):
        assert job_view.quick_wins({}) == []


class TestInterviewOdds:
    def test_prefers_after_fixes(self):
        assert job_view.interview_odds(FULL) == 0.11

    def test_percentage_normalized(self):
        b = {"competitive_context": {"p_first_round_interview": {"after_fixes": 25}}}
        assert job_view.interview_odds(b) == 0.25

    def test_scalar_probability(self):
        assert job_view.interview_odds(
            {"competitive_context": {"p_first_round_interview": 0.4}}) == 0.4

    def test_missing_returns_none(self):
        assert job_view.interview_odds({}) is None
        assert job_view.interview_odds({"competitive_context": {}}) is None


class TestQuickFacts:
    def test_includes_established_facts(self):
        facts = dict(job_view.quick_facts(FULL))
        assert facts["Interview odds"] == "11%"
        assert facts["Effort"] == "1.5h"
        assert facts["German"] == "C1-fluent"
        assert facts["Salary"] == "€55,000-65,000"
        assert facts["Source"] == "Recruiting agency"

    def test_salary_prose_without_a_number_is_dropped(self):
        """The scorer writes 'Not stated — cannot assess…' — that isn't a fact."""
        facts = dict(job_view.quick_facts(
            {"salary_band": "Not stated — cannot assess Blue Card threshold"}))
        assert "Salary" not in facts

    def test_unknown_german_requirement_omitted(self):
        assert "German" not in dict(job_view.quick_facts({"german_required": "unclear"}))
        assert "German" not in dict(job_view.quick_facts({"german_required": "none"}))

    def test_empty_breakdown_yields_no_facts(self):
        assert job_view.quick_facts({}) == []


class TestShorten:
    def test_leaves_short_text_alone(self):
        assert job_view.shorten("Short phrase") == "Short phrase"

    def test_cuts_at_a_word_boundary(self):
        out = job_view.shorten("word " * 40)
        assert out.endswith("…")
        assert len(out) <= job_view.MAX_ITEM_CHARS + 1

    def test_collapses_whitespace(self):
        assert job_view.shorten("a\n\n  b") == "a b"
