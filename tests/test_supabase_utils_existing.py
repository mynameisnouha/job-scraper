"""The "what is already in the database" fetch that every scrape run depends on.

This is the function the scrapers ask before deciding a posting is new. It went
silently broken once: it calls `dedup.normalize_*` but the module was never
imported, and because the whole loop sits inside a try/except that only logs,
every run returned an almost-empty set and re-processed the entire corpus. The
scraper tests all monkeypatch this function, so nothing caught it — hence a test
that runs the real body against a fake client.
"""
from unittest.mock import MagicMock

from db import supabase_utils


def fake_client(monkeypatch, pages):
    """A Supabase stand-in that serves `pages` (a list of row batches) in order."""
    served = []

    class FakeQuery:
        def select(self, *a, **k):
            return self

        def range(self, *a, **k):
            return self

        def execute(self):
            batch = pages[len(served)] if len(served) < len(pages) else []
            served.append(batch)
            return MagicMock(data=batch)

    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeQuery())
    return served


class TestGetExistingJobs:
    def test_returns_both_ids_and_normalised_company_title_keys(self, monkeypatch):
        fake_client(monkeypatch, [[
            {"job_id": "1", "company": "YPOG GmbH", "job_title": "AI/ML Engineer (m/w/d)"},
            {"job_id": "2", "company": "ACME AG", "job_title": "Data Scientist"},
        ]])
        ids, keys = supabase_utils.get_existing_jobs_from_supabase()
        assert ids == {"1", "2"}
        # Normalised through dedup, not a bare lower(): the legal-form suffix and the
        # (m/w/d) marker are what make one posting look like two across sources.
        assert ("ypog", "ai ml engineer") in keys
        assert ("acme", "data scientist") in keys

    def test_a_row_missing_company_or_title_still_contributes_its_id(self, monkeypatch):
        fake_client(monkeypatch, [[
            {"job_id": "1", "company": None, "job_title": "Data Scientist"},
            {"job_id": "2", "company": "ACME", "job_title": None},
        ]])
        ids, keys = supabase_utils.get_existing_jobs_from_supabase()
        assert ids == {"1", "2"}
        assert keys == set()

    def test_every_page_is_read_not_just_the_first(self, monkeypatch):
        """A partial read is the failure mode that re-scrapes the whole corpus."""
        fake_client(monkeypatch, [
            [{"job_id": "1", "company": "ACME", "job_title": "Data Scientist"}],
            [{"job_id": "2", "company": "Beta", "job_title": "ML Engineer"}],
        ])
        ids, keys = supabase_utils.get_existing_jobs_from_supabase()
        assert ids == {"1", "2"}
        assert len(keys) == 2

    def test_an_empty_table_is_not_an_error(self, monkeypatch):
        fake_client(monkeypatch, [[]])
        assert supabase_utils.get_existing_jobs_from_supabase() == (set(), set())
