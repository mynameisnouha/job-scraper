"""The HTML dashboard, and the column that decided whether it had any content.

`build_dashboard` shows what was *found today*, so every row it renders is
filtered on `scraped_at`. A query that does not select that column therefore
yields nothing — silently, with a cheerful "written (0 jobs)" in the log. That
is what had happened to the Unscored table.
"""

from datetime import datetime, timedelta, timezone

from db import supabase_utils
from review import dashboard

_TODAY = datetime.now(timezone.utc).isoformat()
_OLD = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()


class TestFoundToday:
    def test_a_row_without_a_scrape_timestamp_is_not_today(self):
        """The behaviour that made the missing column silent rather than loud."""
        assert dashboard.found_today({"job_id": "j1"}) is False

    def test_today_and_not_older(self):
        assert dashboard.found_today({"scraped_at": _TODAY}) is True
        assert dashboard.found_today({"scraped_at": _OLD}) is False


class TestUnscoredJobsCarryTheirScrapeDate:
    def test_the_query_selects_scraped_at(self, monkeypatch):
        """Without it every unscored job fails found_today and the table is empty.

        Asserted on the query itself rather than on the rendered page: the column
        list is the thing that broke, and it is in a different module from the
        filter that depends on it.
        """
        selected = {}

        class FakeQuery:
            def select(self, columns):
                selected["columns"] = columns
                return self

            def eq(self, *a, **k):
                return self

            def is_(self, *a, **k):
                return self

            def in_(self, *a, **k):
                return self

            def or_(self, *a, **k):
                return self

            def order(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                return type("R", (), {"data": [{"job_id": "j1", "scraped_at": _TODAY}]})()

        monkeypatch.setattr(supabase_utils, "supabase",
                            type("C", (), {"table": lambda self, name: FakeQuery()})())
        rows = supabase_utils.get_jobs_to_score(10)
        assert "scraped_at" in selected["columns"]
        # The email alert links each job; without job_url it had no link to draw.
        assert "job_url" in selected["columns"]
        assert dashboard.found_today(rows[0]) is True


class TestUnscoredJobsComeNewestFirst:
    def test_the_query_orders_by_scrape_date_descending(self, monkeypatch):
        """The screen takes the first JOBS_TO_SCREEN_PER_RUN of these rows.

        Oldest-first, a backlog bigger than the cap meant today's finds were never
        scored today, and the dashboard (today's finds only) went stale.
        """
        orders = []

        class FakeQuery:
            def select(self, columns):
                return self

            def eq(self, *a, **k):
                return self

            def is_(self, *a, **k):
                return self

            def in_(self, *a, **k):
                return self

            def or_(self, *a, **k):
                return self

            def order(self, column, desc=False):
                orders.append((column, desc))
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                return type("R", (), {"data": []})()

        monkeypatch.setattr(supabase_utils, "supabase",
                            type("C", (), {"table": lambda self, name: FakeQuery()})())
        supabase_utils.get_jobs_to_score(10)
        assert orders and all(o == ("scraped_at", True) for o in orders)


class TestDeprioritisedSourcesComeLast:
    """No German-language ad has led to an interview; LinkedIn carries the English ones."""

    def _client(self, rows_by_pass, calls):
        class FakeQuery:
            def __init__(self):
                self.pass_ = None

            def select(self, columns):
                return self

            def eq(self, *a, **k):
                return self

            def is_(self, *a, **k):
                return self

            def or_(self, expr):
                self.pass_ = "first"
                calls.append(("first", expr))
                return self

            def in_(self, column, values):
                self.pass_ = "last"
                calls.append(("last", tuple(values)))
                return self

            def order(self, *a, **k):
                return self

            def limit(self, n):
                self.n = n
                return self

            def execute(self):
                return type("R", (), {"data": rows_by_pass[self.pass_][:self.n]})()

        return type("C", (), {"table": lambda self, name: FakeQuery()})()

    def test_other_sources_fill_the_batch_before_arbeitsagentur(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "SCORE_LAST_PROVIDERS", ["arbeitsagentur"])
        calls = []
        rows = {"first": [{"job_id": "li1"}, {"job_id": "li2"}],
                "last": [{"job_id": "aa1"}, {"job_id": "aa2"}, {"job_id": "aa3"}]}
        monkeypatch.setattr(supabase_utils, "supabase", self._client(rows, calls))
        got = [r["job_id"] for r in supabase_utils.get_jobs_to_score(4)]
        assert got == ["li1", "li2", "aa1", "aa2"]
        assert calls == [("first", "provider.is.null,provider.not.in.(arbeitsagentur)"),
                         ("last", ("arbeitsagentur",))]

    def test_a_full_first_pass_skips_the_second_query(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "SCORE_LAST_PROVIDERS", ["arbeitsagentur"])
        calls = []
        rows = {"first": [{"job_id": f"li{i}"} for i in range(5)], "last": [{"job_id": "aa1"}]}
        monkeypatch.setattr(supabase_utils, "supabase", self._client(rows, calls))
        assert len(supabase_utils.get_jobs_to_score(3)) == 3
        assert [c[0] for c in calls] == ["first"]


class TestBuildDashboard:
    def test_it_counts_todays_unscored_jobs(self, monkeypatch, tmp_path):
        """End to end: a job scraped today reaches the page, an old one does not."""
        monkeypatch.setattr(supabase_utils, "get_top_scored_jobs_to_apply",
                            lambda limit: [{"job_id": "j1", "job_title": "ML Engineer",
                                            "company": "ACME", "resume_score": 80,
                                            "scraped_at": _TODAY, "score_breakdown": {}}])
        monkeypatch.setattr(supabase_utils, "get_jobs_to_score",
                            lambda limit: [{"job_id": "j2", "job_title": "Data Scientist",
                                            "company": "Beta", "scraped_at": _TODAY},
                                           {"job_id": "j3", "job_title": "Old One",
                                            "company": "Gamma", "scraped_at": _OLD}])
        monkeypatch.setattr(supabase_utils, "get_applied_jobs", lambda limit: [])
        monkeypatch.setattr(dashboard, "DASHBOARD_HTML_PATH", str(tmp_path / "dashboard.html"))

        dashboard.build_dashboard()
        page = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert "ML Engineer" in page
        assert "Old One" not in page, "found-today is still enforced"
        # The unscored count is the stat that read 0 for every run.
        assert '<div class="num">1</div><div class="label">Unscored</div>' in page


class TestGracePeriodFilter:
    """With local scoring on, CI only takes jobs scraped before the grace period."""

    @staticmethod
    def _install(monkeypatch, recorded):
        class FakeQuery:
            def __getattr__(self, name):
                if name == "lt":
                    return lambda field, value: recorded.append((field, value)) or self
                if name == "execute":
                    return lambda: type("R", (), {"data": []})()
                return lambda *a, **k: self

        monkeypatch.setattr(supabase_utils, "supabase",
                            type("C", (), {"table": lambda self, name: FakeQuery()})())

    def test_get_jobs_to_score_filters_on_age(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        recorded = []
        self._install(monkeypatch, recorded)
        supabase_utils.get_jobs_to_score(10, older_than_hours=24)
        assert recorded and all(field == "scraped_at" for field, _ in recorded)
        cutoff = datetime.fromisoformat(recorded[0][1])
        expected = datetime.now(timezone.utc) - timedelta(hours=24)
        assert abs((cutoff - expected).total_seconds()) < 300

    def test_no_age_no_filter(self, monkeypatch):
        recorded = []
        self._install(monkeypatch, recorded)
        supabase_utils.get_jobs_to_score(10)
        assert recorded == []
