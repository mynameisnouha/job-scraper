"""Xing source, against pages captured 2026-10-17 and trimmed.

xing_search.html keeps four result cards: two native Xing postings (Düsseldorf and
Wien) and two "Externes Job-Angebot" partner ads that link off to an aggregator.
xing_job.html keeps the JSON-LD JobPosting block and two applyUrl blobs from the
page's state: another job's first, then this job's own, which is the trap a
"first applyUrl on the page" parser falls into.
"""
import pathlib

import pytest
import requests

from sources import dedup, scrape_guard, xing

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SEARCH = (FIXTURES / "xing_search.html").read_bytes()
JOB = (FIXTURES / "xing_job.html").read_bytes()
OWN_APPLY = "https://jobs.smartrecruiters.com/DeutscheGlasfaser/744000153151708"


def _response(body: bytes, status_code=200):
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    response.encoding = "ISO-8859-1"  # what requests guesses for Xing; must be ignored
    response.url = "https://www.xing.com/"
    return response


def test_search_keeps_native_postings_and_skips_partner_ads(monkeypatch):
    monkeypatch.setattr(xing.requests, "get", lambda *a, **k: _response(SEARCH))
    outcome = scrape_guard.SourceOutcome("xing")
    cards = xing.search("data scientist", outcome=outcome)
    assert [c["id"] for c in cards] == ["158289963", "158613580"]
    first = cards[0]
    assert first["title"] == "Data and Analytics Engineer (w/m/d)"
    assert first["company"] == "Deutsche Glasfaser"
    assert first["location"] == "Düsseldorf"
    assert first["url"] == "https://www.xing.com/jobs/duesseldorf-data-and-analytics-engineer-158289963"
    assert outcome.attempts[0].status_code == 200


def test_a_blocked_search_records_the_status(monkeypatch):
    monkeypatch.setattr(xing.requests, "get", lambda *a, **k: _response(b"denied", 403))
    outcome = scrape_guard.SourceOutcome("xing")
    assert xing.search("data scientist", outcome=outcome) == []
    assert outcome.attempts[0].status_code == 403


def test_parse_detail_takes_this_jobs_own_apply_url():
    detail = xing.parse_detail(JOB.decode("utf-8"), "158289963")
    assert detail["apply_url"].startswith(OWN_APPLY)
    assert detail["title"] == "Data and Analytics Engineer (w/m/d)"
    assert detail["company"] == "Deutsche Glasfaser"
    assert detail["location"] == "Düsseldorf"
    assert detail["country"] == "DE"
    assert detail["posted_at"] == "2026-10-02T11:04:44Z"


def test_normalize_builds_the_shared_record():
    card = {"id": "158289963", "url": "https://www.xing.com/jobs/duesseldorf-data-and-analytics-engineer-158289963",
            "title": "x", "company": "y", "location": "Düsseldorf"}
    record = xing.normalize(card, JOB.decode("utf-8"))
    assert record["job_id"] == "xing_158289963"
    assert record["provider"] == "xing"
    assert record["job_url"] == card["url"]
    assert record["apply_url"].startswith(OWN_APPLY)
    assert record["ats_type"] == "smartrecruiters"
    assert record["company"] == "Deutsche Glasfaser"
    assert record["description"].startswith("Unternehmensbeschreibung")
    assert "null" not in record["description"].split("\n")[0]


def test_an_apply_url_on_xing_itself_is_not_an_employer_link():
    page = JOB.decode("utf-8").replace(
        "https:\\u002F\\u002Fjobs.smartrecruiters.com\\u002FDeutscheGlasfaser\\u002F744000153151708",
        "https:\\u002F\\u002Fwww.xing.com\\u002Fjobs\\u002Fapply")
    detail = xing.parse_detail(page, "158289963")
    assert detail["apply_url"] is None


def test_no_url_application_means_no_apply_url():
    page = JOB.decode("utf-8").replace('"applyUrl"', '"otherField"')
    assert xing.parse_detail(page, "158289963")["apply_url"] is None


def test_a_page_without_a_job_posting_block_normalizes_to_none():
    card = {"id": "1", "url": "https://www.xing.com/jobs/x-1", "title": "t", "company": "c", "location": "Berlin"}
    assert xing.normalize(card, "<html></html>") is None


@pytest.fixture
def pipeline(monkeypatch):
    state = {"existing_ids": set(), "existing_keys": set()}
    from db import supabase_utils
    monkeypatch.setattr(supabase_utils, "get_existing_jobs_from_supabase",
                        lambda: (state["existing_ids"], state["existing_keys"]))
    monkeypatch.setattr(xing, "_delay", lambda: None)
    return state


def _serve(monkeypatch, job_body=JOB, job_status=200):
    def fake_get(url, *a, **k):
        if "/jobs/search" in url:
            return _response(SEARCH)
        return _response(job_body, job_status)
    monkeypatch.setattr(xing.requests, "get", fake_get)


def test_process_query_drops_foreign_locations_before_the_detail_call(monkeypatch, pipeline):
    _serve(monkeypatch)
    outcome = scrape_guard.SourceOutcome("xing")
    records = xing.process_query("data scientist", outcome=outcome)
    assert [r["job_id"] for r in records] == ["xing_158289963"]
    assert outcome.fetched == 2
    assert outcome.filtered_out == {"foreign_location": 1}


def test_process_query_accounts_for_every_card(monkeypatch, pipeline):
    pipeline["existing_ids"] = {"xing_158289963"}
    _serve(monkeypatch)
    outcome = scrape_guard.SourceOutcome("xing")
    assert xing.process_query("data scientist", outcome=outcome) == []
    outcome.record_query(new=0)
    assert outcome.already_in_db == 1
    assert outcome.unaccounted == 0


def test_process_query_repost_and_detail_failure(monkeypatch, pipeline):
    pipeline["existing_keys"] = {dedup.identity_key("Deutsche Glasfaser",
                                                    "Data and Analytics Engineer (w/m/d)", "Düsseldorf")}
    _serve(monkeypatch)
    outcome = scrape_guard.SourceOutcome("xing")
    assert xing.process_query("data scientist", outcome=outcome) == []
    assert outcome.filtered_out == {"foreign_location": 1, "repost_same_company_title": 1}

    pipeline["existing_keys"] = set()
    _serve(monkeypatch, job_status=500)
    outcome = scrape_guard.SourceOutcome("xing")
    assert xing.process_query("data scientist", outcome=outcome) == []
    assert outcome.filtered_out == {"foreign_location": 1, "detail_fetch_failed": 1}
    assert outcome.unaccounted == 0


def test_runner_is_registered():
    from sources import scraper
    import config
    assert "xing" in scraper.SOURCE_RUNNERS
    assert "xing" in config.SCRAPING_SOURCES
