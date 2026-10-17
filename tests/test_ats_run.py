"""The `ats` source: poll every company's feed, filter, dedup, save, account."""
import pytest

from sources import dedup, scrape_guard
from sources.ats import run


class FakeAdapter:
    """Raw postings are dicts with title/location/id; normalize builds a record."""

    def __init__(self, ats_type, feeds, status=None):
        self.ats_type = ats_type
        self.feeds = feeds          # slug -> list of raw postings
        self.status = status or {}  # slug -> status code for an empty feed
        self.enriched = []

    def fetch(self, slug, outcome=None):
        raws = self.feeds.get(slug, [])
        if outcome is not None:
            outcome.record_attempt(f"{self.ats_type}:{slug}",
                                   status_code=self.status.get(slug, 200), items_parsed=len(raws))
        return raws

    def id_of(self, raw):
        return raw["id"]

    def title_of(self, raw):
        return raw["title"]

    def location_of(self, raw):
        return raw["location"]

    def enrich(self, raw, slug, outcome=None):
        self.enriched.append(raw["id"])
        return None if raw.get("detail_fails") else raw

    def normalize(self, raw, slug, company):
        if raw.get("no_description"):
            return None
        return {"job_id": f"ats_{self.ats_type}_{slug}_{raw['id']}", "job_title": raw["title"],
                "company": company, "location": raw["location"], "level": None,
                "description": raw.get("description", "Build models."), "provider": "ats",
                "posted_at": None, "job_url": f"https://x/{raw['id']}",
                "apply_url": f"https://x/{raw['id']}", "ats_type": self.ats_type}


GERMAN_DATA = {"id": "1", "title": "Data Scientist", "location": "Berlin, Germany"}
US_DATA = {"id": "2", "title": "Data Engineer", "location": "New York, NY"}
GERMAN_SALES = {"id": "3", "title": "Account Executive", "location": "Munich, Germany"}


@pytest.fixture
def env(monkeypatch):
    state = {"companies": [], "existing_ids": set(), "existing_keys": set(),
             "saved": [], "polled": [], "save_result": None}
    monkeypatch.setattr(run.supabase_utils, "get_companies_to_poll", lambda: state["companies"])
    monkeypatch.setattr(run.supabase_utils, "get_existing_jobs_from_supabase",
                        lambda: (state["existing_ids"], state["existing_keys"]))

    def save(records):
        state["saved"].extend(records)
        return len(records) if state["save_result"] is None else state["save_result"]
    monkeypatch.setattr(run.supabase_utils, "save_jobs_to_supabase", save)
    monkeypatch.setattr(run.supabase_utils, "mark_polled", lambda cid: state["polled"].append(cid) or True)
    monkeypatch.setattr(run.supabase_utils, "get_jobs_missing_apply_url_by_key", lambda: {})
    monkeypatch.setattr(run.supabase_utils, "set_apply_url", lambda *a: True)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    return state


def _use(monkeypatch, *adapters):
    monkeypatch.setattr(run, "ADAPTERS", {a.ats_type: a for a in adapters})


def test_no_companies_to_poll_is_a_broken_source(env, monkeypatch):
    _use(monkeypatch, FakeAdapter("lever", {}))
    outcome = run.run_ats()
    assert outcome.source == "ats"
    assert outcome.fetched == 0
    assert outcome.status == scrape_guard.BROKEN


def test_one_dead_feed_does_not_stop_the_others(env, monkeypatch):
    env["companies"] = [
        {"id": 1, "name": "Gone GmbH", "ats_type": "lever", "ats_slug": "gone"},
        {"id": 2, "name": "Acme GmbH", "ats_type": "lever", "ats_slug": "acme"},
    ]
    _use(monkeypatch, FakeAdapter("lever", {"acme": [GERMAN_DATA, US_DATA, GERMAN_SALES]},
                                  status={"gone": 404}))
    outcome = run.run_ats()
    assert outcome.fetched == 3
    assert outcome.new == 1
    assert outcome.filtered_out == {"off_target_title": 1, "off_target_location": 1}
    assert outcome.unaccounted == 0
    assert [a.status_code for a in outcome.attempts] == [404, 200]
    assert env["polled"] == [1, 2]
    (saved,) = env["saved"]
    assert saved["company_id"] == 2
    assert saved["job_title"] == "Data Scientist"
    assert "program_type" in saved


def test_filters_run_before_the_detail_call(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]
    adapter = FakeAdapter("lever", {"acme": [GERMAN_DATA, US_DATA, GERMAN_SALES]})
    _use(monkeypatch, adapter)
    run.run_ats()
    assert adapter.enriched == ["1"]


def test_a_stored_job_id_counts_as_already_in_db(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]
    env["existing_ids"] = {"ats_lever_acme_1"}
    adapter = FakeAdapter("lever", {"acme": [GERMAN_DATA]})
    _use(monkeypatch, adapter)
    outcome = run.run_ats()
    assert outcome.already_in_db == 1 and outcome.new == 0 and outcome.unaccounted == 0
    assert adapter.enriched == []


def test_same_company_and_title_from_another_source_is_a_repost(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme GmbH", "ats_type": "lever", "ats_slug": "acme"}]
    env["existing_keys"] = {dedup.identity_key("Acme", "Data Scientist", "Berlin, Germany")}
    _use(monkeypatch, FakeAdapter("lever", {"acme": [GERMAN_DATA]}))
    outcome = run.run_ats()
    assert outcome.filtered_out == {"repost_same_company_title": 1}
    assert env["saved"] == []


def test_internship_freelance_detail_and_description_failures_are_accounted(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]
    raws = [
        {"id": "10", "title": "Praktikum Data Science", "location": "Berlin"},
        {"id": "11", "title": "Freelance Data Engineer", "location": "Berlin"},
        {"id": "12", "title": "Data Analyst", "location": "Berlin", "detail_fails": True},
        {"id": "13", "title": "ML Engineer", "location": "Berlin", "no_description": True},
    ]
    _use(monkeypatch, FakeAdapter("lever", {"acme": raws}))
    outcome = run.run_ats()
    assert outcome.filtered_out == {"internship": 1, "freelance": 1,
                                    "detail_fetch_failed": 1, "no_description": 1}
    assert outcome.unaccounted == 0


def test_a_failed_save_is_counted(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]
    env["save_result"] = 0
    _use(monkeypatch, FakeAdapter("lever", {"acme": [GERMAN_DATA]}))
    outcome = run.run_ats()
    assert outcome.new == 0
    assert outcome.filtered_out == {scrape_guard.SAVE_FAILED: 1}
    assert outcome.unaccounted == 0


def test_two_copies_in_one_run_collapse(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme", "ats_type": "lever", "ats_slug": "acme"}]
    twin = dict(GERMAN_DATA, id="99")
    _use(monkeypatch, FakeAdapter("lever", {"acme": [GERMAN_DATA, twin]}))
    outcome = run.run_ats()
    assert outcome.new == 1
    assert outcome.filtered_out == {"repost_same_company_title": 1}


def test_unknown_ats_type_is_skipped(env, monkeypatch):
    env["companies"] = [{"id": 2, "name": "Acme", "ats_type": "taleo", "ats_slug": "acme"}]
    _use(monkeypatch, FakeAdapter("lever", {}))
    outcome = run.run_ats()
    assert outcome.fetched == 0


def test_runner_is_registered():
    from sources import scraper
    import config
    assert "ats" in scraper.SOURCE_RUNNERS
    assert "ats" in config.SCRAPING_SOURCES
    assert dedup.SOURCE_PRIORITY == {"ats": 0, "arbeitsagentur": 1, "xing": 2,
                                     "stepstone": 3, "linkedin": 4}


# --- Task 9b: a feed fills in the employer link of a job stored from elsewhere ---

@pytest.fixture
def missing(monkeypatch, env):
    state = {"missing": {}, "set": []}
    monkeypatch.setattr(run.supabase_utils, "get_jobs_missing_apply_url_by_key", lambda: state["missing"])
    monkeypatch.setattr(run.supabase_utils, "set_apply_url",
                        lambda job_id, url, ats: state["set"].append((job_id, url, ats)) or True)
    return state


def test_a_repost_gives_the_stored_job_its_apply_url(env, missing, monkeypatch):
    key = dedup.identity_key("Acme GmbH", "Data Scientist", "Berlin, Germany")
    env["companies"] = [{"id": 2, "name": "Acme GmbH", "ats_type": "lever", "ats_slug": "acme"}]
    env["existing_keys"] = {key}
    missing["missing"] = {key: "linkedin_4430986464"}
    _use(monkeypatch, FakeAdapter("lever", {"acme": [GERMAN_DATA]}))
    outcome = run.run_ats()
    assert missing["set"] == [("linkedin_4430986464", "https://x/1", "lever")]
    assert outcome.filtered_out == {"repost_same_company_title": 1}
    assert env["saved"] == []


def test_a_repost_whose_stored_job_already_has_a_link_is_left_alone(env, missing, monkeypatch):
    key = dedup.identity_key("Acme GmbH", "Data Scientist", "Berlin, Germany")
    env["companies"] = [{"id": 2, "name": "Acme GmbH", "ats_type": "lever", "ats_slug": "acme"}]
    env["existing_keys"] = {key}
    missing["missing"] = {}
    adapter = FakeAdapter("lever", {"acme": [GERMAN_DATA]})
    _use(monkeypatch, adapter)
    run.run_ats()
    assert missing["set"] == []
    assert adapter.enriched == []
