"""Cross-source identity (Step B.3).

The motivating case is real: one YPOG opening reached the database four times —
once from LinkedIn, three times from Arbeitsagentur under different reference
numbers — was applied to twice, and left a fourth copy in the queue.
"""
import pytest

from sources import dedup

LINKEDIN = {
    "job_id": "4123456789",
    "provider": "linkedin",
    "company": "YPOG",
    "job_title": "AI/ML Engineer – Generative AI",
    "location": "Berlin, Germany",
    "description": ("We are looking for an AI/ML engineer to build generative AI "
                    "features for our legal platform. Python, LLMs, retrieval "
                    "augmented generation, cloud deployment."),
    "job_url": "https://www.linkedin.com/jobs/view/4123456789/",
    "scraped_at": "2026-09-01T10:00:00+00:00",
    "resume_score": 29,
}
ARBEITSAGENTUR = {
    "job_id": "arbeitsagentur_10001-2003-S",
    "provider": "arbeitsagentur",
    "company": "YPOG GmbH",
    "job_title": "AI/ML Engineer (m/w/d) – Generative AI",
    "location": "Berlin, Berlin",
    "description": ("## Rahmendaten\n- Homeoffice: möglich\n\n---\n\nWe are looking "
                    "for an AI/ML engineer to build generative AI features for our "
                    "legal platform. Python, LLMs, retrieval augmented generation."),
    "job_url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-2003-S",
    "scraped_at": "2026-09-06T07:20:00+00:00",
    "resume_score": 58,
}
ATS = {
    "job_id": "ats_ypog_9911",
    "provider": "ats",
    "company": "YPOG",
    "job_title": "AI/ML Engineer",
    "location": "Berlin",
    "description": ("We are looking for an AI/ML engineer to build generative AI "
                    "features for our legal platform. Python, LLMs, retrieval "
                    "augmented generation, cloud deployment."),
    "job_url": "https://boards.greenhouse.io/ypog/jobs/9911",
    "scraped_at": "2026-09-06T08:00:00+00:00",
    "resume_score": None,
}


class TestNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("YPOG GmbH", "ypog"),
        ("eWolff GmbH & Co. KG", "ewolff"),
        ("Statista Q GmbH", "statista q"),
        ("Acme Inc.", "acme"),
        ("Müller & Söhne AG", "müller söhne"),
    ])
    def test_legal_suffixes_do_not_change_identity(self, raw, expected):
        assert dedup.normalize_company(raw) == expected

    @pytest.mark.parametrize("raw", [
        "Data Engineer (m/w/d)", "Data Engineer (f/m/x)", "Data Engineer (all genders)",
        "Data Engineer m/w/d", "(Junior) Data Engineer (m/w/d)", "Data Engineer",
    ])
    def test_gender_and_seniority_markers_are_noise(self, raw):
        assert dedup.normalize_title(raw) == "data engineer"

    def test_german_inclusive_suffixes_collapse(self):
        assert dedup.normalize_title("Entwickler*in") == dedup.normalize_title("Entwickler")
        assert dedup.normalize_title("KI-Engineer:in") == dedup.normalize_title("KI Engineer")

    def test_location_keeps_the_city_only(self):
        assert dedup.normalize_location("Berlin, Berlin") == "berlin"
        assert dedup.normalize_location("Bielefeld, Nordrhein Westfalen") == "bielefeld"
        assert dedup.normalize_location("33611 Bielefeld") == "bielefeld"

    def test_a_missing_value_is_empty_not_an_error(self):
        assert dedup.normalize_company(None) == ""
        assert dedup.normalize_title(None) == ""
        assert dedup.normalize_location(None) == ""


class TestIdentity:
    def test_the_same_job_from_three_sources_is_one_posting(self):
        groups = dedup.group_duplicates([LINKEDIN, ARBEITSAGENTUR, ATS])
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_different_roles_at_the_same_employer_stay_separate(self):
        other = dict(ARBEITSAGENTUR, job_id="x", job_title="Data Engineer (m/w/d)")
        assert dedup.group_duplicates([LINKEDIN, other]) == []

    def test_the_same_title_in_two_cities_is_two_openings(self):
        """A large employer really does post the same role in two places."""
        munich = dict(LINKEDIN, job_id="y", location="Munich, Germany",
                      description="Completely different text about warehouse logistics "
                                  "and forklift certification in southern Bavaria.")
        assert dedup.group_duplicates([LINKEDIN, munich]) == []

    def test_the_same_title_in_two_cities_merges_when_the_text_matches(self):
        """Remote roles get listed under whichever office the poster picked."""
        munich = dict(LINKEDIN, job_id="y", location="Munich, Germany")
        groups = dedup.group_duplicates([LINKEDIN, munich])
        assert len(groups) == 1

    def test_a_missing_company_is_never_an_identity(self):
        nameless = dict(LINKEDIN, job_id="z", company=None)
        also = dict(ARBEITSAGENTUR, job_id="w", company=None)
        assert dedup.group_duplicates([nameless, also]) == []

    def test_a_lone_posting_is_not_a_group(self):
        assert dedup.group_duplicates([LINKEDIN]) == []


class TestDescriptionSimilarity:
    def test_the_rahmendaten_header_does_not_break_the_match(self):
        score = dedup.description_similarity(LINKEDIN["description"],
                                             ARBEITSAGENTUR["description"])
        assert score >= dedup.DESCRIPTION_SIMILARITY_THRESHOLD

    def test_unrelated_text_scores_low(self):
        assert dedup.description_similarity(
            LINKEDIN["description"],
            "Wir suchen eine Pflegefachkraft für unsere Station in Hamburg.") < 0.1

    def test_empty_input_is_zero_not_a_crash(self):
        assert dedup.description_similarity("", "anything") == 0.0
        assert dedup.description_similarity(None, None) == 0.0


class TestSurvivor:
    def test_an_applied_row_always_survives(self):
        """Application state is the one thing a merge cannot reconstruct."""
        applied = dict(LINKEDIN, application_stage="applied")
        survivor = dedup.choose_survivor([applied, ARBEITSAGENTUR, ATS])
        assert survivor["job_id"] == applied["job_id"]

    def test_otherwise_the_row_nearest_the_employer_wins(self):
        survivor = dedup.choose_survivor([LINKEDIN, ARBEITSAGENTUR, ATS])
        assert survivor["provider"] == "ats"

    def test_arbeitsagentur_beats_linkedin(self):
        survivor = dedup.choose_survivor([LINKEDIN, ARBEITSAGENTUR])
        assert survivor["provider"] == "arbeitsagentur"

    def test_a_scored_row_beats_an_unscored_one_from_the_same_source(self):
        scored = dict(ARBEITSAGENTUR, job_id="scored", resume_score=58)
        unscored = dict(ARBEITSAGENTUR, job_id="unscored", resume_score=None)
        assert dedup.choose_survivor([unscored, scored])["job_id"] == "scored"

    def test_the_choice_does_not_depend_on_input_order(self):
        group = [LINKEDIN, ARBEITSAGENTUR, ATS]
        first = dedup.choose_survivor(group)["job_id"]
        assert dedup.choose_survivor(list(reversed(group)))["job_id"] == first


class TestMerge:
    def test_one_row_survives_and_the_rest_become_alt_sources(self):
        merged = dedup.merge_group([LINKEDIN, ARBEITSAGENTUR, ATS])

        assert merged["survivor"]["provider"] == "ats"
        assert len(merged["duplicates"]) == 2
        assert [alt["provider"] for alt in merged["alt_sources"]] == ["arbeitsagentur", "linkedin"]
        assert all(alt["job_url"] for alt in merged["alt_sources"])

    def test_the_apply_link_prefers_the_direct_employer_url(self):
        merged = dedup.merge_group([LINKEDIN, ARBEITSAGENTUR])
        # Survivor is the Arbeitsagentur row and already holds the better URL.
        assert merged["survivor"]["provider"] == "arbeitsagentur"
        assert merged["apply_url"] is None
        assert merged["alt_sources"][0]["provider"] == "linkedin"

    def test_an_applied_row_keeps_the_url_the_application_went_to(self):
        """Rewriting job_url on an applied row would falsify where she applied."""
        applied = dict(LINKEDIN, application_stage="applied")
        merged = dedup.merge_group([applied, ARBEITSAGENTUR])

        assert merged["survivor"]["job_id"] == applied["job_id"]
        assert merged["apply_url"] is None
        assert merged["survivor"]["job_url"].startswith("https://www.linkedin.com")
        # ...but the direct URL is still recorded rather than lost.
        assert any("arbeitsagentur.de" in alt["job_url"] for alt in merged["alt_sources"])

    def test_an_unapplied_survivor_gets_upgraded_to_the_direct_url(self):
        only_linkedin_survives = dict(LINKEDIN, resume_score=99)
        aggregator_only = dict(ARBEITSAGENTUR, provider="some_job_board",
                               job_url="https://aggregator.example/x")
        merged = dedup.merge_group([only_linkedin_survives, aggregator_only])
        # An unranked source sorts below LinkedIn, so no upgrade is available.
        assert merged["apply_url"] is None

        with_ats = dedup.merge_group([dict(LINKEDIN, application_stage=None), ATS])
        assert with_ats["survivor"]["provider"] == "ats"

    def test_duplicate_urls_are_not_recorded_twice(self):
        twin = dict(ARBEITSAGENTUR, job_id="other")
        merged = dedup.merge_group([ARBEITSAGENTUR, twin])
        assert len(merged["alt_sources"]) == 0   # same URL as the survivor


DELOITTE_TEXT = ("Als Consultant im Bereich Data Engineering baust du Datenplattformen "
                 "für unsere Kunden auf, entwickelst Pipelines mit Spark und Databricks "
                 "und berätst zu Architektur, Governance und Cloud-Migration.")


def _deloitte(job_id, title, city, text=DELOITTE_TEXT, **extra):
    return dict({"job_id": job_id, "provider": "linkedin", "company": "Deloitte",
                 "job_title": title, "location": f"{city}, Germany", "description": text,
                 "job_url": f"https://www.linkedin.com/jobs/view/{job_id}/",
                 "resume_score": 40}, **extra)


class TestOfficeInTheTitle:
    """One posting listed once per office, the office in the title. Deloitte alone
    had 16 such groups, 42 rows, in the queue at once - each scored separately."""

    @pytest.mark.parametrize("title, location", [
        ("Consultant Data Engineering (m/w/d) in Hamburg", "Hamburg, Germany"),
        ("Consultant Data Engineering (m/w/d) in Frankfurt (Main)", "Frankfurt, Hesse"),
        ("Consultant Data Engineering (m/w/d), Ismaning", "Ismaning, Bayern"),
        ("Consultant Data Engineering (m/w/d) am Standort Mannheim", "Mannheim"),
        ("Consultant Data Engineering - Berlin", "Berlin, Germany"),
        ("Consultant Data Engineering (Remote)", None),
        ("Consultant Data Engineering (m/w/d) in Berlin oder München", None),
        ("Consultant Data Engineering for Hamburg", None),
        ("Consultant Data Engineering", "Munich, Bavaria, Germany"),
    ])
    def test_the_office_is_not_part_of_the_role(self, title, location):
        assert dedup.title_key(title, location) == "consultant data engineering"

    def test_the_own_city_matches_in_its_other_language(self):
        assert dedup.title_key("Data Engineer in München", "Munich, Bavaria") == "data engineer"

    def test_a_city_inside_a_name_is_left_alone(self):
        assert dedup.title_key("Hamburg Commercial Bank Data Analyst") == \
            "hamburg commercial bank data analyst"

    def test_a_title_that_is_only_a_place_keeps_it(self):
        assert dedup.title_key("Berlin") == "berlin"

    def test_copies_in_different_cities_are_one_posting(self):
        jobs = [_deloitte("1", "Consultant Data Engineering (m/w/d) in Hamburg", "Hamburg"),
                _deloitte("2", "Consultant Data Engineering (m/w/d) in Stuttgart", "Stuttgart"),
                _deloitte("3", "Consultant Data Engineering (m/w/d)", "Berlin")]
        assert [len(g) for g in dedup.group_duplicates(jobs)] == [3]

    def test_the_scrape_time_key_matches_across_offices(self):
        assert (dedup.identity_key("Deloitte", "Consultant Data Engineering (m/w/d) in Hamburg",
                                   "Hamburg, Germany")
                == dedup.identity_key("Deloitte GmbH", "Consultant Data Engineering (m/w/d)",
                                      "Berlin, Germany"))

    def test_two_roles_sharing_boilerplate_stay_separate(self):
        """'Data Engineering' is a subset of 'Data Science & ML Engineering', and the
        firm's boilerplate put their texts at 0.67. Merging hid a real role."""
        other_text = DELOITTE_TEXT.replace("Data Engineering", "Data Science & ML Engineering") \
            + " Du trainierst Modelle, evaluierst sie und bringst sie in Produktion mit MLflow."
        assert 0.6 <= dedup.description_similarity(DELOITTE_TEXT, other_text) < 0.9
        jobs = [_deloitte("1", "Consultant Data Engineering (m/w/d) in Hamburg", "Hamburg"),
                _deloitte("2", "Consultant Data Science & ML Engineering (m/w/d) in Hamburg",
                          "Hamburg", text=other_text)]
        assert dedup.group_duplicates(jobs) == []


class TestCompanyNameVariants:
    @pytest.mark.parametrize("left, right", [
        ("RENK GmbH", "RENK Group"),
        ("Bertrandt AG", "Bertrandt Group"),
        ("NetCologne", "NetCologne Gesellschaft für Telekommunikation mbH"),
        ("AraCom", "AraCom-Gruppe"),
        ("Capgemini Deutschland GmbH", "Capgemini"),
        ("K+S Aktiengesellschaft", "K+S"),
    ])
    def test_one_employer_under_two_names(self, left, right):
        assert dedup.normalize_company(left) == dedup.normalize_company(right)

    def test_a_name_made_only_of_noise_keeps_it(self):
        assert dedup.normalize_company("Deutschland Group") == "deutschland group"


class TestWhichCopyIsKept:
    def test_the_best_scoring_city_wins_between_copies_on_one_board(self):
        group = [_deloitte("low", "Consultant Data Engineering in Hamburg", "Hamburg", resume_score=25),
                 _deloitte("high", "Consultant Data Engineering in Munich", "Munich", resume_score=52)]
        assert dedup.choose_survivor(group)["job_id"] == "high"

    def test_a_dismissed_copy_is_kept_over_an_untouched_one(self):
        group = [_deloitte("new", "Consultant Data Engineering", "Hamburg", resume_score=60),
                 _deloitte("dismissed", "Consultant Data Engineering", "Munich",
                           dismissed_at="2026-10-01T00:00:00+00:00")]
        assert dedup.choose_survivor(group)["job_id"] == "dismissed"

    def test_the_link_is_not_swapped_for_another_citys_copy(self):
        """Same board, so the kept row's own link is the right one for its city."""
        group = [_deloitte("low", "Consultant Data Engineering in Hamburg", "Hamburg", resume_score=25),
                 _deloitte("high", "Consultant Data Engineering in Munich", "Munich", resume_score=52)]
        assert dedup.merge_group(group)["apply_url"] is None


class TestBestApplyUrl:
    """job_url keeps meaning "the best posting page"; apply_url is the form. A merge
    keeps the form link from whichever copy has one, nearest the employer first."""

    @staticmethod
    def _row(provider, job_id, apply_url=None, **extra):
        return {"provider": provider, "job_id": job_id, "company": "Acme", "job_title": "Data Scientist",
                "job_url": f"https://{provider}.example/{job_id}", "apply_url": apply_url, **extra}

    def test_the_feed_link_survives_a_merge_into_an_applied_linkedin_row(self):
        linkedin = self._row("linkedin", "l1", status="applied", application_stage="applied")
        ats = self._row("ats", "a1", apply_url="https://jobs.lever.co/acme/1")
        merged = dedup.merge_group([linkedin, ats])
        assert merged["survivor"] is linkedin
        assert merged["best_apply_url"] == "https://jobs.lever.co/acme/1"

    def test_nearest_source_wins(self):
        xing = self._row("xing", "x1", apply_url="https://acme.de/jobs/1")
        ats = self._row("ats", "a1", apply_url="https://jobs.lever.co/acme/1")
        assert dedup.merge_group([xing, ats])["best_apply_url"] == "https://jobs.lever.co/acme/1"

    def test_no_copy_has_one(self):
        assert dedup.merge_group([self._row("linkedin", "l1"), self._row("stepstone", "s1")])["best_apply_url"] is None


class TestDedupExistingPayload:
    def test_the_survivor_gets_the_link_and_its_ats_when_it_had_none(self):
        from maintenance import dedup_existing
        survivor = {"job_id": "l1", "apply_url": None, "alt_sources": []}
        merged = {"survivor": survivor, "alt_sources": [], "apply_url": None,
                  "best_apply_url": "https://jobs.lever.co/acme/1"}
        payload = dedup_existing.survivor_payload(merged, has_apply_url=True)
        assert payload["apply_url"] == "https://jobs.lever.co/acme/1"
        assert payload["ats_type"] == "lever"

    def test_an_existing_link_is_left_alone(self):
        from maintenance import dedup_existing
        survivor = {"job_id": "a1", "apply_url": "https://jobs.ashbyhq.com/acme/1", "alt_sources": []}
        merged = {"survivor": survivor, "alt_sources": [], "apply_url": None,
                  "best_apply_url": "https://jobs.lever.co/acme/1"}
        assert "apply_url" not in dedup_existing.survivor_payload(merged, has_apply_url=True)

    def test_without_the_column_nothing_about_links_is_written(self):
        from maintenance import dedup_existing
        survivor = {"job_id": "l1", "alt_sources": []}
        merged = {"survivor": survivor, "alt_sources": [], "apply_url": None,
                  "best_apply_url": "https://jobs.lever.co/acme/1"}
        payload = dedup_existing.survivor_payload(merged, has_apply_url=False)
        assert "apply_url" not in payload and "ats_type" not in payload
