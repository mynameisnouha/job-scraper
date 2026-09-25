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
        assert dashboard.found_today(rows[0]) is True


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
