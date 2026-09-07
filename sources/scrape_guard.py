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
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    already_in_db: int = 0
    filtered_out: Dict[str, int] = field(default_factory=dict)
    attempts: List[FetchAttempt] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def filtered_total(self) -> int:
        return sum(self.filtered_out.values())

    @property
    def unaccounted(self) -> int:
        """Fetched postings that reached no bucket. Should be 0; surfaced rather
        than hidden, because a number that silently absorbs the leftovers is how
        `fetched - new` came to mean three different things at once."""
        return self.fetched - (self.already_in_db + self.filtered_total + self.new)

    def record_attempt(self, query: str, status_code: Optional[int] = None,
                       body_bytes: int = 0, items_parsed: int = 0, error: str = "") -> None:
        self.attempts.append(FetchAttempt(query=query, status_code=status_code,
                                          body_bytes=body_bytes, items_parsed=items_parsed,
                                          error=error))

    def record_query(self, fetched: int = 0, new: int = 0, already_in_db: int = 0) -> None:
        """Add one query's totals.

        The four buckets partition `fetched`, so they sum:
            fetched = already_in_db + filtered_out + new(saved)

        `fetched` is what the search returned before any deduplication,
        `already_in_db` what dedup dropped, `filtered_out` what the content
        filters dropped and why (record_filtered), `new` what was saved.
        """
        self.fetched += fetched
        self.new += new
        self.already_in_db += already_in_db

    def record_filtered(self, reason: str, count: int = 1) -> None:
        """Count a fetched posting that was dropped, by reason. Distinguishing
        'already had it' from 'rejected it' from 'the fetch failed' is what makes
        a per-query duplicate rate readable — a query returning 20 results that
        another query already covered looks identical to a productive one when
        the counters are lumped together."""
        if count:
            self.filtered_out[reason] = self.filtered_out.get(reason, 0) + count

    @property
    def status(self) -> str:
        if self.fetched == 0:
            return BROKEN
        return NO_NEW if self.new == 0 else OK


def summarize(outcome: SourceOutcome) -> Tuple[int, str]:
    """(logging level, message) for one source's outcome."""
    breakdown = (f"{outcome.already_in_db} already in the database, "
                 f"{outcome.filtered_total} filtered")
    if outcome.filtered_out:
        reasons = ", ".join(f"{reason} {count}"
                            for reason, count in sorted(outcome.filtered_out.items(),
                                                        key=lambda kv: -kv[1]))
        breakdown += f" ({reasons})"
    if outcome.unaccounted:
        breakdown += (f", {outcome.unaccounted} unaccounted — the counters do not add up")

    if outcome.status == OK:
        return logging.INFO, (f"{outcome.source}: {outcome.fetched} fetched, "
                              f"{outcome.new} saved — {breakdown}.")

    if outcome.status == NO_NEW:
        return logging.INFO, (f"{outcome.source}: {outcome.fetched} fetched, 0 saved — "
                              f"{breakdown}. Normal.")

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


def step_summary(outcomes: List[SourceOutcome]) -> str:
    """A Markdown table for $GITHUB_STEP_SUMMARY: one row per source.

    The columns partition what was fetched — in-DB, filtered, saved — rather than
    collapsing them into one "not saved" number.
    """
    if not outcomes:
        return "### Scrape\n\nNo sources ran.\n"

    labels = {OK: "ok", NO_NEW: "no new", BROKEN: "BROKEN"}
    lines = [
        "### Scrape",
        "",
        "| Source | Fetched | In DB | Filtered | Saved | Elapsed | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for o in outcomes:
        lines.append(f"| {o.source} | {o.fetched} | {o.already_in_db} | {o.filtered_total} | "
                     f"{o.new} | {o.elapsed_seconds:.0f}s | {labels[o.status]} |")

    for o in outcomes:
        if o.filtered_out:
            reasons = ", ".join(f"{reason} {count}"
                                for reason, count in sorted(o.filtered_out.items(),
                                                            key=lambda kv: -kv[1]))
            lines += ["", f"{o.source} filtered: {reasons}"]
        if o.unaccounted:
            lines += ["", f"⚠️ {o.source}: {o.unaccounted} fetched posting(s) reached no "
                          f"bucket — the counters do not add up, so one of them is lying."]

    broken = [o.source for o in outcomes if o.status == BROKEN]
    if broken:
        lines += ["", f"**{', '.join(broken)} fetched nothing.** "
                      "Per-request status codes and body sizes are in the job log."]
    return "\n".join(lines) + "\n"


def write_step_summary(outcomes: List[SourceOutcome]) -> bool:
    """Append the summary to $GITHUB_STEP_SUMMARY when running in Actions.

    Fail-soft: a summary is a convenience, never a reason to fail a scrape.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(step_summary(outcomes))
        return True
    except OSError as e:
        logging.warning(f"Could not write the step summary: {e}")
        return False


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
