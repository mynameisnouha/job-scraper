"""ATS playbooks: Markdown instructions the fenced auto-apply worker reads.

The worker is given only `_common.md` plus one ATS file, so each file stays small.
Unknown or missing ATS types fall back to `other.md`.
"""
import os
from typing import Optional

_DIR = os.path.dirname(os.path.abspath(__file__))

ALL_ATS: tuple = tuple(
    sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(_DIR)
        if f.endswith(".md") and not f.startswith("_")
    )
)


def _path(name: str) -> str:
    """Absolute path of `<name>.md` beside this module."""
    return os.path.join(_DIR, f"{name}.md")


def paths_for(ats_type: Optional[str]) -> list:
    """[_common.md, <ats>.md] as absolute paths; unknown/None map to other.md."""
    key = (ats_type or "").strip().lower()
    return [_path("_common"), _path(key if key in ALL_ATS else "other")]
