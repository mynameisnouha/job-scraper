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

# The language, never the country: "Germany" and "Deutschland" are locations, and
# "Remote in Germany. C1 English" is an English demand.
_DE = r"(?:deutsch(?!land)\w*|german(?!y)\b)"
# Horizontal whitespace only. A language list puts each language on its own line
# ("Englisch: fließend / Deutsch: von Vorteil"); a match must not cross from one
# line to the next.
_H = r"[^\S\n]"
_GAP = r"[^\w\n]"  # punctuation or spaces, still on the same line

_C1_PATTERNS = [
    rf"flie(?:ß|ss)end\w*{_H}+{_DE}",
    rf"{_DE}{_GAP}{{0,15}}flie(?:ß|ss)end\w*",
    rf"fluent{_H}+(?:in{_H}+)?{_DE}",
    rf"german(?!y){_H}*[(:\-–]{_H}*fluent",  # "German (fluent)", "German: fluent"; not "German, fluent English"
    rf"{_DE}{_GAP}{{0,15}}verhandlungssicher\w*",
    rf"verhandlungssicher\w*{_H}+(?:in{_H}+)?{_DE}",
    rf"deutsch(?!land)[\w \t-]{{0,25}}muttersprachlich\w*",
    rf"muttersprachlich\w*{_H}+{_DE}",
    rf"(?:sehr{_H}+gute|exzellente|ausgezeichnete){_H}+{_DE}",
    rf"excellent{_H}+{_DE}",
    # C1/C2 only right after the language: "German (C1)", "Deutsch mindestens C2".
    # Not "English C1, German B1", where C1 belongs to English.
    rf"{_DE}{_GAP}{{0,3}}(?:\w+{_GAP}+)?C[12]\b",
    rf"\bC[12][ \t-]{{1,2}}{_DE}",  # "C1 Deutsch", "C1-German"; a comma ends the claim
]

_C1_RE = re.compile("|".join(f"(?:{p})" for p in _C1_PATTERNS), re.IGNORECASE)

# "Sehr gute Deutschkenntnisse von Vorteil" asks for nothing: the level is a plus.
_SOFTENER_RE = re.compile(r"von\s+vorteil|wünschenswert|wuenschenswert|\bplus\b|nice[\s-]to[\s-]have",
                          re.IGNORECASE)
_SOFTENER_WINDOW = 40  # characters after the phrase, same line


def german_c1_match(description: Optional[str]) -> Optional[str]:
    """The phrase demanding fluent German, or None when the text demands no such level."""
    if not description:
        return None
    for match in _C1_RE.finditer(description):
        after = description[match.end():match.end() + _SOFTENER_WINDOW].split("\n", 1)[0]
        if not _SOFTENER_RE.search(after):
            return match.group(0)
    return None
