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
