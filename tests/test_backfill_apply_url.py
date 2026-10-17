"""Filling apply_url on stored rows whose job_url already is an ATS form. No network."""
from maintenance import backfill_apply_url


ROWS = [
    {"job_id": "a", "job_url": "https://jobs.lever.co/acme/1"},
    {"job_id": "b", "job_url": "https://www.linkedin.com/jobs/view/1/"},
    {"job_id": "c", "job_url": "https://www.xing.com/jobs/x-1"},
    {"job_id": "d", "job_url": "https://acme.de/karriere/1"},
    {"job_id": "e", "job_url": None},
]


def test_only_rows_on_a_known_ats_are_updated():
    """'other' (a company site) could be a careers landing page, so it is not
    promoted to an apply link without evidence."""
    assert backfill_apply_url.plan_updates(ROWS) == [("a", "https://jobs.lever.co/acme/1", "lever")]


def test_dry_run_writes_nothing(monkeypatch, capsys):
    writes = []
    monkeypatch.setattr(backfill_apply_url, "fetch_rows_without_apply_url", lambda limit: ROWS)
    monkeypatch.setattr(backfill_apply_url.supabase_utils, "set_apply_url", lambda *a: writes.append(a) or True)
    assert backfill_apply_url.main([]) == 0
    assert writes == []
    assert "would update 1" in capsys.readouterr().out


def test_apply_writes_each_update(monkeypatch, capsys):
    writes = []
    monkeypatch.setattr(backfill_apply_url, "fetch_rows_without_apply_url", lambda limit: ROWS)
    monkeypatch.setattr(backfill_apply_url.supabase_utils, "set_apply_url", lambda *a: writes.append(a) or True)
    assert backfill_apply_url.main(["--apply"]) == 0
    assert writes == [("a", "https://jobs.lever.co/acme/1", "lever")]
    assert "updated 1" in capsys.readouterr().out
