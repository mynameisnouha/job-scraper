"""Deterministic checks on a generated application. No LLM.

This is the honesty guarantee, and it is code rather than a prompt on purpose.
An optimisation loop with something to maximise drifts: "working student"
softens into "engineer", "30% memory reduction" becomes "substantial gains",
contribution slides into ownership. Each step is defensible on its own, which is
exactly why a model asked to police it will wave them through. A regex will not.

Three things are checked:

1. Every claim-bearing line cites at least one fact id, and every id exists.
2. Every number in a line appears in one of the facts it cites. This is the
   check that catches invented metrics, which are the most damaging failure -
   a number is the thing an interviewer is most likely to probe.
3. No phrase from the banned list. Crude, but it is the only anti-generic
   measure here that cannot be argued with.
"""

import re
from dataclasses import dataclass, field
from typing import List

from tailor import settings
from tailor.documents import Application, all_lines
from tailor.facts import FactBase

# Numbers worth checking. Deliberately skips bare years (1990-2099) - dates come
# from context rather than from a metric, and flagging them produced nothing but
# noise about employment periods that were never in question.
_NUMBER = re.compile(r"\b\d[\d.,]*\s*%?|\b\d+x\b", re.IGNORECASE)
_YEAR = re.compile(r"^(19|20)\d{2}$")

# Reserved citation for lines drawn from the personal details block rather than
# the fact base - availability, notice period, location, contact.
PERSONAL_CITATION = "personal"


@dataclass
class Problem:
    kind: str          # missing_citation | unknown_fact | unsupported_number | banned_phrase
    where: str
    detail: str


@dataclass
class VerifyResult:
    problems: List[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        if self.ok:
            return "All claims cite facts, every number is supported, no banned phrasing."
        return "\n".join(f"- [{p.kind}] {p.where}: {p.detail}" for p in self.problems)


def _numbers_in(text: str) -> List[str]:
    found = []
    for raw in _NUMBER.findall(text):
        token = raw.strip()
        digits = token.rstrip("%x").replace(",", "").replace(".", "").strip()
        if _YEAR.match(token.strip()):
            continue
        if not digits:
            continue
        found.append(token)
    return found


def _normalise_number(token: str) -> str:
    return token.lower().replace(" ", "").replace(",", ".").rstrip("%x").rstrip(".")


def verify(app: Application, base: FactBase) -> VerifyResult:
    result = VerifyResult()
    known = base.ids()

    for index, line in enumerate(all_lines(app)):
        where = f"line {index + 1}"
        snippet = line.text[:60]

        if not line.fact_ids:
            result.problems.append(
                Problem("missing_citation", where, f'no fact cited for "{snippet}"')
            )
            continue

        # Availability, notice period, location and contact details are not
        # claims about competence — they come from application_answers.json, not
        # from the fact base. Requiring a fact id for them made the writer either
        # fabricate a citation or drop the closing paragraph of the Anschreiben
        # entirely, and a German cover letter that never states a start date is
        # worse than one whose start date is unverifiable here. Their numbers are
        # dates and salary bands, so number checking is skipped too.
        if PERSONAL_CITATION in line.fact_ids:
            continue

        unknown = [fid for fid in line.fact_ids if fid not in known]
        if unknown:
            result.problems.append(
                Problem("unknown_fact", where,
                        f"cites {', '.join(unknown)}, which do not exist in the fact base")
            )
            continue

        # Every number must be traceable to a cited fact.
        supporting = " ".join(
            (f.claim + " " + " ".join(f.metrics) + " " + f.context + " " + f.your_words)
            for f in (base.by_id(fid) for fid in line.fact_ids) if f
        )
        supporting_numbers = {_normalise_number(n) for n in _numbers_in(supporting)}
        for number in _numbers_in(line.text):
            if _normalise_number(number) not in supporting_numbers:
                result.problems.append(
                    Problem("unsupported_number", where,
                            f'"{number}" in "{snippet}" is not in any cited fact')
                )

    # Banned phrasing, across everything the reader sees.
    from tailor.documents import render_cv, render_cover_letter

    haystack = (render_cv(app.cv) + "\n" + render_cover_letter(app.cover_letter)).lower()
    for phrase in settings.BANNED_PHRASES:
        if phrase in haystack:
            result.problems.append(
                Problem("banned_phrase", "document", f'"{phrase}" reads as machine-written')
            )

    return result
