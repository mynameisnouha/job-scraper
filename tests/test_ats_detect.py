"""URL -> ATS type. Pure string work, no network."""
import pytest

from sources import ats_detect


@pytest.mark.parametrize("url, expected", [
    ("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/X", "workday"),
    ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse"),
    ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse"),
    ("https://jobs.ashbyhq.com/acme/uuid", "ashby"),
    ("https://jobs.lever.co/acme/uuid", "lever"),
    ("https://career5.successfactors.eu/sfcareer/jobreqcareer?jobId=1&company=acme", "successfactors"),
    ("https://careers-acme.icims.com/jobs/1/job", "icims"),
    ("https://acme.teamtailor.com/jobs/1", "teamtailor"),
    ("https://join.com/companies/acme/1", "join"),
    ("https://acme.jobs.personio.de/job/1", "personio"),
    ("https://acme.jobs.personio.com/job/1", "personio"),
    ("https://jobs.smartrecruiters.com/Acme/1", "smartrecruiters"),
    ("https://acme.recruitee.com/o/x", "recruitee"),
    ("https://apply.workable.com/acme/j/1", "workable"),
    ("https://acme.de/karriere/data-scientist", "other"),
])
def test_known_hosts_map_to_their_ats(url, expected):
    assert ats_detect.detect(url) == expected


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/1/",
    "https://www.stepstone.de/stellenangebote--x-1-inline.html",
    "https://www.xing.com/jobs/x-1",
    "https://www.arbeitsagentur.de/jobsuche/jobdetail/1",
    None,
    "",
    "mailto:a@b.de",
])
def test_job_boards_and_non_urls_are_not_an_ats(url):
    """A job board is where the ad lives, not where you apply, so it is never an ATS."""
    assert ats_detect.detect(url) is None


def test_is_job_board():
    assert ats_detect.is_job_board("https://de.linkedin.com/jobs/view/1") is True
    assert ats_detect.is_job_board("https://jobs.lever.co/a/b") is False
    assert ats_detect.is_job_board(None) is False


def test_every_returned_type_is_declared():
    assert "other" in ats_detect.ATS_TYPES
    assert ats_detect.detect("https://jobs.lever.co/a/b") in ats_detect.ATS_TYPES


@pytest.mark.parametrize("url, expected", [
    ("https://job-boards.greenhouse.io/celonis/jobs/123", ("greenhouse", "celonis")),
    ("https://boards.greenhouse.io/embed/job_app?for=n26&token=1", ("greenhouse", "n26")),
    ("https://jobs.lever.co/contentsquare/d872f05c/apply", ("lever", "contentsquare")),
    ("https://jobs.ashbyhq.com/deepl/5d5ed7b1", ("ashby", "deepl")),
    ("https://jobs.smartrecruiters.com/DeutscheGlasfaser/744000153151708-data", ("smartrecruiters", "DeutscheGlasfaser")),
    ("https://apply.workable.com/blueground/j/ABC123/", ("workable", "blueground")),
    ("https://make.recruitee.com/o/delivery-lead-3", ("recruitee", "make")),
    ("https://thermondo.jobs.personio.de/job/123?language=de", ("personio", "thermondo")),
    ("https://ottonova.jobs.personio.com/job/9", ("personio", "ottonova")),
])
def test_slug_from_url_reads_the_exact_feed_name(url, expected):
    assert ats_detect.slug_from_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://turck.wd3.myworkdayjobs.com/de-DE/Turck_Careers/job/x",  # no Workday adapter
    "https://www.henkel.de/karriere/job/1",
    "https://www.linkedin.com/jobs/view/1/",
    "https://jobs.lever.co/",
    "https://www.recruitee.com/",
    None, "",
])
def test_slug_from_url_is_none_without_a_readable_feed_name(url):
    assert ats_detect.slug_from_url(url) is None
