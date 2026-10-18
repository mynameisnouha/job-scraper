"""Auto-apply queue helpers: atomic claim, guarded queueing, result write-back."""
import datetime
from types import SimpleNamespace

from db import supabase_utils


class FakeQuery:
    """In-memory jobs table that honours the filters the helpers chain."""

    def __init__(self, db):
        self.db = db
        self.op = None
        self.payload = None
        self.filters = []
        self.order_col = None
        self.order_desc = False
        self.max = None

    def select(self, cols):
        self.op = "select"
        self.db.selects.append(cols)
        if self.db.fail_select_on and self.db.fail_select_on in cols:
            self.db.fail_select_on_hit = True
            raise Exception("column jobs.auto_apply_status does not exist")
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = dict(payload)
        self.db.updates.append(dict(payload))
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        self.db.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def or_(self, expr):
        self.db.filters.append(("or", expr))
        # "col.is.null,col.in.(a,b,c)"
        col = expr.split(".")[0]
        inside = expr[expr.index("in.(") + 4:expr.rindex(")")].split(",")
        self.filters.append(lambda r: r.get(col) is None or r.get(col) in inside)
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and r[col] < val)
        return self

    def like(self, col, pattern):
        needle = pattern.strip("%")
        self.filters.append(lambda r: needle in (r.get(col) or ""))
        return self

    def order(self, col, desc=False):
        self.order_col, self.order_desc = col, desc
        return self

    def limit(self, n):
        self.max = n
        return self

    def execute(self):
        rows = [r for r in self.db.rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            if self.db.lose_next_update and self.payload.get("auto_apply_status") == "running":
                self.db.lose_next_update = False
                return SimpleNamespace(data=[])
            for r in rows:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in rows])
        if self.order_col:
            rows.sort(key=lambda r: r.get(self.order_col) or "", reverse=self.order_desc)
        if self.max is not None:
            rows = rows[:self.max]
        return SimpleNamespace(data=[dict(r) for r in rows])


class FakeDB:
    def __init__(self, rows, fail_select_on=None):
        self.rows = rows
        self.updates = []
        self.selects = []
        self.filters = []
        self.lose_next_update = False
        self.fail_select_on = fail_select_on
        self.fail_select_on_hit = False

    def table(self, name):
        return FakeQuery(self)


def _install(monkeypatch, rows, **kw):
    db = FakeDB(rows, **kw)
    monkeypatch.setattr(supabase_utils.supabase, "table", db.table)
    return db


def _row(job_id, status=None, ts="2026-01-01T00:00:00+00:00", **extra):
    return {"job_id": job_id, "job_title": "DS", "company": "Acme", "auto_apply_status": status,
            "auto_apply_updated_at": ts, **extra}


def test_queue_only_from_requeueable_states(monkeypatch):
    db = _install(monkeypatch, [_row("a"), _row("b", "ready_to_submit"), _row("c", "running"),
                                _row("d", "applied"), _row("e", "dead")])
    assert supabase_utils.queue_auto_apply("a") is True
    assert supabase_utils.queue_auto_apply("b") is True
    (_, expr), = [f for f in db.filters if f[0] == "or"][:1]
    assert "is.null" in expr
    for state in ("ready_to_submit", "verify", "failed"):
        assert state in expr
    for blocked in ("c", "d", "e"):
        assert supabase_utils.queue_auto_apply(blocked) is False
    assert {r["job_id"]: r["auto_apply_status"] for r in db.rows}["c"] == "running"
    assert all(u["auto_apply_updated_at"] for u in db.updates)


def test_two_claims_one_winner(monkeypatch):
    db = _install(monkeypatch, [_row("a", "queued")])
    first = supabase_utils.claim_next_auto_apply("laptop")
    assert first["job_id"] == "a"
    assert supabase_utils.claim_next_auto_apply("laptop") is None
    assert db.rows[0]["auto_apply_status"] == "running"
    assert db.rows[0]["auto_apply_worker"] == "laptop"


def test_claim_skips_a_row_taken_by_someone_else(monkeypatch):
    db = _install(monkeypatch, [_row("a", "queued", ts="2026-01-01T00:00:00+00:00"),
                                _row("b", "queued", ts="2026-01-02T00:00:00+00:00")])
    db.lose_next_update = True  # another runner wins "a" between our select and update
    # The rival's win is simulated by flipping "a" once our conditional update loses.
    real = FakeQuery.execute

    def execute(self):
        res = real(self)
        if self.op == "update" and not res.data and self.payload.get("auto_apply_status") == "running":
            db.rows[0]["auto_apply_status"] = "running"
        return res

    monkeypatch.setattr(FakeQuery, "execute", execute)
    got = supabase_utils.claim_next_auto_apply("laptop")
    assert got["job_id"] == "b"


def test_applied_marks_the_job_applied(monkeypatch):
    db = _install(monkeypatch, [_row("j1", "running")])
    calls = []
    monkeypatch.setattr(supabase_utils, "mark_job_applied", lambda j: calls.append(("mark", j)) or True)
    monkeypatch.setattr(supabase_utils, "update_application_stage",
                        lambda j, s, *a, **k: calls.append(("stage", j, s)) or True)
    assert supabase_utils.set_auto_apply_result("j1", "applied", None, 0.42) is True
    assert calls == [("mark", "j1"), ("stage", "j1", "applied")]
    assert db.rows[0]["auto_apply_cost_usd"] == 0.42
    assert db.rows[0]["auto_apply_status"] == "applied"


def test_applied_returns_false_when_mark_job_applied_fails(monkeypatch):
    _install(monkeypatch, [_row("j1", "running")])
    monkeypatch.setattr(supabase_utils, "mark_job_applied", lambda j: False)
    assert supabase_utils.set_auto_apply_result("j1", "applied", None, 0.1) is False


def test_unknown_status_rejected(monkeypatch):
    db = _install(monkeypatch, [_row("j1", "running")])
    assert supabase_utils.set_auto_apply_result("j1", "bogus", None, None) is False
    assert db.updates == []


def test_stale_running_rows_reset(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    old = (now - datetime.timedelta(minutes=120)).isoformat()
    fresh = (now - datetime.timedelta(minutes=1)).isoformat()
    db = _install(monkeypatch, [_row("old", "running", ts=old), _row("new", "running", ts=fresh)])
    assert supabase_utils.reset_stale_auto_apply(30) == 1
    by_id = {r["job_id"]: r for r in db.rows}
    assert by_id["old"]["auto_apply_status"] == "failed"
    assert by_id["old"]["auto_apply_reason"] == "runner stopped before finishing"
    assert by_id["new"]["auto_apply_status"] == "running"


def test_links_survive_missing_auto_apply_columns(monkeypatch):
    db = _install(monkeypatch,
                  [{"job_id": "a", "apply_url": "https://jobs.lever.co/x/1", "ats_type": "lever",
                    "linkedin_apply_mode": None}],
                  fail_select_on="auto_apply_status")
    jobs = supabase_utils.attach_apply_links([{"job_id": "a"}, {"job_id": "b"}])
    assert jobs[0]["apply_url"] == "https://jobs.lever.co/x/1"
    assert all(j["auto_apply_missing"] is True for j in jobs)
    assert db.selects[-1] == supabase_utils.LINK_COLUMNS


def test_attach_apply_links_fills_auto_apply_columns(monkeypatch):
    _install(monkeypatch, [_row("a", "queued", apply_url="https://x", ats_type="lever",
                                auto_apply_reason=None, auto_apply_cost_usd=None)])
    (job,) = supabase_utils.attach_apply_links([{"job_id": "a"}])
    assert job["auto_apply_status"] == "queued" and "auto_apply_missing" not in job


def test_questions_are_verify_rows_with_a_question_newest_first(monkeypatch):
    _install(monkeypatch, [
        _row("a", "verify", ts="2026-01-01T00:00:00+00:00", auto_apply_reason="Salary?"),
        _row("b", "verify", ts="2026-01-02T00:00:00+00:00", auto_apply_reason="Visa?"),
        _row("c", "verify", auto_apply_reason="captcha"),
        _row("d", "failed", auto_apply_reason="why?"),
    ])
    assert [r["job_id"] for r in supabase_utils.get_auto_apply_questions()] == ["b", "a"]


def test_queued_listing_oldest_first(monkeypatch):
    _install(monkeypatch, [_row("a", "queued", ts="2026-01-02T00:00:00+00:00"),
                           _row("b", "queued", ts="2026-01-01T00:00:00+00:00"),
                           _row("c", "running")])
    assert [r["job_id"] for r in supabase_utils.get_queued_auto_apply()] == ["b", "a"]


def test_helpers_are_non_fatal_on_errors(monkeypatch):
    def boom(name):
        raise Exception("down")
    monkeypatch.setattr(supabase_utils.supabase, "table", boom)
    assert supabase_utils.queue_auto_apply("a") is False
    assert supabase_utils.claim_next_auto_apply("w") is None
    assert supabase_utils.set_auto_apply_result("a", "failed", "x", None) is False
    assert supabase_utils.reset_stale_auto_apply(5) == 0
    assert supabase_utils.get_auto_apply_questions() == []
    assert supabase_utils.get_queued_auto_apply() == []


def test_get_job_for_auto_apply_selects_apply_fields(monkeypatch):
    db = _install(monkeypatch, [_row("a", "queued", apply_url="https://x/apply", ats_type="lever")])
    job = supabase_utils.get_job_for_auto_apply("a")
    assert job["apply_url"] == "https://x/apply"
    for col in ("description", "apply_url", "ats_type", "why_me_pitch", "score_breakdown",
                "auto_apply_status"):
        assert col in db.selects[-1]
    assert supabase_utils.get_job_for_auto_apply("missing") is None

    def boom(name):
        raise Exception("down")
    monkeypatch.setattr(supabase_utils.supabase, "table", boom)
    assert supabase_utils.get_job_for_auto_apply("a") is None


def test_none_cost_is_not_written(monkeypatch):
    db = _install(monkeypatch, [_row("j1", "ready_to_submit", auto_apply_cost_usd=0.31)])
    assert supabase_utils.set_auto_apply_result("j1", "running", "in review", None) is True
    assert "auto_apply_cost_usd" not in db.updates[-1]
    assert db.rows[0]["auto_apply_cost_usd"] == 0.31


def test_zero_cost_is_written(monkeypatch):
    db = _install(monkeypatch, [_row("j1", "running", auto_apply_cost_usd=0.31)])
    assert supabase_utils.set_auto_apply_result("j1", "failed", "x", 0.0) is True
    assert db.updates[-1]["auto_apply_cost_usd"] == 0.0
