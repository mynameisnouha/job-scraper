"""Per-source outcome accounting for a scrape run.

A source can die without anyone noticing. Indeed returned zero jobs for at least
twelve weeks — every search 403, every run still green — because a source that
fetches nothing looks exactly like a source that found nothing new. This module
draws that line explicitly:

    fetched == 0                -> BROKEN   (ERROR, run exits non-zero)
    fetched > 0 and new == 0    -> NO_NEW   (INFO — everything was a duplicate)
    fetched > 0 and new > 0     -> OK       (INFO, with counts)

The middle case is routine, and becomes more routine once cross-source dedup
lands, so it reads as routine rather than as something to squint at.

Zero-fetched is still ambiguous on its own: a Cloudflare-style block and a
genuinely empty result set both parse to zero cards. So every search response is
recorded with its HTTP status and body size, and those are printed when a source
trips the guard. A 200 with 40KB of HTML and no cards is a block or a broken
selector; a 200 with a small body and no cards is plausibly an empty result.
This module does not try to tell those apart — it just puts both numbers in
front of whoever reads the log.

Streamlit-free, HTTP-free, and side-effect-free apart from logging.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

BROKEN = "broken"
NO_NEW = "no_new"
OK = "ok"

# A broken source can have one failed attempt per query per page. Print enough to
# see the pattern, not so many that the real summary scrolls away.
MAX_DIAGNOSTIC_LINES = 10


@dataclass
class FetchAttempt:
    """One search-page request: what came back, and how much of it."""
    query: str
    status_code: Optional[int] = None
    body_bytes: int = 0
    items_parsed: int = 0
    error: str = ""

    def describe(self) -> str:
        status = self.status_code if self.status_code is not None else "no-response"
        line = (f"query={self.query!r} status={status} "
                f"body={self.body_bytes}B parsed={self.items_parsed}")
        if self.error:
            line += f" error={self.error}"
        return line


@dataclass
class SourceOutcome:
    """What one source produced across every query in a run."""
    source: str
    fetched: int = 0
    new: int = 0
    attempts: List[FetchAttempt] = field(default_factory=list)

    def record_attempt(self, query: str, status_code: Optional[int] = None,
                       body_bytes: int = 0, items_parsed: int = 0, error: str = "") -> None:
        self.attempts.append(FetchAttempt(query=query, status_code=status_code,
                                          body_bytes=body_bytes, items_parsed=items_parsed,
                                          error=error))

    def record_query(self, fetched: int = 0, new: int = 0) -> None:
        """Add one query's totals. `fetched` counts postings the source returned
        before deduplication; `new` counts the ones actually saved."""
        self.fetched += fetched
        self.new += new

    @property
    def status(self) -> str:
        if self.fetched == 0:
            return BROKEN
        return NO_NEW if self.new == 0 else OK


def summarize(outcome: SourceOutcome) -> Tuple[int, str]:
    """(logging level, message) for one source's outcome."""
    if outcome.status == OK:
        return logging.INFO, (f"{outcome.source}: {outcome.fetched} fetched, "
                              f"{outcome.new} new.")

    if outcome.status == NO_NEW:
        return logging.INFO, (f"{outcome.source}: {outcome.fetched} fetched, 0 new — "
                              f"every posting was already in the database. Normal.")

    lines = [
        f"{outcome.source}: 0 fetched across {len(outcome.attempts)} request(s) — "
        f"the source is broken, not quiet.",
    ]
    if outcome.attempts:
        lines.append("  What each request returned (status, body size, postings parsed):")
        for attempt in outcome.attempts[:MAX_DIAGNOSTIC_LINES]:
            lines.append(f"    {attempt.describe()}")
        remaining = len(outcome.attempts) - MAX_DIAGNOSTIC_LINES
        if remaining > 0:
            lines.append(f"    ... and {remaining} more request(s), all with 0 parsed.")
        lines.append("  A non-2xx status is an outright block. A 200 with a large body and "
                     "0 parsed is a challenge page or a broken selector; a 200 with a small "
                     "body and 0 parsed is plausibly an empty result set.")
    else:
        lines.append("  No HTTP responses were recorded for this source, so there is nothing "
                     "to tell a block apart from an empty result. Instrument its fetch path.")
    return logging.ERROR, "\n".join(lines)


def report(outcomes: List[SourceOutcome]) -> int:
    """Log every source's outcome and return the process exit code.

    Non-zero when any source fetched nothing: a red run is the signal that a
    source needs attention. Sources that fetched but found nothing new are not
    failures and never affect the exit code.
    """
    for outcome in outcomes:
        level, message = summarize(outcome)
        logging.log(level, message)

    broken = [o.source for o in outcomes if o.status == BROKEN]
    if broken:
        logging.error(f"Scrape finished with {len(broken)} broken source(s): "
                      f"{', '.join(broken)}. Exiting non-zero.")
        return 1
    return 0
