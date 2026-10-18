"""The generic CV and cover letter the owner uploads on the Set up page."""
import json
import os

import pytest

from apply import documents


@pytest.fixture
def docs(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "SETTINGS_PATH", str(tmp_path / "apply_documents.json"))
    monkeypatch.setattr(documents, "DOCUMENTS_DIR", str(tmp_path / "cv"))
    return tmp_path


def test_nothing_saved_means_no_documents(docs):
    assert documents.generic_cv() is None
    assert documents.generic_cover_letter() is None


def test_saved_cv_is_returned_and_recorded(docs):
    path = documents.save_upload("cv", "Nouha CV 2026.pdf", b"%PDF-1.4 x")
    assert path == os.path.join(str(docs / "cv"), "generic_cv.pdf")
    assert open(path, "rb").read() == b"%PDF-1.4 x"
    assert documents.generic_cv() == path
    saved = json.load(open(docs / "apply_documents.json", encoding="utf-8"))
    assert saved == {"cv": path, "cv_name": "Nouha CV 2026.pdf"}


def test_cover_letter_keeps_its_extension(docs):
    path = documents.save_upload("cover_letter", "Anschreiben.docx", b"PK..")
    assert path.endswith("generic_cover_letter.docx")
    assert documents.generic_cover_letter() == path


def test_replacing_a_letter_with_another_type_removes_the_old_file(docs):
    old = documents.save_upload("cover_letter", "a.docx", b"PK..")
    new = documents.save_upload("cover_letter", "b.pdf", b"%PDF")
    assert not os.path.exists(old)
    assert documents.generic_cover_letter() == new


@pytest.mark.parametrize("kind,name", [("cv", "cv.docx"), ("cv", "cv.exe"),
                                       ("cover_letter", "letter.exe"), ("photo", "a.pdf")])
def test_unsupported_uploads_are_refused(docs, kind, name):
    with pytest.raises(ValueError):
        documents.save_upload(kind, name, b"x")
    assert documents.generic_cv() is None


def test_a_recorded_file_that_was_deleted_counts_as_missing(docs):
    path = documents.save_upload("cv", "cv.pdf", b"%PDF")
    os.remove(path)
    assert documents.generic_cv() is None


def test_unreadable_settings_mean_nothing_saved(docs):
    (docs / "apply_documents.json").write_text("{not json", encoding="utf-8")
    assert documents.generic_cv() is None


def test_display_name_is_the_uploaded_name(docs):
    documents.save_upload("cv", "My CV.pdf", b"%PDF")
    assert documents.display_name("cv") == "My CV.pdf"
    assert documents.display_name("cover_letter") is None
