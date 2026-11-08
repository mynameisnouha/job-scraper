"""Gates the screening phase applies in code, before or after the screen model.

`german_c1_match` finds an explicit demand for fluent German (C1 or above). It is
code rather than a prompt rule because the screen keeps missing it: in the 30 days
to 2026-11-08 the screen passed 222 of 636 fully scored jobs that the full scorer
then labelled C1-fluent, so about a third of full-scoring spend went on jobs the
scorer capped anyway. The phrase list is the screen prompt's rule 1.

Every phrase must qualify *German*. "Englisch verhandlungssicher" or "English C1"
are demands about English and must not match; a German-language ad that names no
level, or a lower one ("gute Deutschkenntnisse", B2), must not match either.
"""

import re
from typing import Optional

_DE = r"(?:deutsch|german)"

_C1_PATTERNS = [
    r"flie(?:ß|ss)end\w*\s+deutsch\w*",
    r"deutsch\w*\W{0,15}flie(?:ß|ss)end\w*",
    r"fluent\s+(?:in\s+)?german",
    r"german\W{0,3}fluent",
    r"deutsch\w*\W{0,15}verhandlungssicher\w*",
    r"verhandlungssicher\w*\s+(?:in\s+)?deutsch\w*",
    r"deutsch[\w\s-]{0,25}muttersprachlich\w*",
    r"muttersprachlich\w*\s+deutsch\w*",
    r"(?:sehr\s+gute|exzellente|ausgezeichnete)\s+deutsch\w*",
    r"excellent\s+german",
    # C1/C2 only right after the language: "German (C1)", "Deutsch mindestens C2".
    # Not "English C1, German B1", where C1 belongs to English.
    _DE + r"\W{0,3}(?:\w+\W+)?C[12]\b",
    r"\bC[12][\s-]{1,2}" + _DE,  # "C1 Deutsch", "C1-German"; a comma ends the claim
]

_C1_RE = re.compile("|".join(f"(?:{p})" for p in _C1_PATTERNS), re.IGNORECASE)


def german_c1_match(description: Optional[str]) -> Optional[str]:
    """The phrase demanding fluent German, or None when the text demands no such level."""
    if not description:
        return None
    match = _C1_RE.search(description)
    return match.group(0) if match else None
