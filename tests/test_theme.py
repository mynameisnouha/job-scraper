"""The HTML builders behind the redesigned UI — plain functions, no Streamlit."""
from review import theme as T


class TestBands:
    def test_bands_follow_the_observed_distribution_not_a_school_scale(self):
        assert T.band(72)["band"] == "Strong"
        assert T.band(65)["band"] == "Strong"
        assert T.band(52)["band"] == "Worth it"
        assert T.band(40)["band"] == "Marginal"
        assert T.band(20)["band"] == "Long shot"
        assert T.band(None)["band"] == "Unscored"

    def test_corpus_rank_is_top_share_of_everything_scored(self):
        scores = [82, 72, 68, 62, 58, 52] + [30] * 94
        assert T.corpus_rank(72, scores) == "top 2% of 100"
        assert T.corpus_rank(30, scores) == "top 7% of 100"
        assert T.corpus_rank(None, scores) == ""
        assert T.corpus_rank(72, []) == ""


class TestGermanGate:
    def test_named_levels_get_their_own_chip(self):
        assert T.german_gate({"german_required": "B2"})["label"] == "B2"
        assert "hard gate" in T.german_gate({"german_required": "C1-fluent"})["label"]
        assert T.german_gate({"german_required": "none"})["label"] == "No German"

    def test_unstated_in_a_german_ad_is_flagged_not_treated_as_none(self):
        gate = T.german_gate({"german_required": "unstated", "jd_language": "de"})
        assert gate["label"] == "Unstated · ad in German"
        assert gate["border"] != "transparent"   # outlined: a question, not a fact

    def test_empty_breakdown_still_renders(self):
        assert T.german_gate({})["label"] == "German not stated"
        assert "German not stated" in T.gate_chip({})


class TestEscaping:
    def test_scraped_text_never_lands_unescaped(self):
        html = T.title_block("<script>alert(1)</script>", "Co & Sons", None)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "Co &amp; Sons" in html

    def test_urls_are_escaped_inside_the_link(self):
        html = T.title_block("Job", "Co", None, url='https://x.test/?a=1&b="2"')
        assert 'href="https://x.test/?a=1&amp;b=&quot;2&quot;"' in html


class TestComposites:
    def test_score_disc_carries_number_band_and_rank(self):
        html = T.score_disc(72, [72, 50, 30])
        assert ">72<" in html
        assert "Strong" in html
        assert "top 33% of 3" in html

    def test_fact_chips_put_german_first_and_reword_the_rest(self):
        html = T.fact_chips({"german_required": "B2"},
                            [("Interview odds", "34%"), ("Effort", "2.5h"),
                             ("Source", "Recruiting agency"), ("German", "B2")])
        assert html.index("B2") < html.index("34% interview odds")
        assert "2.5h" in html
        assert html.count("B2") == 1   # the German fact is not repeated as a plain chip

    def test_stat_tiles_render_every_tile(self):
        html = T.stat_tiles([("Sent", "38", "12 recent"), ("Open", "21", "")])
        assert "Sent" in html and ">38<" in html and "12 recent" in html
        assert html.count("border-radius:16px") == 2

    def test_timeline_marks_done_current_and_pending_differently(self):
        html = T.timeline_html([
            {"label": "Applied", "date": "6 Sep", "state": "done"},
            {"label": "Interview 1", "date": "pending", "state": "pending"},
        ])
        assert "Applied" in html and "Interview 1" in html
        assert T.BLUE[600] in html       # done dot
        assert "background:transparent" in html   # pending dot

    def test_bucket_bars_handle_empty_buckets(self):
        html = T.bucket_bars([{"label": "<50", "n": 0, "rate": None},
                              {"label": "50-64", "n": 4, "rate": 0.5}])
        assert "—" in html and "50%" in html

    def test_step_bar_states(self):
        html = T.step_bar([{"n": "1", "label": "Fill", "note": "", "state": "done"},
                           {"n": "2", "label": "Write", "note": "here", "state": "current"},
                           {"n": "3", "label": "Send", "note": "", "state": "todo"}])
        assert "✓" in html
        assert T.ACCENT in html
        assert ">3<" in html

    def test_pulse_card_survives_a_failed_read(self):
        assert "Not reachable" in T.pulse_card(None, "")
        html = T.pulse_card({"new": 14, "cleared": 3, "sources": ["a", "b"]}, "09:12 · 3h ago")
        assert "14 new postings" in html and "2 sources reporting" in html
