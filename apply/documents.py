"""The generic CV and cover letter the auto-apply worker falls back on.

Uploaded once on the Set up page rather than named in config.py: the files are
personal and this repository is public, so both the files (cv/) and the record of
which ones they are (apply_documents.json) stay untracked. A tailored CV or letter
for a job, when one exists, still wins over these.
"""

import json
import logging
import os
from typing import Dict, Optional

SETTINGS_PATH = "apply_documents.json"
DOCUMENTS_DIR = "cv"

# A CV goes out as a PDF so it renders the same in every ATS; a letter may also
# be a Word or text file, since some forms only offer a text box to paste into.
ALLOWED = {
    "cv": (".pdf",),
    "cover_letter": (".pdf", ".docx", ".txt"),
}
_STEM = {"cv": "generic_cv", "cover_letter": "generic_cover_letter"}


def _load() -> Dict[str, str]:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logging.warning(f"Ignoring unreadable {SETTINGS_PATH}: {e}")
        return {}
    return saved if isinstance(saved, dict) else {}


def _existing(kind: str) -> Optional[str]:
    path = _load().get(kind)
    return path if path and os.path.isfile(path) else None


def generic_cv() -> Optional[str]:
    """The uploaded generic CV, or None when none was uploaded or it was deleted."""
    return _existing("cv")


def generic_cover_letter() -> Optional[str]:
    return _existing("cover_letter")


def display_name(kind: str) -> Optional[str]:
    """The file name as uploaded, for showing which one is in use."""
    return _load().get(f"{kind}_name") if _existing(kind) else None


def save_upload(kind: str, filename: str, data: bytes) -> str:
    """Store an uploaded document under DOCUMENTS_DIR and remember its path."""
    if kind not in ALLOWED:
        raise ValueError(f"Unknown document kind: {kind}")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED[kind]:
        raise ValueError(f"Use a {' / '.join(ALLOWED[kind])} file for this.")
    os.makedirs(DOCUMENTS_DIR, exist_ok=True)
    path = os.path.join(DOCUMENTS_DIR, _STEM[kind] + ext)
    previous = _existing(kind)
    with open(path, "wb") as fh:
        fh.write(data)
    if previous and os.path.abspath(previous) != os.path.abspath(path):
        os.remove(previous)
    saved = _load()
    saved[kind] = path
    saved[f"{kind}_name"] = filename
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(saved, fh, indent=2)
    return path
