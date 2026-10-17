"""The companies table behind the career-page feeds, against a recording fake client."""
from unittest.mock import MagicMock

import pytest

from db import supabase_utils


class Recorder:
    """Records every chained call; serves `pages` from execute() in order."""

    def __init__(self, pages=None, fail=False):
        self.calls = []
        self.tables = []
        self.pages = list(pages or [[]])
        self.fail = fail

    def table(self, name):
        self.tables.append(name)
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return method

    @property
    def not_(self):
        self.calls.append(("not_", (), {}))
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")
        data = self.pages.pop(0) if self.pages else []
        return MagicMock(data=data)

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def rec(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(supabase_utils, "supabase", recorder)
    return recorder


def test_get_companies_to_poll_filters_enabled_rows_with_a_feed(rec):
    rec.pages = [[{"id": 1, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]]
    rows = supabase_utils.get_companies_to_poll()
    assert rows == [{"id": 1, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]
    assert rec.tables == ["companies"]
    assert ("eq", ("poll_enabled", True), {}) in rec.calls
    assert ("is_", ("ats_type", "null"), {}) in rec.calls
    assert ("is_", ("ats_slug", "null"), {}) in rec.calls
    assert len(rec.called("not_")) == 2


def test_upsert_companies_upserts_on_normalized_name(rec):
    rows = [{"name": "Acme GmbH", "name_normalized": "acme"}]
    assert supabase_utils.upsert_companies(rows) == 1
    (_, args, kwargs), = rec.called("upsert")
    assert args[0] == rows
    assert kwargs.get("on_conflict") == "name_normalized"


def test_upsert_companies_with_nothing_does_not_call_the_db(rec):
    assert supabase_utils.upsert_companies([]) == 0
    assert rec.tables == []


def test_record_probe_hit_enables_polling(rec):
    assert supabase_utils.record_probe("acme", "lever", "acme", "found") is True
    (_, args, _), = rec.called("update")
    payload = args[0]
    assert payload["ats_type"] == "lever" and payload["ats_slug"] == "acme"
    assert payload["poll_enabled"] is True
    assert payload["last_probe_result"] == "found"
    assert "last_probe_at" in payload
    assert ("eq", ("name_normalized", "acme"), {}) in rec.calls


def test_record_probe_miss_leaves_the_feed_fields_alone(rec):
    assert supabase_utils.record_probe("acme", None, None, "not_found") is True
    (_, args, _), = rec.called("update")
    assert set(args[0]) == {"last_probe_at", "last_probe_result"}


def test_mark_polled(rec):
    assert supabase_utils.mark_polled(7) is True
    (_, args, _), = rec.called("update")
    assert "last_polled_at" in args[0]
    assert ("eq", ("id", 7), {}) in rec.calls


def test_get_distinct_job_companies_reads_every_page_and_dedups(rec):
    rec.pages = [[{"company": "Acme"}, {"company": None}, {"company": "Beta"}],
                 [{"company": "Acme"}], []]
    assert sorted(supabase_utils.get_distinct_job_companies(batch_size=3)) == ["Acme", "Beta"]


@pytest.mark.parametrize("call", [
    lambda: supabase_utils.upsert_companies([{"name_normalized": "a"}]),
    lambda: supabase_utils.record_probe("a", None, None, "x"),
    lambda: supabase_utils.mark_polled(1),
])
def test_a_db_error_returns_falsy_instead_of_raising(monkeypatch, call):
    monkeypatch.setattr(supabase_utils, "supabase", Recorder(fail=True))
    assert not call()


def test_get_companies_to_poll_on_error_returns_empty(monkeypatch):
    monkeypatch.setattr(supabase_utils, "supabase", Recorder(fail=True))
    assert supabase_utils.get_companies_to_poll() == []


def test_get_all_companies_reads_the_probe_state(rec):
    rec.pages = [[{"name_normalized": "acme", "last_probe_at": None}], []]
    assert supabase_utils.get_all_companies() == [{"name_normalized": "acme", "last_probe_at": None}]
    assert rec.tables[0] == "companies"


def test_get_job_titles_by_company_groups_normalized_titles(rec):
    rec.pages = [[{"company": "Acme GmbH", "job_title": "Data Scientist (m/w/d)"},
                  {"company": "ACME", "job_title": "ML Engineer"},
                  {"company": None, "job_title": "x"}], []]
    assert supabase_utils.get_job_titles_by_company() == {"acme": {"data scientist", "ml engineer"}}
