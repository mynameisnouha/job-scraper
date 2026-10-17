"""StepStone source, against pages captured 2026-10-17 and trimmed.

stepstone_search.html keeps three result cards; stepstone_job.html keeps the
JSON-LD JobPosting block of the second card's posting plus the apply control,
which must never be followed: on StepStone it can be a one-click submission.
"""
import inspect
import pathlib

import pytest
import requests

from sources import dedup, scrape_guard, stepstone

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SEARCH = (FIXTURES / "stepstone_search.html").read_bytes()
JOB = (FIXTURES / "stepstone_job.html").read_bytes()
CHALLENGE = (FIXTURES / "stepstone_challenge.html").read_bytes()


def _response(body: bytes, status_code=200):
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    response.encoding = "ISO-8859-1"
    response.url = "https://www.stepstone.de/"
    return response


def test_search_url_uses_the_slug_newest_first_and_last_24_hours():
    url = stepstone.search_url("Machine Learning Engineer")
    assert url.startswith("https://www.stepstone.de/jobs/machine-learning-engineer?")
    assert "sort=2" in url and "ag=age_1" in url


def test_search_parses_every_card(monkeypatch):
    monkeypatch.setattr(stepstone.requests, "get", lambda *a, **k: _response(SEARCH))
    outcome = scrape_guard.SourceOutcome("stepstone")
    cards = stepstone.search("data scientist", outcome=outcome)
    assert [c["id"] for c in cards] == ["14640906", "14570361", "14569268"]
    first = cards[0]
    assert first["title"] == "Data Scientist Inventur und Bestandsführung (w/m/d)"
    assert first["company"] == "meinestadt.de"
    assert first["location"] == "Karlsruhe"
    assert first["url"].startswith("https://www.stepstone.de/stellenangebote--")
    assert first["url"].endswith("--14640906-inline.html")
    assert first["posted_at"]
    assert outcome.attempts[0].items_parsed == 3


def test_a_challenge_page_parses_nothing_and_says_so(monkeypatch):
    """A 200 with no cards is how a bot wall looks; items_parsed=0 makes it visible."""
    monkeypatch.setattr(stepstone.requests, "get", lambda *a, **k: _response(CHALLENGE))
    outcome = scrape_guard.SourceOutcome("stepstone")
    assert stepstone.search("data scientist", outcome=outcome) == []
    assert outcome.attempts[0].status_code == 200
    assert outcome.attempts[0].items_parsed == 0


def test_normalize_builds_the_shared_record_and_never_an_apply_url():
    card = {"id": "14570361", "url": "https://www.stepstone.de/stellenangebote--x--14570361-inline.html",
            "title": "t", "company": "c", "location": "Karlsruhe", "posted_at": None}
    record = stepstone.normalize(card, JOB.decode("utf-8"))
    assert record["job_id"] == "stepstone_14570361"
    assert record["provider"] == "stepstone"
    assert record["job_title"] == "Senior Data Scientist - Machine Learning / KI (m/w/d)"
    assert record["company"] == "Knuddels GmbH & Co. KG"
    assert record["location"] == "Karlsruhe"
    assert record["posted_at"].startswith("2026-10-17")
    assert record["description"] and "&amp;" not in record["description"]
    assert "apply_url" not in record and "ats_type" not in record


def test_a_page_without_a_job_posting_block_normalizes_to_none():
    card = {"id": "1", "url": "https://www.stepstone.de/x--1-inline.html", "title": "t",
            "company": "c", "location": "Berlin", "posted_at": None}
    assert stepstone.normalize(card, "<html></html>") is None


def test_the_module_never_touches_the_apply_control():
    source = inspect.getsource(stepstone)
    assert "harmonised-apply" not in source
    assert "/apply" not in source


@pytest.fixture
def pipeline(monkeypatch):
    state = {"existing_ids": set(), "existing_keys": set()}
    from db import supabase_utils
    monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                        lambda: (state["existing_ids"], state["existing_keys"]))
    monkeypatch.setattr(stepstone, "_delay", lambda: None)
    return state


def _serve(monkeypatch, job_status=200):
    def fake_get(url, *a, **k):
        if "/jobs/" in url:
            return _response(SEARCH)
        return _response(JOB, job_status)
    monkeypatch.setattr(stepstone.requests, "get", fake_get)


def test_process_query_accounts_for_every_card(monkeypatch, pipeline):
    pipeline["existing_ids"] = {"stepstone_14640906"}
    pipeline["existing_keys"] = {dedup.identity_key(
        "Knuddels GmbH & Co. KG", "Senior Data Scientist - Machine Learning / KI (m/w/d)", "Karlsruhe")}
    _serve(monkeypatch)
    outcome = scrape_guard.SourceOutcome("stepstone")
    records = stepstone.process_query("data scientist", outcome=outcome)
    outcome.record_query(new=len(records))
    assert outcome.fetched == 3
    assert outcome.already_in_db == 1
    # Two cards reach the detail page; the fixture page is Knuddels', a known repost.
    assert outcome.filtered_out == {"repost_same_company_title": 2}
    assert outcome.unaccounted == 0


def test_process_query_detail_failure(monkeypatch, pipeline):
    _serve(monkeypatch, job_status=403)
    outcome = scrape_guard.SourceOutcome("stepstone")
    assert stepstone.process_query("data scientist", outcome=outcome) == []
    assert outcome.filtered_out == {"detail_fetch_failed": 3}


def test_runner_is_registered():
    from sources import scraper
    import config
    assert "stepstone" in scraper.SOURCE_RUNNERS
    assert "stepstone" in config.SCRAPING_SOURCES
