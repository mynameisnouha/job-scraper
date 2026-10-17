"""Finding a LinkedIn job's employer link in the owner's logged-in browser.

The browser is faked: these tests pin what the resolver reads, what it clicks and,
above all, what it never clicks (Easy Apply, anything that submits).
"""
import pytest

from apply import resolve_link

LEVER = "https://jobs.lever.co/acme/1"
SAFETY = ("https://www.linkedin.com/safety/go?url=https%3A%2F%2Fjobs.lever.co%2Facme%2F1"
          "&trk=flagship&urlhash=abc")


class FakePopup:
    def __init__(self, url):
        self.url = url
        self.closed = False

    def wait_for_load_state(self, *a, **k):
        pass

    def close(self):
        self.closed = True


class FakeExpect:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def value(self):
        if self.page.popup is None:
            raise TimeoutError("no popup")
        return self.page.popup


class FakeContext:
    def __init__(self, page):
        self.page = page

    def expect_page(self, timeout=None):
        return FakeExpect(self.page)


class FakeButtons:
    def __init__(self, page):
        self.page = page

    def nth(self, i):
        page = self.page

        class Button:
            def click(self, *a, **k):
                page.clicked.append(page.buttons[i])
        return Button()


class FakePage:
    def __init__(self, anchors=(), buttons=(), popup_url=None, landed=None, guest=False):
        self.guest = guest
        self.anchors = list(anchors)
        self.buttons = list(buttons)
        self.popup = FakePopup(popup_url) if popup_url else None
        self.landed = landed
        self.url = None
        self.clicked = []
        self.context = FakeContext(self)

    def goto(self, url, **kwargs):
        self.url = self.landed or url

    def wait_for_timeout(self, ms):
        pass

    def eval_on_selector_all(self, selector, script):
        if "public_jobs" in selector:
            return [1] if self.guest else []
        if "safety" in selector:
            return self.anchors
        return [{"i": i, "text": text} for i, text in enumerate(self.buttons)]

    def locator(self, selector):
        return FakeButtons(self)


@pytest.mark.parametrize("href, expected", [
    (SAFETY, LEVER),
    ("https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fwww.linkedin.com%2Fjobs", None),
    ("https://www.linkedin.com/safety/go?trk=x", None),
    ("https://www.linkedin.com/safety/go?url=javascript%3Aalert(1)", None),
])
def test_decode_safety_link(href, expected):
    assert resolve_link.decode_safety_link(href) == expected


def test_linkedin_job_url_from_a_stored_job():
    assert resolve_link.linkedin_job_url({"job_url": "https://www.linkedin.com/jobs/view/4430986464/"}) \
        == "https://www.linkedin.com/jobs/view/4430986464/"
    assert resolve_link.linkedin_job_url({"job_id": "4430986464", "provider": "linkedin"}) \
        == "https://www.linkedin.com/jobs/view/4430986464/"
    assert resolve_link.linkedin_job_url({"job_url": "https://x.de/1", "provider": "xing"}) is None


def test_an_external_link_on_the_page_is_read_without_clicking():
    page = FakePage(anchors=[SAFETY], buttons=["Apply"])
    result = resolve_link.resolve(page, "https://www.linkedin.com/jobs/view/1/")
    assert result == ("found", LEVER)
    assert page.clicked == []


def test_the_external_apply_button_is_clicked_only_to_read_the_new_tab():
    page = FakePage(buttons=["Save", "Apply on company website"], popup_url=LEVER)
    assert resolve_link.resolve(page, "https://www.linkedin.com/jobs/view/1/") == ("found", LEVER)
    assert page.clicked == ["Apply on company website"]
    assert page.popup.closed


@pytest.mark.parametrize("label", ["Easy Apply", "Einfach bewerben"])
def test_easy_apply_is_never_clicked(label):
    page = FakePage(buttons=[label], popup_url=LEVER)
    assert resolve_link.resolve(page, "https://www.linkedin.com/jobs/view/1/") == ("easy_apply", None)
    assert page.clicked == []


@pytest.mark.parametrize("landed", ["https://www.linkedin.com/authwall?trk=x",
                                    "https://www.linkedin.com/login?session_redirect=y",
                                    "https://www.linkedin.com/checkpoint/challenge/z"])
def test_a_login_wall_says_so(landed):
    page = FakePage(anchors=[SAFETY], landed=landed)
    assert resolve_link.resolve(page, "https://www.linkedin.com/jobs/view/1/") == ("not_logged_in", None)


def test_a_new_tab_that_stays_on_linkedin_is_not_an_employer_link():
    page = FakePage(buttons=["Apply"], popup_url="https://www.linkedin.com/jobs/view/1/apply")
    assert resolve_link.resolve(page, "https://www.linkedin.com/jobs/view/1/") == ("not_found", None)


def test_no_apply_control_at_all():
    assert resolve_link.resolve(FakePage(), "https://www.linkedin.com/jobs/view/1/") == ("not_found", None)


class FakeDB:
    def __init__(self, job):
        self.job = job
        self.saved = []

    def get_job(self, job_id):
        return self.job

    def set_apply_url(self, job_id, url, ats_type):
        self.saved.append((job_id, url, ats_type))
        return True


def _run(monkeypatch, job, outcome):
    db = FakeDB(job)
    monkeypatch.setattr(resolve_link, "_load_job", db.get_job)
    monkeypatch.setattr(resolve_link.supabase_utils, "set_apply_url", db.set_apply_url)
    monkeypatch.setattr(resolve_link, "_resolve_in_browser", lambda url, headless: outcome)
    return resolve_link.main([job["job_id"] if job else "missing"]), db


def test_main_saves_a_found_link(monkeypatch, capsys):
    job = {"job_id": "4430986464", "provider": "linkedin",
           "job_url": "https://www.linkedin.com/jobs/view/4430986464/"}
    code, db = _run(monkeypatch, job, ("found", LEVER))
    assert code == 0
    assert db.saved == [("4430986464", LEVER, "lever")]
    assert LEVER in capsys.readouterr().out


@pytest.mark.parametrize("outcome, code", [(("easy_apply", None), 2), (("not_found", None), 2),
                                           (("not_logged_in", None), 3)])
def test_main_saves_nothing_otherwise(monkeypatch, outcome, code):
    job = {"job_id": "1", "provider": "linkedin", "job_url": "https://www.linkedin.com/jobs/view/1/"}
    result, db = _run(monkeypatch, job, outcome)
    assert result == code and db.saved == []


def test_main_refuses_jobs_that_are_not_linkedin(monkeypatch):
    job = {"job_id": "xing_1", "provider": "xing", "job_url": "https://www.xing.com/jobs/x-1"}
    result, db = _run(monkeypatch, job, ("found", LEVER))
    assert result == 1 and db.saved == []


def test_main_unknown_job(monkeypatch):
    monkeypatch.setattr(resolve_link, "_load_job", lambda job_id: None)
    assert resolve_link.main(["nope"]) == 1


def test_the_public_guest_page_means_not_logged_in_and_nothing_is_clicked():
    """Live check 2026-10-17: logged out, LinkedIn serves the public page with no
    redirect, and its Apply button only opens a sign-in box."""
    page = FakePage(buttons=["Apply"], guest=True)
    assert resolve_link.resolve(page, "https://www.linkedin.com/jobs/view/1/") == ("not_logged_in", None)
    assert page.clicked == []
