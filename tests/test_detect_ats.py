"""Finding which ATS feed a company publishes. No network: adapters are faked."""
from datetime import datetime, timedelta, timezone

import pytest

from maintenance import detect_ats


@pytest.mark.parametrize("company, expected", [
    ("Zalando SE", "zalando"),
    ("N26 GmbH", "n26"),
    ("Deutsche Bahn AG", "deutschebahn"),
    ("Deutsche Bahn AG", "deutsche-bahn"),
])
def test_slug_candidates_contain_the_obvious_slug(company, expected):
    assert expected in detect_ats.slug_candidates(company)


def test_single_word_company_has_one_candidate():
    assert detect_ats.slug_candidates("Celonis") == ["celonis"]


def test_candidates_are_unique_and_capped():
    slugs = detect_ats.slug_candidates("Very Long Company Name Holding GmbH & Co. KG")
    assert len(slugs) == len(set(slugs)) <= 4


def test_empty_company_has_no_candidates():
    assert detect_ats.slug_candidates("") == []
    assert detect_ats.slug_candidates(None) == []


class FakeAdapter:
    def __init__(self, ats_type, live_slugs):
        self.ats_type = ats_type
        self.live = live_slugs
        self.asked = []

    def fetch(self, slug, outcome=None):
        self.asked.append(slug)
        return [{"id": 1}] if slug in self.live else []


def test_probe_returns_the_first_feed_with_postings():
    adapters = {"greenhouse": FakeAdapter("greenhouse", set()),
                "lever": FakeAdapter("lever", {"acme"})}
    assert detect_ats.probe("Acme GmbH", adapters=adapters) == ("lever", "acme")


def test_probe_finds_nothing():
    adapters = {"lever": FakeAdapter("lever", set())}
    assert detect_ats.probe("Acme GmbH", adapters=adapters) is None


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_main_dry_run_skips_recent_probes_and_writes_nothing(monkeypatch, tmp_path):
    rows = [
        {"name": "Recent", "name_normalized": "recent", "ats_type": None, "last_probe_at": _ago(10)},
        {"name": "Stale", "name_normalized": "stale", "ats_type": None, "last_probe_at": _ago(40)},
        {"name": "Known", "name_normalized": "known", "ats_type": "lever", "last_probe_at": _ago(40)},
    ]
    probed, writes = [], []
    monkeypatch.setattr(detect_ats.supabase_utils, "get_all_companies", lambda: rows)
    monkeypatch.setattr(detect_ats.supabase_utils, "get_distinct_job_companies", lambda: ["Recent", "Stale", "Known"])
    monkeypatch.setattr(detect_ats.supabase_utils, "upsert_companies", lambda r: writes.append(r) or len(r))
    monkeypatch.setattr(detect_ats.supabase_utils, "record_probe", lambda *a: writes.append(a) or True)
    monkeypatch.setattr(detect_ats, "probe", lambda name, adapters=None: probed.append(name) or None)
    monkeypatch.setattr(detect_ats, "TARGETS_PATH", str(tmp_path / "none.json"))

    assert detect_ats.main(["--dry-run"]) == 0
    assert probed == ["Stale"]
    assert writes == []


def test_main_saves_hits_misses_and_explicit_targets(monkeypatch, tmp_path):
    targets = tmp_path / "targets.json"
    targets.write_text('[{"name": "Pinned GmbH", "ats_type": "personio", "ats_slug": "pinned"},'
                       ' {"name": "Guess AG"}]', encoding="utf-8")
    rows = []
    recorded, upserted = [], []
    monkeypatch.setattr(detect_ats.supabase_utils, "get_all_companies", lambda: rows)
    monkeypatch.setattr(detect_ats.supabase_utils, "get_distinct_job_companies", lambda: ["Found SE"])
    monkeypatch.setattr(detect_ats.supabase_utils, "upsert_companies", lambda r: upserted.extend(r) or len(r))
    monkeypatch.setattr(detect_ats.supabase_utils, "record_probe", lambda *a: recorded.append(a) or True)
    monkeypatch.setattr(detect_ats, "probe",
                        lambda name, adapters=None: ("greenhouse", "found") if name == "Found SE" else None)
    monkeypatch.setattr(detect_ats, "TARGETS_PATH", str(targets))
    monkeypatch.setattr(detect_ats.time, "sleep", lambda s: None)

    assert detect_ats.main([]) == 0
    assert {r["name_normalized"] for r in upserted} == {"found", "pinned", "guess"}
    assert ("pinned", "personio", "pinned", "manual") in recorded
    assert ("found", "greenhouse", "found", "found") in recorded
    assert ("guess", None, None, "not_found") in recorded


@pytest.mark.parametrize("company", ["1&1", "2501.ai", "A B"])
def test_slugs_without_letters_or_too_short_are_never_guessed(company):
    """Dry run 2026-10-17: '1&1' matched workable:'1-1' and '2501.ai' matched
    ashby:'2501' — short or digit-only slugs collide with other companies' feeds."""
    for slug in detect_ats.slug_candidates(company):
        assert len(slug.replace("-", "")) >= 3
        assert any(ch.isalpha() for ch in slug)


def test_main_survives_a_console_that_cannot_encode_the_name(monkeypatch, tmp_path):
    """Windows consoles default to cp1252; a company name outside it crashed the run."""
    import io
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(detect_ats.sys, "stdout", cp1252)
    monkeypatch.setattr(detect_ats.supabase_utils, "get_all_companies", lambda: [])
    monkeypatch.setattr(detect_ats.supabase_utils, "get_distinct_job_companies", lambda: ["株式会社 Data"])
    monkeypatch.setattr(detect_ats, "probe", lambda name, adapters=None: None)
    monkeypatch.setattr(detect_ats, "TARGETS_PATH", str(tmp_path / "none.json"))
    assert detect_ats.main(["--dry-run"]) == 0
