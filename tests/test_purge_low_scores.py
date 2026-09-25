"""The automatic purge: what it deletes, and more importantly what it will not.

It runs unattended in the pipeline over a table where 56% of rows scored under
20, so the tests that matter are the ones pinning the exclusions. Everything the
purge is forbidden to touch is a record of something a human did.
"""

import pytest

from maintenance import purge_low_scores as purge


class FakeQuery:
    def __init__(self, client):
        self.client = client
        self.filters = {}

    def select(self, columns):
        self.client.selected = columns
        return self

    def lt(self, field, value):
        self.filters[f"lt:{field}"] = value
        return self

    def eq(self, field, value):
        self.filters[f"eq:{field}"] = value
        return self

    def is_(self, field, value):
        if field in self.client.missing_columns:
            raise Exception(f'column "{field}" does not exist')
        self.filters[f"is:{field}"] = value
        return self

    def order(self, field, desc=False):
        self.client.ordered = (field, desc)
        return self

    def limit(self, n):
        self.client.limited = n
        return self

    def execute(self):
        self.client.filters = dict(self.filters)
        return type("R", (), {"data": list(self.client.rows)})()


class FakeClient:
    def __init__(self, rows=(), missing_columns=()):
        self.rows = list(rows)
        self.missing_columns = set(missing_columns)
        self.filters = {}
        self.selected = ""
        self.ordered = None
        self.limited = None

    def table(self, name):
        return FakeQuery(self)


def _rows(n=3):
    return [{"job_id": f"j{i}", "job_title": f"Job {i}", "company": "ACME",
             "resume_score": i, "score_breakdown": {}} for i in range(n)]


@pytest.fixture
def client(monkeypatch):
    def install(rows=(), missing_columns=()):
        fake = FakeClient(rows, missing_columns)
        monkeypatch.setattr(purge.supabase_utils, "supabase", fake)
        return fake

    return install


class TestWhatItRefusesToTouch:
    def test_only_untouched_postings_are_eligible(self, client):
        """An applied job is a record of something you did; a dismissal is a
        decision with a reason attached. A low score erases neither."""
        fake = client(_rows())
        purge.find_purgeable(20, 400)
        assert fake.filters["eq:status"] == "new"
        assert fake.filters["is:application_date"] is None
        assert fake.filters["is:dismissed_at"] is None

    def test_the_floor_is_strict(self, client):
        fake = client(_rows())
        purge.find_purgeable(20, 400)
        assert fake.filters["lt:resume_score"] == 20, "20 itself survives"

    def test_a_dangerous_floor_is_refused_outright(self, client, capsys):
        """No one is at the terminal to answer a confirmation prompt in CI."""
        client(_rows())
        assert purge.main(["--min-score", "60"]) == 2

    def test_worst_first_so_a_capped_run_takes_the_worst(self, client):
        fake = client(_rows())
        purge.find_purgeable(20, 50)
        assert fake.ordered == ("resume_score", False)
        assert fake.limited == 50


class TestPurging:
    def test_every_delete_goes_through_the_tombstone_path(self, client, monkeypatch):
        """Not a bare row delete: without a tombstone the next scrape re-adds
        the posting and pays to screen it again."""
        client(_rows(3))
        seen = []
        monkeypatch.setattr(purge.supabase_utils, "delete_job",
                            lambda job, reason, note=None: seen.append((job["job_id"], reason, note)) or True)
        counts = purge.purge(min_score=20, limit=400)
        assert counts == {"found": 3, "deleted": 3, "failed": 0}
        assert [s[1] for s in seen] == [purge.REASON] * 3
        assert all("auto-purged" in s[2] for s in seen)

    def test_a_dry_run_deletes_nothing(self, client, monkeypatch):
        client(_rows(3))
        monkeypatch.setattr(purge.supabase_utils, "delete_job",
                            lambda *a, **k: pytest.fail("dry run deleted something"))
        assert purge.purge(dry_run=True) == {"found": 3, "deleted": 0, "failed": 0}

    def test_failures_are_counted_not_swallowed(self, client, monkeypatch):
        """A missing tombstone table fails every delete; the run must say so."""
        client(_rows(2))
        monkeypatch.setattr(purge.supabase_utils, "delete_job", lambda *a, **k: False)
        counts = purge.purge()
        assert counts == {"found": 2, "deleted": 0, "failed": 2}
        assert purge.main([]) == 1, "a failed purge exits non-zero"

    def test_nothing_to_do_is_not_an_error(self, client):
        client([])
        assert purge.purge() == {"found": 0, "deleted": 0, "failed": 0}


class TestOlderDatabases:
    def test_a_missing_dismissed_at_column_does_not_stop_the_purge(self, client):
        """Pre-add_dismissal.sql. It logs that dismissals cannot be excluded."""
        fake = client(_rows(2), missing_columns={"dismissed_at"})
        rows = purge.find_purgeable(20, 400)
        assert len(rows) == 2

    def test_the_reason_is_a_recognised_one(self):
        from db import supabase_utils

        assert purge.REASON in supabase_utils.VALID_DELETE_REASONS

    def test_the_purge_reason_is_never_a_button_in_the_ui(self):
        """It is the pipeline's judgement, not something you said about a job."""
        from review import apply_queue

        assert purge.REASON not in apply_queue.DELETE_REASONS
        assert purge.REASON in apply_queue.DELETE_REASON_LABELS


class TestEnvOverrides:
    def test_an_undefined_repository_variable_arrives_empty(self, monkeypatch):
        """Actions sets an unset `vars.X` to "", and int("") would crash the step."""
        import config

        monkeypatch.setenv("PURGE_MIN_SCORE", "")
        assert config._int_env("PURGE_MIN_SCORE", 20) == 20
        monkeypatch.setenv("PURGE_MIN_SCORE", "15")
        assert config._int_env("PURGE_MIN_SCORE", 20) == 15
