from unittest.mock import MagicMock

import supabase_utils


class TestUpdateApplicationStage:
    def test_rejects_invalid_stage(self):
        assert supabase_utils.update_application_stage("job1", "not_a_real_stage") is False

    def test_rejects_missing_job_id(self):
        assert supabase_utils.update_application_stage("", "applied") is False

    def test_valid_stage_writes_expected_payload(self, monkeypatch):
        captured = {}

        class FakeQuery:
            def eq(self, *a, **k):
                return self

            def execute(self):
                return MagicMock(data=[{"job_id": "job1"}])

        class FakeTable:
            def update(self, payload):
                captured["payload"] = payload
                return FakeQuery()

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeTable())

        ok = supabase_utils.update_application_stage("job1", "interview_1", notes="great call")
        assert ok is True
        assert captured["payload"]["application_stage"] == "interview_1"
        assert captured["payload"]["outcome_notes"] == "great call"
        assert "rejection_reason" not in captured["payload"]

    def test_spam_or_removed_is_a_valid_stage(self, monkeypatch):
        captured = {}

        class FakeQuery:
            def eq(self, *a, **k):
                return self

            def execute(self):
                return MagicMock(data=[{"job_id": "job1"}])

        class FakeTable:
            def update(self, payload):
                captured["payload"] = payload
                return FakeQuery()

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeTable())

        assert supabase_utils.update_application_stage("job1", "spam_or_removed") is True
        assert captured["payload"]["application_stage"] == "spam_or_removed"

    def test_missing_columns_fails_soft(self, monkeypatch):
        class FakeTable:
            def update(self, payload):
                raise Exception("column application_stage does not exist")

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeTable())

        assert supabase_utils.update_application_stage("job1", "applied") is False


class TestGetAppliedJobsWithOutcomes:
    def test_falls_back_when_outcome_columns_missing(self, monkeypatch):
        calls = []

        class FakeQuery:
            def eq(self, *a, **k):
                return self

            def order(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                calls.append(1)
                if len(calls) == 1:
                    raise Exception("column application_stage does not exist")
                return MagicMock(data=[{"job_id": "job1"}])

        class FakeTable:
            def select(self, cols):
                return FakeQuery()

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeTable())

        result = supabase_utils.get_applied_jobs_with_outcomes()
        assert result == [{"job_id": "job1"}]
        assert len(calls) == 2


class TestDismissJob:
    def _fake_table(self, monkeypatch, captured, rows=None):
        class FakeQuery:
            def eq(self, *a, **k):
                return self

            def execute(self):
                return MagicMock(data=rows if rows is not None else [{"job_id": "job1"}])

        class FakeTable:
            def update(self, payload):
                captured["payload"] = payload
                return FakeQuery()

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeTable())

    def test_rejects_missing_job_id(self):
        assert supabase_utils.dismiss_job("", "not_interested") is False

    def test_rejects_an_unknown_reason(self):
        """The reasons are a fixed vocabulary — free text can't be counted later."""
        assert supabase_utils.dismiss_job("job1", "did not like the vibe") is False

    def test_writes_only_the_dismissal_fields(self, monkeypatch):
        """
        A dismissal must never look like an application. `status` and `is_active`
        stay untouched so the row keeps meaning what it meant.
        """
        captured = {}
        self._fake_table(monkeypatch, captured)
        assert supabase_utils.dismiss_job("job1", "german_level") is True
        payload = captured["payload"]
        assert payload["dismissal_reason"] == "german_level"
        assert payload["dismissed_at"]
        assert set(payload) == {"dismissed_at", "dismissal_reason"}

    def test_a_reasonless_dismissal_still_records_the_timestamp(self, monkeypatch):
        captured = {}
        self._fake_table(monkeypatch, captured)
        assert supabase_utils.dismiss_job("job1") is True
        assert "dismissal_reason" not in captured["payload"]

    def test_missing_migration_fails_soft(self, monkeypatch):
        class FakeTable:
            def update(self, payload):
                raise Exception('column "dismissed_at" does not exist')

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeTable())
        assert supabase_utils.dismiss_job("job1", "other") is False

    def test_no_matching_row_is_a_failure_not_a_silent_pass(self, monkeypatch):
        captured = {}
        self._fake_table(monkeypatch, captured, rows=[])
        assert supabase_utils.dismiss_job("job1", "other") is False

    def test_undismiss_clears_both_fields(self, monkeypatch):
        captured = {}
        self._fake_table(monkeypatch, captured)
        assert supabase_utils.undismiss_job("job1") is True
        assert captured["payload"] == {"dismissed_at": None, "dismissal_reason": None}

    def test_the_ui_and_the_db_agree_on_the_reason_vocabulary(self):
        import apply_queue
        assert set(apply_queue.SKIP_REASONS) == supabase_utils.VALID_SKIP_REASONS


class TestQueueExcludesDismissed:
    def test_dismissed_jobs_are_filtered_out_in_the_query(self, monkeypatch):
        filters = []

        class FakeQuery:
            def select(self, cols):
                filters.append(("select", cols))
                return self

            def eq(self, col, val):
                filters.append(("eq", col, val))
                return self

            def is_(self, col, val):
                filters.append(("is", col, val))
                return self

            @property
            def not_(self):
                return self

            def order(self, *a, **k):
                return self

            def limit(self, n):
                return self

            def execute(self):
                return MagicMock(data=[{"job_id": "job1"}])

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeQuery())

        supabase_utils.get_top_scored_jobs_to_apply(10)
        assert ("is", "dismissed_at", None) in filters

    def test_falls_back_to_an_unfiltered_query_before_the_migration(self, monkeypatch):
        """Without the migration the queue still works — dismissals just don't stick."""
        attempts = []

        class FakeQuery:
            def __init__(self, cols):
                self.cols = cols

            def select(self, cols):
                self.cols = cols
                return self

            def eq(self, *a, **k):
                return self

            def is_(self, col, val):
                if col == "dismissed_at":
                    raise Exception('column jobs.dismissed_at does not exist')
                return self

            @property
            def not_(self):
                return self

            def order(self, *a, **k):
                return self

            def limit(self, n):
                return self

            def execute(self):
                attempts.append(self.cols)
                return MagicMock(data=[{"job_id": "job1"}])

        monkeypatch.setattr(supabase_utils.supabase, "table", lambda name: FakeQuery(""))

        jobs = supabase_utils.get_top_scored_jobs_to_apply(10)
        assert jobs == [{"job_id": "job1"}]
        assert attempts and "dismissed_at" not in attempts[-1]
