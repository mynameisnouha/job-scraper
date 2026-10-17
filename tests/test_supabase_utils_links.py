"""The new link columns must not break scraping or the queue before the owner has
run supabase_setup/add_apply_url.sql and add_companies.sql."""
from unittest.mock import MagicMock

from db import supabase_utils

MISSING = "Could not find the '{}' column of 'jobs' in the schema cache"


class FakeTable:
    def __init__(self, missing=(), links=None, fail_links=False):
        self.missing = set(missing)
        self.upserts = []
        self.links = links or []
        self.fail_links = fail_links
        self.selected = None

    def upsert(self, rows, **kwargs):
        for name in self.missing:
            if any(name in row for row in rows):
                raise Exception(MISSING.format(name))
        self.upserts.append([dict(row) for row in rows])
        return self

    def select(self, cols):
        self.selected = cols
        return self

    def in_(self, col, values):
        if self.fail_links:
            raise Exception(MISSING.format("apply_url"))
        return self

    def execute(self):
        if self.selected:
            return MagicMock(data=self.links)
        # save_jobs_to_supabase unpacks the result as (data, count).
        return ("data", ["ok"]), ("count", None)


def _job(**extra):
    return {"job_id": "linkedin_1", "job_title": "Data Scientist", "company": "Acme",
            "provider": "linkedin", "job_url": "https://x", **extra}


def test_save_drops_link_columns_the_table_does_not_have_yet(monkeypatch):
    table = FakeTable(missing={"apply_url", "ats_type", "linkedin_apply_mode"})
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: table)
    job = _job(apply_url=None, ats_type=None, linkedin_apply_mode="offsite")
    assert supabase_utils.save_jobs_to_supabase([job]) == 1
    (saved,), = table.upserts
    assert "apply_url" not in saved and "ats_type" not in saved and "linkedin_apply_mode" not in saved
    assert saved["job_title"] == "Data Scientist"


def test_save_drops_company_id_when_the_companies_migration_is_missing(monkeypatch):
    table = FakeTable(missing={"company_id"})
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: table)
    assert supabase_utils.save_jobs_to_supabase([_job(company_id=3, apply_url="https://y")]) == 1
    (saved,), = table.upserts
    assert "company_id" not in saved and saved["apply_url"] == "https://y"


def test_save_keeps_link_columns_when_they_exist(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: table)
    assert supabase_utils.save_jobs_to_supabase([_job(apply_url="https://y", ats_type="lever")]) == 1
    (saved,), = table.upserts
    assert saved["apply_url"] == "https://y"


def test_an_unrelated_error_still_fails_the_save(monkeypatch):
    class Broken(FakeTable):
        def upsert(self, rows, **kwargs):
            raise Exception("connection reset")
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: Broken())
    assert supabase_utils.save_jobs_to_supabase([_job()]) == 0


def test_attach_apply_links_merges_by_job_id(monkeypatch):
    table = FakeTable(links=[{"job_id": "a", "apply_url": "https://jobs.lever.co/x/1",
                              "ats_type": "lever", "linkedin_apply_mode": None}])
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: table)
    jobs = supabase_utils.attach_apply_links([{"job_id": "a"}, {"job_id": "b"}])
    assert jobs[0]["apply_url"] == "https://jobs.lever.co/x/1" and jobs[0]["ats_type"] == "lever"
    assert "apply_url" not in jobs[1]


def test_attach_apply_links_without_the_columns_leaves_jobs_as_they_were(monkeypatch):
    table = FakeTable(fail_links=True)
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: table)
    jobs = [{"job_id": "a"}]
    assert supabase_utils.attach_apply_links(jobs) == [{"job_id": "a"}]


def test_attach_apply_links_with_no_jobs_makes_no_call(monkeypatch):
    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: (_ for _ in ()).throw(AssertionError))
    assert supabase_utils.attach_apply_links([]) == []


def test_attach_apply_links_queries_in_chunks(monkeypatch):
    """Final review: 999 ids in one GET can exceed URL limits, and the failure is
    swallowed, so every link would silently vanish."""
    calls = []

    class Chunked(FakeTable):
        def in_(self, col, values):
            calls.append(len(values))
            return self

    monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: Chunked())
    supabase_utils.attach_apply_links([{"job_id": str(i)} for i in range(400)])
    assert len(calls) > 1 and max(calls) <= 150 and sum(calls) == 400
