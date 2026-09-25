"""What the market asks for, across the postings you could plausibly apply to.

Two questions, both about the corpus rather than about any one posting: how much
German does it really demand, and which skills recur.

The population is every job scoring at or above `clustering.settings.MIN_SCORE`
(30). Deliberately NOT the clustering corpus, which is a different and narrower
set: that one excludes C1-German postings before it fits archetypes, so asking it
"how many postings demand C1?" would answer approximately zero. The question only
means something against the wider set.
"""

import json
from typing import Any, Dict, List, Sequence

from review import theme as T

# How the ad's own language is read when it states no level. This is the one
# judgement call in the module and it is worth stating plainly, because the
# scorer deliberately refuses to make it.
#
# The scorer treats 'unstated' as unknown-and-worth-asking rather than as a
# demand, and that is right for scoring: capping every German-language ad would
# throw away most of the Arbeitsagentur inventory on the strength of the ad's
# language rather than the employer's stated requirement, and German employers
# are in practice more flexible than the wording implies.
#
# For a market summary the opposite reading is the useful one. An employer who
# writes the whole ad in German, names no level, and expects to read applications
# is describing a German-speaking workplace. Counting those as "unknown" makes
# the market look far more open than it is.
#
# So the assumption is applied here and nowhere else - the queue, the gates and
# the scores are untouched - and only where the ad states NO level. An explicit
# statement always wins: an ad written in German that says B2, or says English is
# the working language, is counted as it says, not as its language implies.
ASSUMED_LEVEL_FOR_GERMAN_ADS = "C2 assumed"

GERMAN_ADS = ("de", "mixed")

# Ordered hardest-to-clear first, and coloured with the app's existing German
# vocabulary: purple is a gate that closes the job to you, neutral is a level you
# could reach, blue is a job open to you today. Same tokens as the gate chip on
# every job card, so the page agrees with the cards.
GERMAN_BUCKETS: List[Dict[str, str]] = [
    {"key": "c2_assumed", "label": "C2 assumed · ad in German, no level stated",
     "fill": T.PURPLE[600]},
    {"key": "C1-fluent", "label": "C1 stated — hard gate", "fill": T.PURPLE[500]},
    {"key": "B2", "label": "B2 stated", "fill": T.NEUTRAL[500]},
    {"key": "B1", "label": "B1 stated", "fill": T.NEUTRAL[400]},
    {"key": "nice-to-have", "label": "A plus, not required", "fill": T.BLUE[400]},
    {"key": "none", "label": "None — English is the working language", "fill": T.BLUE[600]},
    {"key": "unknown", "label": "Not stated, ad in English", "fill": T.NEUTRAL[300]},
]

# Buckets that mean "you cannot walk into this today". B2 is deliberately not
# here: it is a level to reach, not a wall, and lumping it with C1 would overstate
# how closed the market is.
CLOSED_KEYS = ("c2_assumed", "C1-fluent")
OPEN_KEYS = ("nice-to-have", "none")


def breakdown_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """score_breakdown comes back as a dict or a JSON string depending on driver."""
    raw = (row or {}).get("score_breakdown") or {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


def classify_german(breakdown: Dict[str, Any]) -> str:
    """Which bucket one posting falls into.

    An explicit level always wins over the ad's language — see the note above.
    """
    b = breakdown or {}
    level = str(b.get("german_required") or "").strip()
    if level in ("C1-fluent", "B2", "B1", "nice-to-have", "none"):
        return level
    # 'unstated' and 'unclear' both land here: neither names a level.
    if b.get("jd_language") in GERMAN_ADS:
        return "c2_assumed"
    return "unknown"


def german_demand(breakdowns: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The German picture for a set of postings: every bucket, plus the headline.

    Empty buckets are kept. A zero next to "B1 stated" is information — it says
    the scorer has never recorded that level — whereas a missing row just looks
    like the question was not asked.
    """
    counts: Dict[str, int] = {b["key"]: 0 for b in GERMAN_BUCKETS}
    for breakdown in breakdowns:
        counts[classify_german(breakdown)] += 1

    total = sum(counts.values())
    rows = [
        {**bucket, "n": counts[bucket["key"]],
         "share": (counts[bucket["key"]] / total) if total else 0.0}
        for bucket in GERMAN_BUCKETS
    ]
    closed = sum(counts[k] for k in CLOSED_KEYS)
    open_now = sum(counts[k] for k in OPEN_KEYS)
    return {
        "rows": rows,
        "total": total,
        "closed": closed,
        "closed_share": (closed / total) if total else 0.0,
        "open": open_now,
        "open_share": (open_now / total) if total else 0.0,
    }


def top_skills(summary: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
    """Corpus-wide skill demand, weighted by how many postings each cluster holds.

    Read off the clustering summary rather than recomputed: `skill_demand` is
    already the share of an archetype's postings that ask for a skill, so a
    size-weighted mean over the archetypes is the share of the whole addressable
    corpus — no second pass over the postings, and no risk of the two numbers
    disagreeing.

    Note the population differs from the German panel above: these are the
    addressable postings the archetypes were fitted to, which exclude C1-German.
    That is the right denominator here — this is "what do the jobs I could take
    ask for", not "what does the market ask for".
    """
    totals: Dict[str, float] = {}
    n_total = 0
    for cluster in summary.get("clusters", []):
        size = cluster.get("size") or 0
        n_total += size
        for skill, share in cluster.get("skill_demand") or []:
            totals[skill] = totals.get(skill, 0.0) + share * size
    if not n_total:
        return []
    ranked = sorted(((s, v / n_total) for s, v in totals.items()),
                    key=lambda x: -x[1])[:limit]
    return [{"label": skill.replace("_", " "), "share": share,
             "n": f"{share * 100:.0f}%", "fill": T.PURPLE[400]}
            for skill, share in ranked]
