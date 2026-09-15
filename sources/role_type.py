"""What kind of posting is this — a standard role or a graduate/trainee programme?

One place for the answer because three things need it and must agree: the
sources tag rows at scrape time, the scorer reads the tag to judge the posting
as a programme, and the queue filters and badges on it.
"""

import re
from typing import Any, Dict, Optional

# A structured early-careers intake: fixed start dates, rotations, a cohort, an
# assessment centre. These are wanted, but they are a different kind of posting
# from a standard role — the competing candidate is a fresh graduate, the risk is
# being over-qualified rather than under, and the intake date decides everything —
# so they are tagged here, where the title is first seen, and the scorer and the
# queue read the tag rather than each guessing again from the title.
#
# Title only, deliberately. A description mentions "graduates welcome" on half the
# junior roles in Germany; the title is what the employer chose to call it.
# German compounds are left open-ended (Traineeprogramm, Absolventenprogramm,
# Nachwuchskräfteprogramm). "Trainee" alone is enough: in the German market it is
# the programme word, not a junior title.
_PROGRAM_RE = re.compile(
    r"\btrainee"
    r"|\bgraduate (program|programme|scheme|trainee|track)"
    r"|\bgraduates? (development|leadership|rotation(al)?) program"
    r"|\babsolvent(en|innen)?[- ]?program"
    r"|\bnachwuchs(kr(ä|ae)fte|f(ü|ue)hrungskr(ä|ae)fte)?[- ]?program"
    r"|\beinstiegsprogram"
    r"|\b(rotation(al)?|early[- ]careers?|future leaders?|young (professionals?|talents?)) program"
    r"|\b(ai|ml|machine learning|data science|research) (residency|fellowship)\b"
    r"|\bresidency program",
    re.IGNORECASE,
)

GRADUATE_PROGRAM = "graduate_program"


def program_type_of(title: str | None) -> str | None:
    """'graduate_program' if the title names a structured graduate/trainee intake, else None.

    Checked AFTER is_internship_role: "Trainee" postings on Arbeitsagentur share a
    category with Praktika, and a "Praktikum im Traineeprogramm" is still a Praktikum.
    """
    if _PROGRAM_RE.search(title or ""):
        return GRADUATE_PROGRAM
    return None


def program_type(job: Dict[str, Any]) -> Optional[str]:
    """The programme tag for a stored job, from whichever layer recorded it.

    The column is the scrape-time verdict; the breakdown carries the screen's
    own reading for postings whose title hid it; the title is the fallback for
    rows written before the column existed.
    """
    if job.get("program_type"):
        return job["program_type"]
    breakdown = job.get("score_breakdown") or {}
    if isinstance(breakdown, dict) and breakdown.get("program_type"):
        return breakdown["program_type"]
    return program_type_of(job.get("job_title"))


def is_program(job: Dict[str, Any]) -> bool:
    return program_type(job) == GRADUATE_PROGRAM
