"""Exact feed names read from employer links the scrapers already collect."""
import pytest

from sources.ats import harvest


@pytest.fixture
def db(monkeypatch):
    state = {"companies": [], "upserted": [], "probes": []}
    monkeypatch.setattr(harvest.supabase_utils, "get_all_companies", lambda: state["companies"])
    monkeypatch.setattr(harvest.supabase_utils, "upsert_companies",
                        lambda rows: state["upserted"].extend(rows) or len(rows))
    monkeypatch.setattr(harvest.supabase_utils, "record_probe",
                        lambda *a: state["probes"].append(a) or True)
    return state


def _rec(company, apply_url):
    return {"company": company, "apply_url": apply_url}


def test_a_feed_link_switches_polling_on_for_its_company(db):
    n = harvest.harvest([_rec("Thermondo GmbH", "https://thermondo.jobs.personio.de/job/1")])
    assert n == 1
    assert db["upserted"] == [{"name": "Thermondo GmbH", "name_normalized": "thermondo"}]
    assert db["probes"] == [("thermondo", "personio", "thermondo", "harvested")]


def test_a_company_that_already_has_a_feed_is_left_alone(db):
    db["companies"] = [{"name_normalized": "thermondo", "ats_type": "personio", "ats_slug": "thermondo"}]
    assert harvest.harvest([_rec("Thermondo", "https://other.jobs.personio.de/job/1")]) == 0
    assert db["probes"] == [] and db["upserted"] == []


def test_a_known_company_without_a_feed_gets_one_without_a_new_row(db):
    db["companies"] = [{"name_normalized": "thermondo", "ats_type": None}]
    assert harvest.harvest([_rec("Thermondo", "https://thermondo.jobs.personio.de/job/1")]) == 1
    assert db["upserted"] == []


def test_links_without_a_feed_name_are_ignored(db):
    records = [_rec("Henkel", "https://www.henkel.de/karriere/1"),
               _rec("Turck", "https://turck.wd3.myworkdayjobs.com/x"),
               _rec(None, "https://acme.jobs.personio.de/job/1"),
               {"company": "Acme"}]
    assert harvest.harvest(records) == 0
    assert db["probes"] == []


def test_nothing_to_harvest_reads_nothing(monkeypatch):
    monkeypatch.setattr(harvest.supabase_utils, "get_all_companies",
                        lambda: (_ for _ in ()).throw(AssertionError("no read expected")))
    assert harvest.harvest([]) == 0


def test_a_database_error_never_fails_the_scrape(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(harvest.supabase_utils, "get_all_companies", boom)
    assert harvest.harvest([_rec("Acme", "https://acme.jobs.personio.de/job/1")]) == 0


@pytest.mark.parametrize("source", ["xing", "arbeitsagentur"])
def test_the_runners_harvest_what_they_saved(monkeypatch, source):
    import importlib
    from sources import scraper
    module = importlib.import_module(f"sources.{source}")
    queries = "XING_SEARCH_QUERIES" if source == "xing" else "ARBEITSAGENTUR_SEARCH_QUERIES"
    monkeypatch.setattr(scraper.config, queries, ["q1", "q2"])
    rec = _rec("Acme", "https://acme.jobs.personio.de/job/1")
    monkeypatch.setattr(module, "process_query", lambda q, limit=None, outcome=None: [dict(rec, job_id=q)])
    monkeypatch.setattr(scraper.supabase_utils, "save_jobs_to_supabase", lambda jobs: len(jobs))
    seen = []
    monkeypatch.setattr(harvest, "harvest", lambda records: seen.append(list(records)) or 0)
    getattr(scraper, f"run_{source}")()
    assert [r["job_id"] for r in seen[0]] == ["q1", "q2"]
