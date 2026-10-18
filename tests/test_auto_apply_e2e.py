"""Click to chip, end to end, with Supabase, claude and the CV faked.

Guards the seams the unit tests each cover alone: the queue row shape (no `status`
key), eligibility, the queue/claim/result writes, the runner and the UI chip.
"""
import json
from types import SimpleNamespace

import pytest

import config
from apply import bundle, eligibility, runner
from db import supabase_utils
from review import application_pack
from tailor import store
from tests.test_supabase_utils_auto_apply import FakeDB

CLAUDE_JSON = json.dumps({
    "result": "filled the form\nAPPLIED|https://jobs.lever.co/acme/1|confirmation shown",
    "total_cost_usd": 0.31})


def test_click_to_applied_chip(monkeypatch, tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")
    for mod in (application_pack, eligibility):
        monkeypatch.setattr(mod, "choose_cv", lambda title: str(cv))
    monkeypatch.setattr(eligibility, "unfilled_answers", lambda a: [])
    monkeypatch.setattr(application_pack, "load_answers", lambda path=None: {"name": "N"})
    monkeypatch.setattr(store, "load", lambda job_id: None)
    monkeypatch.setattr(config, "GENERIC_COVER_LETTER_PATH", None)
    monkeypatch.setattr(config, "AUTO_APPLY_WORK_DIR", str(tmp_path / "work"))

    db_row = {"job_id": "j1", "job_title": "Data Engineer", "company": "Acme",
              "location": "Berlin", "description": "Do things", "resume_score": 80,
              "job_url": "https://x/j", "apply_url": "https://jobs.lever.co/acme/1",
              "ats_type": "lever", "linkedin_apply_mode": None, "why_me_pitch": "w",
              "score_breakdown": {}, "dismissed_at": None,
              "auto_apply_status": None, "auto_apply_reason": None,
              "auto_apply_cost_usd": None, "auto_apply_updated_at": None}
    db = FakeDB([db_row])
    monkeypatch.setattr(supabase_utils.supabase, "table", db.table)
    marks = []
    monkeypatch.setattr(supabase_utils, "mark_job_applied", lambda j: marks.append(("mark", j)) or True)
    monkeypatch.setattr(supabase_utils, "update_application_stage",
                        lambda j, s, *a, **k: marks.append(("stage", j, s)) or True)

    # The queue row as get_top_scored_jobs_to_apply selects it: no `status` key.
    queue_row = {k: db_row[k] for k in ("job_id", "job_title", "company", "resume_score",
                                        "job_url", "why_me_pitch", "score_breakdown",
                                        "dismissed_at")}
    (job,) = supabase_utils.attach_apply_links([queue_row])
    assert "status" not in job
    assert eligibility.check(job, {"name": "N"}, lambda p: True) == (True, None)

    assert supabase_utils.queue_auto_apply("j1") is True
    claimed = supabase_utils.claim_next_auto_apply("laptop")
    assert claimed["job_id"] == "j1"

    class FakePopen:
        def __init__(self, cmd, **kw):
            self.returncode = 0

        def communicate(self, input=None, timeout=None):
            return CLAUDE_JSON, ""

    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(runner.worker, "check_claude_bin", lambda: "claude")
    result = runner.run_one(claimed, SimpleNamespace(headless=True, model="sonnet"))
    assert result.status == "applied" and result.cost_usd == 0.31

    assert supabase_utils.set_auto_apply_result(
        "j1", result.status, result.detail, result.cost_usd) is True
    assert marks == [("mark", "j1"), ("stage", "j1", "applied")]
    assert db.rows[0]["auto_apply_status"] == "applied"
    assert db.rows[0]["auto_apply_cost_usd"] == 0.31

    import ui_app
    (applied_job,) = supabase_utils.attach_apply_links([dict(queue_row)])
    facts = dict(ui_app.card_facts(applied_job, {}))
    assert facts["Auto apply"] == "Applied · $0.31"
