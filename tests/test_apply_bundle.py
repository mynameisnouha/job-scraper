import json
import os

import pytest

import config
from apply import bundle
from review import application_pack
from tailor import store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cv = tmp_path / "me.pdf"
    cv.write_bytes(b"%PDF cv")
    monkeypatch.setattr(application_pack, "choose_cv", lambda title: str(cv))
    monkeypatch.setattr(application_pack, "load_answers", lambda path=None: {"name": "N"})
    monkeypatch.setattr(store, "load", lambda job_id: None)
    monkeypatch.setattr(config, "GENERIC_COVER_LETTER_PATH", None)
    return tmp_path


def make_job(**kw):
    job = {"job_id": "j1", "job_title": "Dev", "company": "Acme",
           "apply_url": "https://x/apply", "ats_type": "lever",
           "description": "Do things", "why_me_pitch": "pitch",
           "score_breakdown": {"differentiators": ["a", "b"]}}
    job.update(kw)
    return job


def read_job(d):
    with open(os.path.join(d, "job.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_bundle_has_exactly_the_expected_files(setup):
    d = bundle.build(make_job(), str(setup / "work"), env={})
    assert set(os.listdir(d)) == {"job.json", "answers.json", "cv.pdf", "playbook.md"}
    data = read_job(d)
    assert data["lead_with"] == ["a", "b"] and data["pitch"] == "pitch"
    assert os.path.isabs(data["files"]["cv"]) and os.path.isfile(data["files"]["cv"])
    assert data["account"] == {"email": None, "password": None}


def test_auto_submit_flag_follows_the_ats(setup):
    w = str(setup / "work")
    assert read_job(bundle.build(make_job(ats_type="lever"), w, env={}))["auto_submit"] is True
    assert read_job(bundle.build(make_job(ats_type="workday"), w, env={}))["auto_submit"] is False
    forced = bundle.build(make_job(ats_type="lever"), w, env={}, auto_submit=False)
    assert read_job(forced)["auto_submit"] is False


def test_tailored_letter_written_when_present(setup, monkeypatch):
    monkeypatch.setattr(store, "load", lambda job_id: {"cover_letter_text": "Dear Acme"})
    data = read_job(bundle.build(make_job(), str(setup / "work"), env={}))
    assert os.path.isabs(data["files"]["cover_letter"])
    with open(data["files"]["cover_letter"], encoding="utf-8") as fh:
        assert fh.read() == "Dear Acme"


def test_no_letter_when_absent(setup):
    data = read_job(bundle.build(make_job(), str(setup / "work"), env={}))
    assert data["files"]["cover_letter"] is None
    assert data["files"]["generic_cover_letter"] is None


def test_generic_letter_copied(setup, monkeypatch):
    g = setup / "gen.docx"
    g.write_bytes(b"x")
    monkeypatch.setattr(config, "GENERIC_COVER_LETTER_PATH", str(g))
    data = read_job(bundle.build(make_job(), str(setup / "work"), env={}))
    assert data["files"]["generic_cover_letter"].endswith("generic_cover_letter.docx")
    assert os.path.isfile(data["files"]["generic_cover_letter"])


def test_no_repo_secrets_in_bundle(setup):
    env = {"SUPABASE_KEY": "s3cret", "ATS_EMAIL": "a@b.c", "ATS_PASSWORD": "pw"}
    d = bundle.build(make_job(), str(setup / "work"), env=env)
    for root, _, files in os.walk(d):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert b"s3cret" not in fh.read()
    assert read_job(d)["account"] == {"email": "a@b.c", "password": "pw"}


def test_rebuild_removes_stale_files(setup):
    w = str(setup / "work")
    d = bundle.build(make_job(), w, env={})
    open(os.path.join(d, "stale.txt"), "w").close()
    bundle.build(make_job(), w, env={})
    assert not os.path.exists(os.path.join(d, "stale.txt"))


def test_missing_cv_raises_bundle_error(setup, monkeypatch):
    monkeypatch.setattr(application_pack, "choose_cv", lambda t: str(setup / "nope.pdf"))
    with pytest.raises(bundle.BundleError):
        bundle.build(make_job(), str(setup / "work"), env={})


def test_missing_description_raises_bundle_error(setup):
    for desc in (None, "", "  "):
        with pytest.raises(bundle.BundleError, match="description"):
            bundle.build(make_job(description=desc), str(setup / "work"), env={})


@pytest.mark.parametrize("bad", ["", None, ".", "..", "../x", "a/b", "a\\b", "..\\..\\x"])
def test_bad_job_id_never_deletes_outside(setup, bad):
    work = setup / "work"
    work.mkdir()
    keep = work / "keep.txt"
    keep.write_text("k")
    with pytest.raises(bundle.BundleError):
        bundle.build(make_job(job_id=bad), str(work), env={})
    assert keep.exists() and (setup / "me.pdf").exists()


def test_uploaded_cv_and_letter_are_the_fallback(setup, monkeypatch):
    from apply import documents
    monkeypatch.setattr(application_pack, "choose_cv", lambda title: None)
    documents.save_upload("cv", "mine.pdf", b"%PDF uploaded")
    documents.save_upload("cover_letter", "letter.docx", b"PK letter")
    out = bundle.build(make_job(), str(setup / "work"))
    data = json.load(open(os.path.join(out, "job.json"), encoding="utf-8"))
    assert open(data["files"]["cv"], "rb").read() == b"%PDF uploaded"
    assert data["files"]["generic_cover_letter"].endswith("generic_cover_letter.docx")
    assert open(data["files"]["generic_cover_letter"], "rb").read() == b"PK letter"


def test_start_url_falls_back_to_the_posting():
    assert bundle.start_url({"apply_url": "https://a/apply", "job_url": "https://b"}) == "https://a/apply"
    assert bundle.start_url({"apply_url": None, "job_url": "https://b"}) == "https://b"
    assert bundle.start_url({}) is None


def test_job_board_start_never_auto_submits(setup):
    job = make_job(apply_url=None, ats_type="greenhouse",
                   job_url="https://www.linkedin.com/jobs/view/123/")
    out = bundle.build(job, str(setup / "work"))
    data = json.load(open(os.path.join(out, "job.json"), encoding="utf-8"))
    assert data["apply_url"] == "https://www.linkedin.com/jobs/view/123/"
    assert data["via_job_board"] is True
    assert data["auto_submit"] is False


def test_employer_form_start_is_not_a_job_board(setup):
    out = bundle.build(make_job(), str(setup / "work"))
    data = json.load(open(os.path.join(out, "job.json"), encoding="utf-8"))
    assert data["via_job_board"] is False
