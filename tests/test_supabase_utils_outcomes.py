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
