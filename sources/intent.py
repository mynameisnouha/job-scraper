"""Is a career-feed posting worth storing at all?

Feeds return every opening a company has, in every country. Storing them all
would make the screening pass pay for sales and US roles, so two cheap checks run
first: an on-topic title, and a German (or remote, open-to-Germany) location.
Terms come from config.py so the feed follows the same search intent as the
job-board queries.
"""

import re
from typing import Iterable, Optional

import config

_SPLIT_PARTS = re.compile(r";|\||\s/\s")


def _words(text: str) -> str:
    """Lowercase, punctuation to spaces, padded, so ' ai ' is a whole-word test."""
    return " " + " ".join(re.sub(r"[^\wäöüß]+", " ", text.lower()).split()) + " "


def _has_any(padded: str, terms: Iterable[str]) -> bool:
    return any(_words(term) in padded for term in terms if term.strip())


def matches_search_intent(title: Optional[str]) -> bool:
    if not title or not title.strip():
        return False
    return _has_any(_words(title), config.ATS_TITLE_TERMS)


def in_target_location(location: Optional[str]) -> bool:
    """True for German places and open remote roles. An empty location is kept:
    the scorer reads the description and gates location itself."""
    if not location or not location.strip():
        return True
    open_remote, foreign_place = False, False
    for part in _SPLIT_PARTS.split(location):
        words = _words(part)
        if words.strip() == "":
            continue
        if _has_any(words, config.ATS_LOCATION_KEEP):
            return True
        if " remote " in words:
            open_remote = open_remote or not _has_any(words, config.ATS_LOCATION_DROP)
        else:
            foreign_place = True
    # A bare "Remote" beside a non-German office ("San Francisco; Remote") is that
    # office's remote role, not one open to Germany.
    return open_remote and not foreign_place
