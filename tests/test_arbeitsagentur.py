"""Bundesagentur für Arbeit adapter, against recorded fixture responses.

The fixtures are real payloads captured from the live API on 2026-09-06 (trimmed
to two search results and a shorter description). No test here touches the network.
"""
import base64
import json
import pathlib

import pytest
import requests

import dedup
import scrape_guard
from scrapers import arbeitsagentur


def _key(company, title):
    """The company/title key exactly as the pipeline builds it."""
    return dedup.normalize_company(company), dedup.normalize_title(title)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SEARCH = json.loads((FIXTURES / "arbeitsagentur_search.json").read_text(encoding="utf-8"))
DETAIL = json.loads((FIXTURES / "arbeitsagentur_detail.json").read_text(encoding="utf-8"))
OFFER = SEARCH["ergebnisliste"][0]


def _response(status_code=200, payload=None, text=""):
    response = requests.Response()
    response.status_code = status_code
    body = json.dumps(payload, ensure_ascii=False) if payload is not None else text
    response._content = body.encode("utf-8")
    response.url = "https://rest.arbeitsagentur.de/"
    return response


class TestSearch:
    def test_offers_come_from_ergebnisliste(self, monkeypatch):
        """Not `stellenangebote` — the live v6 payload nests results under
        `ergebnisliste`, and reading the wrong key yields a silent zero."""
        monkeypatch.setattr(arbeitsagentur.requests, "get",
                            lambda *a, **k: _response(payload=SEARCH))
        offers = arbeitsagentur.search_jobs("Data Scientist")
        assert len(offers) == 2
        assert offers[0]["referenznummer"] == "14225-9e7ac7a2671f1076-S"

    def test_staffing_firms_are_excluded_at_the_source(self, monkeypatch):
        sent = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            sent.update(params or {})
            return _response(payload=SEARCH)

        monkeypatch.setattr(arbeitsagentur.requests, "get", fake_get)
        arbeitsagentur.search_jobs("Data Scientist")

        assert sent["zeitarbeit"] == "false"
        assert sent["was"] == "Data Scientist"
        assert sent["veroeffentlichtseit"] == 1

    def test_the_api_key_header_is_sent(self, monkeypatch):
        sent = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            sent.update(headers or {})
            return _response(payload=SEARCH)

        monkeypatch.setattr(arbeitsagentur.requests, "get", fake_get)
        arbeitsagentur.search_jobs("Data Scientist")
        assert sent["X-API-Key"] == "jobboerse-jobsuche"

    def test_a_block_is_recorded_with_status_and_body_size(self, monkeypatch):
        monkeypatch.setattr(arbeitsagentur.requests, "get",
                            lambda *a, **k: _response(status_code=403, text="denied"))
        outcome = scrape_guard.SourceOutcome("arbeitsagentur")

        assert arbeitsagentur.search_jobs("Data Scientist", outcome=outcome) == []
        attempt = outcome.attempts[0]
        assert attempt.status_code == 403
        assert attempt.body_bytes == len("denied")
        assert outcome.status == scrape_guard.BROKEN

    def test_a_network_error_returns_empty_rather_than_raising(self, monkeypatch):
        def boom(*a, **k):
            raise requests.exceptions.ConnectionError("no route to host")

        monkeypatch.setattr(arbeitsagentur.requests, "get", boom)
        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        assert arbeitsagentur.search_jobs("Data Scientist", outcome=outcome) == []
        assert outcome.attempts[0].status_code is None

    def test_non_json_is_survivable(self, monkeypatch):
        monkeypatch.setattr(arbeitsagentur.requests, "get",
                            lambda *a, **k: _response(text="<html>maintenance</html>"))
        assert arbeitsagentur.search_jobs("Data Scientist") == []


class TestDetail:
    def test_the_reference_number_is_base64_encoded_into_the_path(self, monkeypatch):
        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen["url"] = url
            return _response(payload=DETAIL)

        monkeypatch.setattr(arbeitsagentur.requests, "get", fake_get)
        arbeitsagentur.fetch_job_detail("14225-9e7ac7a2671f1076-S")

        encoded = base64.b64encode(b"14225-9e7ac7a2671f1076-S").decode()
        assert seen["url"].endswith(encoded)
        assert "/pc/v4/jobdetails/" in seen["url"]   # v5/v6 return 403

    def test_a_failed_detail_is_none_not_an_exception(self, monkeypatch):
        monkeypatch.setattr(arbeitsagentur.requests, "get",
                            lambda *a, **k: _response(status_code=404, text="nope"))
        assert arbeitsagentur.fetch_job_detail("whatever") is None


class TestNormalize:
    def test_produces_the_shared_record_shape(self):
        record = arbeitsagentur.normalize(OFFER, DETAIL)

        assert record["job_id"] == "arbeitsagentur_14225-9e7ac7a2671f1076-S"
        assert record["provider"] == "arbeitsagentur"
        assert record["company"] == "eWolff GmbH"
        assert record["job_url"] == ("https://www.arbeitsagentur.de/jobsuche/jobdetail/"
                                     "14225-9e7ac7a2671f1076-S")
        assert record["posted_at"] == "2026-09-04"
        assert record["location"].startswith("Bielefeld")
        assert record["level"] is None

    def test_umlauts_survive(self):
        record = arbeitsagentur.normalize(OFFER, DETAIL)
        assert "Johanneswerkstraße" not in record["description"]  # street isn't in the text
        assert "Geschäftsmodelle" in record["description"]

    def test_salary_and_home_office_reach_the_description(self):
        """The jobs table has no salary or remote column, so these facts go where
        the scorer already reads them from."""
        record = arbeitsagentur.normalize(OFFER, DETAIL)

        assert "Rahmendaten" in record["description"]
        assert "65.000" in record["description"] and "95.000" in record["description"]
        assert "Homeoffice: möglich" in record["description"]
        # ...and the actual posting text is still there, after the header.
        assert "Wir entwickeln starke Marken" in record["description"]

    def test_a_posting_without_a_description_is_dropped(self):
        assert arbeitsagentur.normalize(OFFER, {"stellenangebotsBeschreibung": "   "}) is None
        assert arbeitsagentur.normalize(OFFER, None) is None

    def test_a_posting_without_a_reference_number_is_dropped(self):
        assert arbeitsagentur.normalize({"stellenangebotsTitel": "x"}, DETAIL) is None

    def test_no_facts_means_no_empty_header(self):
        bare_offer = {"referenznummer": "abc", "stellenangebotsTitel": "Dev"}
        record = arbeitsagentur.normalize(bare_offer, {"stellenangebotsBeschreibung": "Text."})
        assert record["description"] == "Text."


class TestProcessQuery:
    @pytest.fixture
    def wired(self, monkeypatch):
        monkeypatch.setattr(arbeitsagentur, "_delay", lambda: None)
        monkeypatch.setattr(arbeitsagentur, "search_jobs",
                            lambda *a, **k: list(SEARCH["ergebnisliste"]))
        # A distinct employer per reference number: feeding one identical detail for
        # every posting would (correctly) trip the repost check and collapse them.
        employers = {o["referenznummer"]: f"Employer {i}"
                     for i, o in enumerate(SEARCH["ergebnisliste"])}
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail",
                            lambda refnr: dict(DETAIL, firma=employers.get(refnr, "Other GmbH")))
        import supabase_utils
        monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                            lambda: (set(), set()))

    def test_records_are_returned_and_counters_partition(self, wired):
        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data Scientist", outcome=outcome)
        outcome.record_query(new=len(records))

        assert len(records) == 2
        assert outcome.fetched == 2
        assert outcome.already_in_db + outcome.filtered_total + outcome.new == outcome.fetched
        assert outcome.unaccounted == 0

    def test_jobs_already_stored_are_counted_not_refetched(self, wired, monkeypatch):
        import supabase_utils
        monkeypatch.setattr(
            supabase_utils, "get_existing_jobs_from_supabase",
            lambda: ({"arbeitsagentur_14225-9e7ac7a2671f1076-S"}, set()))

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data Scientist", outcome=outcome)
        outcome.record_query(new=len(records))

        assert outcome.already_in_db == 1
        assert len(records) == 1
        assert outcome.unaccounted == 0

    def test_training_places_are_filtered(self, wired, monkeypatch):
        offers = [dict(o, stellenangebotsart="AUSBILDUNG")
                  for o in SEARCH["ergebnisliste"]]
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: offers)

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data Scientist", outcome=outcome)
        outcome.record_query(new=len(records))

        assert records == []
        assert outcome.filtered_out["not_a_regular_job"] == 2
        assert outcome.unaccounted == 0

    def test_internships_are_filtered_by_title(self, wired, monkeypatch):
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail",
                            lambda refnr: dict(DETAIL, stellenangebotsTitel="Praktikum Data Science"))

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data Scientist", outcome=outcome)
        outcome.record_query(new=len(records))

        assert records == []
        assert outcome.filtered_out["internship"] == 2

    def test_the_per_query_limit_is_attributed(self, wired):
        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data Scientist", limit=1, outcome=outcome)
        outcome.record_query(new=len(records))

        assert len(records) == 1
        assert outcome.filtered_out["over_per_query_limit"] == 1
        assert outcome.unaccounted == 0

    def test_a_repeated_reference_number_is_only_counted_once(self, wired, monkeypatch):
        """Same refnr twice in one response: deduped before it reaches `fetched`."""
        doubled = SEARCH["ergebnisliste"] + [dict(SEARCH["ergebnisliste"][0])]
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: doubled)

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data Scientist", outcome=outcome)

        assert outcome.fetched == 2
        assert len(records) == 2

    def test_an_empty_search_yields_nothing_and_does_not_crash(self, wired, monkeypatch):
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: [])
        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        assert arbeitsagentur.process_query("Data Scientist", outcome=outcome) == []
        assert outcome.status == scrape_guard.BROKEN


class TestRepostDedup:
    """Arbeitsagentur relists the same role under a fresh referenznummer, so
    reference-number dedup alone lets duplicates through — one YPOG opening reached
    the database three times that way, and two of the copies were applied to."""

    @pytest.fixture
    def wired(self, monkeypatch):
        monkeypatch.setattr(arbeitsagentur, "_delay", lambda: None)
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail", lambda refnr: dict(DETAIL))
        return None

    def _relisted(self):
        """The same posting under two different reference numbers."""
        first = dict(OFFER, referenznummer="ref-1", firma="YPOG",
                     stellenangebotsTitel="AI/ML Engineer (m/w/d)")
        second = dict(OFFER, referenznummer="ref-2", firma="YPOG",
                      stellenangebotsTitel="AI/ML Engineer (m/w/d)")
        return [first, second]

    def test_a_relisting_inside_one_run_is_collapsed(self, wired, monkeypatch):
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: self._relisted())
        import supabase_utils
        monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                            lambda: (set(), set()))
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail",
                            lambda refnr: dict(DETAIL, firma="YPOG",
                                               stellenangebotsTitel="AI/ML Engineer (m/w/d)"))

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("AI Engineer", outcome=outcome)
        outcome.record_query(new=len(records))

        assert len(records) == 1
        assert outcome.filtered_out["repost_same_company_title"] == 1
        assert outcome.unaccounted == 0

    def test_a_role_already_in_the_database_is_not_refetched(self, wired, monkeypatch):
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: self._relisted()[:1])
        import supabase_utils
        monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                            lambda: (set(), {_key("YPOG", "AI/ML Engineer (m/w/d)")}))
        fetched = []
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail",
                            lambda refnr: fetched.append(refnr) or dict(DETAIL))

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("AI Engineer", outcome=outcome)

        assert records == []
        assert fetched == [], "the repost check must run before the detail request"
        assert outcome.filtered_out["repost_same_company_title"] == 1

    def test_a_title_that_only_differs_in_the_detail_payload_is_still_caught(self, wired, monkeypatch):
        """The search and detail payloads disagree on the title for some postings."""
        offer = dict(OFFER, referenznummer="ref-9", firma="YPOG",
                     stellenangebotsTitel="Some Other Title")
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: [offer])
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail",
                            lambda refnr: dict(DETAIL, firma="YPOG",
                                               stellenangebotsTitel="AI/ML Engineer (m/w/d)"))
        import supabase_utils
        monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                            lambda: (set(), {_key("YPOG", "AI/ML Engineer (m/w/d)")}))

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        assert arbeitsagentur.process_query("AI Engineer", outcome=outcome) == []
        assert outcome.filtered_out["repost_same_company_title"] == 1

    def test_different_roles_at_the_same_employer_both_survive(self, wired, monkeypatch):
        offers = [dict(OFFER, referenznummer="r1", firma="SAP", stellenangebotsTitel="Data Scientist"),
                  dict(OFFER, referenznummer="r2", firma="SAP", stellenangebotsTitel="Data Engineer")]
        monkeypatch.setattr(arbeitsagentur, "search_jobs", lambda *a, **k: offers)
        titles = {"r1": "Data Scientist", "r2": "Data Engineer"}
        monkeypatch.setattr(arbeitsagentur, "fetch_job_detail",
                            lambda refnr: dict(DETAIL, firma="SAP",
                                               stellenangebotsTitel=titles[refnr]))
        import supabase_utils
        monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                            lambda: (set(), set()))

        outcome = scrape_guard.SourceOutcome("arbeitsagentur")
        records = arbeitsagentur.process_query("Data", outcome=outcome)
        assert len(records) == 2
