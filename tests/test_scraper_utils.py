from scraper import convert_html_to_markdown, _get_careers_future_job_company_name


class TestConvertHtmlToMarkdown:
    def test_empty_html(self):
        assert convert_html_to_markdown("") == ""
        assert convert_html_to_markdown(None) == ""
        assert convert_html_to_markdown("   ") == ""

    def test_simple_html(self):
        html = "<p>Hello World</p>"
        result = convert_html_to_markdown(html)
        assert "Hello World" in result

    def test_strip_scripts(self):
        html = "<p>Content</p><script>alert('xss')</script>"
        result = convert_html_to_markdown(html)
        assert "Content" in result
        assert "alert" not in result

    def test_strip_styles(self):
        html = "<p>Text</p><style>body { color: red; }</style>"
        result = convert_html_to_markdown(html)
        assert "Text" in result

    def test_links_preserved(self):
        html = '<a href="https://example.com">Click here</a>'
        result = convert_html_to_markdown(html)
        assert "Click here" in result

    def test_headers(self):
        html = "<h1>Title</h1><h2>Subtitle</h2>"
        result = convert_html_to_markdown(html)
        assert "Title" in result
        assert "Subtitle" in result

    def test_lists(self):
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        result = convert_html_to_markdown(html)
        assert "Item 1" in result
        assert "Item 2" in result


class TestGetCareersFutureCompanyName:
    def test_hiring_company(self):
        job = {"hiringCompany": {"name": "Acme Corp"}}
        assert _get_careers_future_job_company_name(job) == "Acme Corp"

    def test_posted_company_fallback(self):
        job = {"postedCompany": {"name": "Beta Inc"}}
        assert _get_careers_future_job_company_name(job) == "Beta Inc"

    def test_hiring_company_preferred(self):
        job = {
            "hiringCompany": {"name": "Acme Corp"},
            "postedCompany": {"name": "Recruiter Co"},
        }
        assert _get_careers_future_job_company_name(job) == "Acme Corp"

    def test_no_company(self):
        assert _get_careers_future_job_company_name({}) is None
        assert _get_careers_future_job_company_name(None) is None


# --- Zero-yield diagnostics on the LinkedIn fetch path (step A.0) -------------

import pytest
import requests

import scraper
import scrape_guard


def _response(status_code, body):
    """A real requests.Response, so raise_for_status behaves as in production."""
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    response.url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    return response


class TestLinkedInFetchDiagnostics:
    def test_a_block_records_status_and_body_size(self, monkeypatch):
        monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scraper.requests, "get",
                            lambda *a, **k: _response(403, "<html>denied</html>"))

        outcome = scrape_guard.SourceOutcome("linkedin")
        ids = scraper._fetch_linkedin_job_ids("Data Scientist", "Germany", outcome=outcome)

        assert ids == []
        assert outcome.attempts, "a blocked request must still be recorded"
        attempt = outcome.attempts[0]
        assert attempt.status_code == 403
        assert attempt.body_bytes == len("<html>denied</html>")
        assert attempt.items_parsed == 0
        # And that is enough for the guard to fail the run.
        assert outcome.status == scrape_guard.BROKEN
        assert scrape_guard.report([outcome]) == 1

    def test_a_200_with_no_job_cards_records_the_body_size(self, monkeypatch):
        """The case a status code alone cannot explain: served fine, parsed nothing."""
        big_page = "<html>" + ("<div>not a job card</div>" * 500) + "</html>"
        monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _response(200, big_page))

        outcome = scrape_guard.SourceOutcome("linkedin")
        scraper._fetch_linkedin_job_ids("AI Engineer", "Germany", outcome=outcome)

        attempt = outcome.attempts[0]
        assert attempt.status_code == 200
        assert attempt.body_bytes == len(big_page)
        assert attempt.items_parsed == 0

    def test_a_successful_page_records_what_it_parsed(self, monkeypatch):
        card = ('<li><div class="base-card" '
                'data-entity-urn="urn:li:jobPosting:4001"></div></li>')
        monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scraper.requests, "get",
                            lambda *a, **k: _response(200, f"<ul>{card}</ul>"))

        outcome = scrape_guard.SourceOutcome("linkedin")
        ids = scraper._fetch_linkedin_job_ids("MLOps Engineer", "Germany", outcome=outcome)

        assert ids == ["4001"]
        assert outcome.attempts[0].items_parsed == 1

    def test_the_recorder_is_optional(self, monkeypatch):
        """Other callers pass no outcome and must keep working."""
        monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _response(403, "no"))
        assert scraper._fetch_linkedin_job_ids("Data Scientist", "Germany") == []


class TestLinkedInCountersPartitionFetched:
    """Every fetched posting lands in exactly one bucket: already in the DB,
    filtered (with a reason), or saved. `fetched - saved` used to mean all three
    at once, which is unusable for the per-query duplicate analysis in B.1."""

    @pytest.fixture
    def query(self, monkeypatch):
        ids = ["1", "2", "3", "4", "5", "6", "7"]
        monkeypatch.setattr(scraper, "_fetch_linkedin_job_ids",
                            lambda *a, **k: list(ids))
        # "1" and "2" are already stored; "7" is a repost of a stored company/title.
        monkeypatch.setattr(scraper.supabase_utils, "get_existing_jobs_from_supabase",
                            lambda: ({"1", "2"}, {("acme", "ml engineer")}))

        details = {
            "3": {"job_id": "3", "job_title": "Data Scientist", "company": "Beta",
                  "level": "Entry", "description": "Real description."},
            "4": {"job_id": "4", "job_title": "Machine Learning Intern", "company": "Beta",
                  "level": "Internship", "description": "Real description."},
            "5": {"job_id": "5", "job_title": "Freelance NLP Consultant", "company": "Gamma",
                  "level": "Associate", "description": "Real description."},
            "6": {"job_id": "6", "job_title": "AI Engineer", "company": "Delta",
                  "level": "Entry", "description": "   "},
            "7": {"job_id": "7", "job_title": "ML Engineer", "company": "ACME",
                  "level": "Entry", "description": "Real description."},
        }
        monkeypatch.setattr(scraper, "_fetch_linkedin_job_details",
                            lambda job_id: details.get(job_id))
        return scrape_guard.SourceOutcome("linkedin")

    def test_buckets_sum_to_fetched(self, query):
        outcome = query
        saved = scraper.process_linkedin_query("Data Scientist", "Germany", outcome=outcome)
        outcome.record_query(new=len(saved))

        assert outcome.fetched == 7
        assert outcome.new == 1                      # only job "3" survives
        assert outcome.already_in_db == 2            # "1" and "2"
        assert outcome.already_in_db + outcome.filtered_total + outcome.new == outcome.fetched
        assert outcome.unaccounted == 0

    def test_each_drop_is_attributed_to_a_reason(self, query):
        outcome = query
        saved = scraper.process_linkedin_query("Data Scientist", "Germany", outcome=outcome)
        outcome.record_query(new=len(saved))

        assert outcome.filtered_out == {
            "internship": 1,
            "freelance": 1,
            "no_description": 1,
            "repost_same_company_title": 1,
        }

    def test_the_per_query_limit_is_its_own_reason(self, query):
        outcome = query
        saved = scraper.process_linkedin_query("Data Scientist", "Germany", limit=2,
                                               outcome=outcome)
        outcome.record_query(new=len(saved))

        # 5 candidates after dedup, 2 processed, 3 never looked at.
        assert outcome.filtered_out.get("over_per_query_limit") == 3
        assert outcome.unaccounted == 0

    def test_a_failed_detail_fetch_is_counted_separately(self, monkeypatch, query):
        outcome = query
        monkeypatch.setattr(scraper, "_fetch_linkedin_job_details", lambda job_id: None)
        saved = scraper.process_linkedin_query("Data Scientist", "Germany", outcome=outcome)
        outcome.record_query(new=len(saved))

        assert outcome.filtered_out.get("detail_fetch_failed") == 5
        assert outcome.unaccounted == 0
