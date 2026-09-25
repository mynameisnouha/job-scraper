"""Market statistics: how much German the corpus really demands, and which skills.

The whole module turns on one judgement — reading a German-language ad that names
no level as C2 — so most of these tests pin the edges of exactly that rule.
"""

from review import market


def _bd(level, language="de"):
    return {"german_required": level, "jd_language": language}


class TestClassifyGerman:
    def test_a_german_ad_with_no_level_is_read_as_c2(self):
        """The rule this panel exists for."""
        assert market.classify_german(_bd("unstated")) == "c2_assumed"
        assert market.classify_german(_bd("unclear")) == "c2_assumed"
        assert market.classify_german(_bd("unstated", "mixed")) == "c2_assumed"

    def test_an_explicit_level_wins_over_the_ads_language(self):
        """An ad in German saying B2 asks for B2. Reading it as C2 contradicts it.

        This is the edge that keeps the assumption honest: it fills a silence, it
        does not overrule a statement.
        """
        assert market.classify_german(_bd("B2")) == "B2"
        assert market.classify_german(_bd("nice-to-have")) == "nice-to-have"
        assert market.classify_german(_bd("none")) == "none", \
            "a German-language ad that names English as the working language is open"

    def test_an_english_ad_with_no_level_stays_unknown(self):
        """Nothing to infer: no level stated and no German in the ad itself."""
        assert market.classify_german(_bd("unstated", "en")) == "unknown"
        assert market.classify_german({}) == "unknown"


class TestGermanDemand:
    def test_shares_are_over_the_whole_population(self):
        stats = market.german_demand([_bd("C1-fluent"), _bd("unstated"),
                                      _bd("none", "en"), _bd("unstated", "en")])
        by_key = {r["key"]: r for r in stats["rows"]}
        assert stats["total"] == 4
        assert by_key["C1-fluent"]["share"] == 0.25
        assert by_key["c2_assumed"]["share"] == 0.25
        assert sum(r["share"] for r in stats["rows"]) == 1.0

    def test_closed_counts_c1_and_the_assumption_but_not_b2(self):
        """B2 is a level to reach, not a wall. Lumping it in overstates the gate."""
        stats = market.german_demand([_bd("C1-fluent"), _bd("unstated"), _bd("B2"),
                                      _bd("nice-to-have"), _bd("none")])
        assert stats["closed"] == 2
        assert stats["open"] == 2, "a plus and none are both open today"

    def test_empty_buckets_are_kept(self):
        """A zero next to B1 says the scorer has no B1 band; a missing row says nothing."""
        stats = market.german_demand([_bd("C1-fluent")])
        assert [r["key"] for r in stats["rows"]] == [b["key"] for b in market.GERMAN_BUCKETS]
        assert {r["key"]: r["n"] for r in stats["rows"]}["B1"] == 0

    def test_no_postings_is_not_a_division_by_zero(self):
        stats = market.german_demand([])
        assert stats["total"] == 0 and stats["closed_share"] == 0.0

    def test_every_bucket_has_a_colour_from_the_apps_german_vocabulary(self):
        """Purple closes, neutral is reachable, blue is open — same as the job cards."""
        from review import theme as T

        fills = {b["key"]: b["fill"] for b in market.GERMAN_BUCKETS}
        assert fills["C1-fluent"] in T.PURPLE.values()
        assert fills["c2_assumed"] in T.PURPLE.values()
        assert fills["B2"] in T.NEUTRAL.values()
        assert fills["none"] in T.BLUE.values()


class TestTopSkills:
    SUMMARY = {"clusters": [
        {"size": 90, "skill_demand": [["python", 1.0], ["sql", 0.5], ["rag", 0.1]]},
        {"size": 10, "skill_demand": [["python", 0.5], ["sql", 1.0]]},
    ]}

    def test_demand_is_weighted_by_how_many_postings_each_archetype_holds(self):
        """A 10-posting cluster must not outvote a 90-posting one."""
        skills = {s["label"]: s["share"] for s in market.top_skills(self.SUMMARY)}
        assert skills["python"] == (1.0 * 90 + 0.5 * 10) / 100
        assert skills["sql"] == (0.5 * 90 + 1.0 * 10) / 100

    def test_ordered_by_demand_and_capped(self):
        skills = market.top_skills(self.SUMMARY, limit=2)
        assert [s["label"] for s in skills] == ["python", "sql"]

    def test_underscores_become_readable_labels(self):
        summary = {"clusters": [{"size": 1, "skill_demand": [["llm_apis", 1.0]]}]}
        assert market.top_skills(summary)[0]["label"] == "llm apis"

    def test_a_run_with_no_clusters_yields_nothing(self):
        assert market.top_skills({"clusters": []}) == []


class TestBreakdownParsing:
    def test_a_json_string_breakdown_is_parsed(self):
        row = {"score_breakdown": '{"german_required": "B2"}'}
        assert market.breakdown_of(row)["german_required"] == "B2"

    def test_junk_is_an_empty_breakdown_not_a_crash(self):
        assert market.breakdown_of({"score_breakdown": "{not json"}) == {}
        assert market.breakdown_of({}) == {}
