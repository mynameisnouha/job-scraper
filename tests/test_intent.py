"""Which career-feed postings are worth storing: on-topic title, German location."""
import pytest

from sources import intent


@pytest.mark.parametrize("title", [
    "Senior Data Scientist (m/w/d)",
    "Machine Learning Engineer",
    "KI-Entwickler",
    "AI Engineer - Platform",
    "Werkstudent ML Ops",
])
def test_on_topic_titles_match(title):
    assert intent.matches_search_intent(title) is True


@pytest.mark.parametrize("title", ["Account Executive", "Sales Manager DACH", "Mail Clerk", "", None])
def test_off_topic_titles_do_not_match(title):
    """"Mail" contains "ai" — a substring match would keep it."""
    assert intent.matches_search_intent(title) is False


@pytest.mark.parametrize("location", [
    "Berlin, Germany", "München", "Deutschland", "Hamburg, DE", "Remote - Germany",
    "Remote (EU)", "Remote, EMEA", "Remote", "", None, "Berlin, Germany; London, UK",
])
def test_german_or_open_remote_locations_are_kept(location):
    assert intent.in_target_location(location) is True


@pytest.mark.parametrize("location", ["New York, NY", "London, UK", "Remote - US", "Bangalore, India"])
def test_foreign_locations_are_dropped(location):
    assert intent.in_target_location(location) is False
