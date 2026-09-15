"""Deleting a posting outright, and making the deletion stick.

The interesting half is that it sticks. A bare row delete undoes itself: the
posting vanishes from the dedup set, so the next scrape treats it as new, saves
it, and pays to screen and score something that was explicitly thrown away. So
the tombstone is written first, and the scrapers read it.
"""

import pytest

from db import supabase_utils


class _Result:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Records what each table was asked to do, and can be told to fail."""

    def __init__(self, client, name):
        self.client, self.name = client, name
        self.action = None

    def upsert(self, payload):
        if "upsert" in self.client.fail_on:
            raise Exception(f'relation "{self.name}" does not exist')
        self.client.calls.append((self.name, "upsert", payload))
        self.action = "upsert"
        return self

    def delete(self):
        self.action = "delete"
        return self

    def select(self, *_a, **_k):
        if "select" in self.client.fail_on:
            raise Exception(f'relation "{self.name}" does not exist')
        self.action = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def execute(self):
        if self.action == "delete":
            self.client.calls.append((self.name, "delete", None))
            return _Result([{"job_id": "j1"}])
        if self.action == "select":
            return _Result(self.client.rows)
        return _Result([{"job_id": "j1"}])


class FakeClient:
    def __init__(self, fail_on=(), rows=None):
        self.calls = []
        self.rows = rows or []
        self.fail_on = fail_on

    def table(self, name):
        return FakeTable(self, name)


JOB = {"job_id": "j1", "job_title": "AI/ML Engineer (m/w/d)", "company": "YPOG GmbH"}


@pytest.fixture
def client(monkeypatch):
    def install(fail_on=(), rows=None):
        fake = FakeClient(fail_on, rows)
        monkeypatch.setattr(supabase_utils, "supabase", fake)
        return fake

    return install


class TestDelete:
    def test_the_tombstone_is_written_before_the_row_goes(self, client):
        """The other order loses the posting and the memory of it together."""
        fake = client()
        assert supabase_utils.delete_job(JOB, "agency_repost") is True
        assert [(t, op) for t, op, _ in fake.calls] == [
            ("deleted_jobs", "upsert"), ("jobs", "delete")]

    def test_the_tombstone_carries_a_normalised_dedup_key(self, client):
        """So the same posting from another source, with another id, is still caught."""
        fake = client()
        supabase_utils.delete_job(JOB, "duplicate_posting", note="same as j7")
        payload = fake.calls[0][2]
        assert "|" in payload["dedup_key"]
        assert payload["dedup_key"] == payload["dedup_key"].lower()
        assert payload["note"] == "same as j7"

    def test_nothing_is_deleted_when_the_tombstone_cannot_be_written(self, client):
        """Without add_deleted_jobs.sql the delete would not stay deleted."""
        fake = client(fail_on=("upsert",))
        assert supabase_utils.delete_job(JOB, "other") is False
        assert [t for t, _, _ in fake.calls] == [], "the row must survive"

    def test_an_unknown_reason_is_refused(self, client):
        client()
        assert supabase_utils.delete_job(JOB, "did not like the vibe") is False

    def test_a_job_without_an_id_is_refused(self, client):
        client()
        assert supabase_utils.delete_job({"company": "ACME"}, "other") is False

    def test_the_delete_vocabulary_is_not_the_skip_vocabulary(self):
        """A skip is a decision about a real job; a delete says the row is junk.

        Sharing the words would put agency reposts into the statistics about why
        she turns work down, which is the one thing dismissals exist to measure.
        """
        shared = supabase_utils.VALID_DELETE_REASONS & supabase_utils.VALID_SKIP_REASONS
        assert shared == {"duplicate_posting", "other"}


class TestDeletedStayDeleted:
    def test_deleted_keys_join_the_dedup_sets(self, client, monkeypatch):
        """What stops the next scrape re-adding and re-scoring a deleted posting."""
        monkeypatch.setattr(supabase_utils, "get_deleted_job_keys",
                            lambda: ({"j9"}, {("ypog", "ai ml engineer")}))
        monkeypatch.setattr(supabase_utils, "supabase", FakeClient(rows=[]))
        ids, keys = supabase_utils.get_existing_jobs_from_supabase()
        assert "j9" in ids
        assert ("ypog", "ai ml engineer") in keys

    def test_a_missing_table_does_not_stop_a_scrape(self, client):
        """Deleted postings reappearing is visible and fixable; no scrape is not."""
        client(fail_on=("select",))
        assert supabase_utils.get_deleted_job_keys() == (set(), set())

    def test_malformed_keys_are_skipped_not_crashed(self, client):
        client(rows=[{"job_id": "j9", "dedup_key": ""},
                     {"job_id": "j8", "dedup_key": "acme|engineer"}])
        ids, keys = supabase_utils.get_deleted_job_keys()
        assert ids == {"j9", "j8"}
        assert keys == {("acme", "engineer")}
