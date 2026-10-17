"""Career-page feed adapters, against recorded fixture responses.

Fixtures are real payloads captured on 2026-10-17 (Greenhouse: helsing, Lever:
contentsquare, Ashby: deepl, SmartRecruiters: DeliveryHero, Recruitee: make,
Workable: blueground), trimmed to three postings and edited so the three cover the
cases the filters care about: a German data role, a US data role, a German sales
role. Long text fields are cut to 800 characters. No test touches the network.
"""
import json
import pathlib

import pytest
import requests

from sources import scrape_guard
from sources.ats import ADAPTERS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
JSON_TYPES = ["greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "workable"]


def _load(name):
    return json.loads((FIXTURES / f"ats_{name}.json").read_text(encoding="utf-8"))


def _response(status_code=200, payload=None, text=None):
    response = requests.Response()
    response.status_code = status_code
    body = text if text is not None else json.dumps(payload, ensure_ascii=False)
    response._content = body.encode("utf-8")
    response.url = "https://example.test/"
    return response


def _serve(monkeypatch, ats_type, adapter_module):
    """Patch requests.get in the adapter's module to serve that ATS's fixture."""
    payload = _load(ats_type)
    detail = _load("smartrecruiters_detail")

    def fake_get(url, *a, **k):
        if ats_type == "smartrecruiters" and "/postings/" in url:
            return _response(payload=detail)
        return _response(payload=payload)
    monkeypatch.setattr(adapter_module.requests, "get", fake_get)


def _first_record(monkeypatch, ats_type):
    adapter = ADAPTERS[ats_type]
    _serve(monkeypatch, ats_type, adapter.module)
    raw = adapter.fetch("acme")[0]
    raw = adapter.enrich(raw, "acme")
    return adapter.normalize(raw, "acme", "Acme GmbH")


def test_registry_covers_every_feed_type():
    assert set(JSON_TYPES) <= set(ADAPTERS)
    for ats_type, adapter in ADAPTERS.items():
        assert adapter.ats_type == ats_type


@pytest.mark.parametrize("ats_type", JSON_TYPES)
def test_normalize_produces_the_shared_record(monkeypatch, ats_type):
    record = _first_record(monkeypatch, ats_type)
    assert record["provider"] == "ats"
    assert record["ats_type"] == ats_type
    assert record["job_id"].startswith(f"ats_{ats_type}_acme_")
    assert record["job_title"] == "Data Scientist (m/w/d)"
    assert record["company"] == "Acme GmbH"
    assert "Berlin" in (record["location"] or "")
    assert record["job_url"].startswith("https://")
    assert record["apply_url"].startswith("https://")
    assert record["level"] is None
    assert record["description"] and "&lt;" not in record["description"]


@pytest.mark.parametrize("ats_type", JSON_TYPES)
def test_fetch_returns_every_posting_and_records_the_attempt(monkeypatch, ats_type):
    adapter = ADAPTERS[ats_type]
    _serve(monkeypatch, ats_type, adapter.module)
    outcome = scrape_guard.SourceOutcome("ats")
    raws = adapter.fetch("acme", outcome=outcome)
    assert len(raws) == 3
    assert len(outcome.attempts) == 1
    attempt = outcome.attempts[0]
    assert attempt.query == f"{ats_type}:acme"
    assert attempt.status_code == 200 and attempt.items_parsed == 3


@pytest.mark.parametrize("ats_type", JSON_TYPES)
def test_a_404_is_an_empty_feed_with_the_status_recorded(monkeypatch, ats_type):
    adapter = ADAPTERS[ats_type]
    monkeypatch.setattr(adapter.module.requests, "get", lambda *a, **k: _response(404, text="Not Found"))
    outcome = scrape_guard.SourceOutcome("ats")
    assert adapter.fetch("acme", outcome=outcome) == []
    assert outcome.attempts[0].status_code == 404


@pytest.mark.parametrize("ats_type", JSON_TYPES)
def test_a_timeout_is_an_empty_feed_with_the_error_recorded(monkeypatch, ats_type):
    adapter = ADAPTERS[ats_type]

    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(adapter.module.requests, "get", boom)
    outcome = scrape_guard.SourceOutcome("ats")
    assert adapter.fetch("acme", outcome=outcome) == []
    assert outcome.attempts[0].error


def test_smartrecruiters_list_has_no_description_until_enriched(monkeypatch):
    """The list endpoint carries no ad text; the detail call is made only for
    postings that survive the cheap filters, which is why it is a separate step."""
    adapter = ADAPTERS["smartrecruiters"]
    _serve(monkeypatch, "smartrecruiters", adapter.module)
    raw = adapter.fetch("acme")[0]
    assert adapter.normalize(raw, "acme", "Acme") is None
    assert adapter.normalize(adapter.enrich(raw, "acme"), "acme", "Acme") is not None


def test_enrich_failure_returns_none(monkeypatch):
    adapter = ADAPTERS["smartrecruiters"]
    monkeypatch.setattr(adapter.module.requests, "get", lambda *a, **k: _response(500, text="err"))
    assert adapter.enrich({"id": "1"}, "acme") is None


def test_title_and_location_accessors_see_the_raw_posting(monkeypatch):
    """run_ats filters on these before paying for detail calls or saving."""
    for ats_type in JSON_TYPES:
        adapter = ADAPTERS[ats_type]
        _serve(monkeypatch, ats_type, adapter.module)
        raws = adapter.fetch("acme")
        assert [adapter.title_of(r) for r in raws] == [
            "Data Scientist (m/w/d)", "Senior Data Engineer", "Account Executive DACH"]
        assert "New York" in adapter.location_of(raws[1])


# --- Personio (XML) -----------------------------------------------------------
# Real feed captured 2026-10-17 from appliedai.jobs.personio.de, trimmed to three
# positions and edited like the JSON fixtures.

PERSONIO_XML = (FIXTURES / "ats_personio.xml").read_bytes()


def _xml_response(body, status_code=200):
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    response.url = "https://acme.jobs.personio.de/xml"
    return response


@pytest.fixture
def personio(monkeypatch):
    adapter = ADAPTERS["personio"]
    monkeypatch.setattr(adapter.module.requests, "get", lambda *a, **k: _xml_response(PERSONIO_XML))
    return adapter


def test_personio_fetch_reads_every_position(personio):
    outcome = scrape_guard.SourceOutcome("ats")
    raws = personio.fetch("acme", outcome=outcome)
    assert [personio.title_of(r) for r in raws] == [
        "Data Scientist (m/w/d)", "Senior Data Engineer", "Account Executive DACH"]
    assert personio.location_of(raws[1]) == "New York"
    assert outcome.attempts[0].status_code == 200 and outcome.attempts[0].items_parsed == 3


def test_personio_normalize_produces_the_shared_record(personio):
    raw = personio.fetch("acme")[0]
    record = personio.normalize(personio.enrich(raw, "acme"), "acme", "Acme GmbH")
    assert record["provider"] == "ats" and record["ats_type"] == "personio"
    assert record["job_id"] == f"ats_personio_acme_{raw['id']}"
    assert record["job_url"] == f"https://acme.jobs.personio.de/job/{raw['id']}"
    assert record["apply_url"] == record["job_url"]
    assert record["location"] == "Berlin"
    assert record["description"] and "&lt;" not in record["description"]
    assert record["posted_at"]


def test_personio_position_without_descriptions_is_dropped(personio):
    raw = dict(personio.fetch("acme")[0], descriptions=[])
    assert personio.normalize(raw, "acme", "Acme") is None


def test_personio_malformed_xml_is_an_empty_feed_with_the_error_recorded(monkeypatch):
    adapter = ADAPTERS["personio"]
    monkeypatch.setattr(adapter.module.requests, "get", lambda *a, **k: _xml_response(b"<workzag-jobs><position>"))
    outcome = scrape_guard.SourceOutcome("ats")
    assert adapter.fetch("acme", outcome=outcome) == []
    assert outcome.attempts[0].error
