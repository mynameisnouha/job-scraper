"""What the scorer noticed about you while it worked through the queue.

The scorer already reads every scraped posting against your CV and writes down,
per job, the gaps where **the substance is probably there and the CV just does
not show it** - that is `fixable_before_applying`, defined in the scoring prompt
as tier-4 evidence. Those observations were being computed a few hundred times
and then read once each, on the job they came from, by a dashboard.

Across the corpus they are a far better signal than on any single posting. One
posting wanting Airflow is that posting's taste; fourteen wanting it is a gap
worth an evening, and the scorer has effectively been polling the market about
you for months. Harvesting turns that into the one question worth asking.

Two rules make this safe to run unattended.

**Nothing harvested is a fact.** Leads land in the base `confirmed=False`, which
means the writer never sees them and the verifier rejects any line citing one.
The scorer is reading a CV and a posting, not your memory: it can say "no
Airflow shown", it cannot say whether you have used it. Only you can, and until
you do the lead is a question rather than evidence. Writing these straight in as
facts would put the scorer's guesses about you into a document sent to an
employer, which is the one failure this package exists to prevent.

**Nothing here calls a model.** This runs inside the scoring loop, over every
scraped job, in CI. It is string handling and a dictionary.
"""

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tailor import settings
from tailor.facts import SOURCE_SCORER, Fact, FactBase

# Words that carry no meaning when matching one gap phrasing against another.
# Kept short deliberately: an aggressive stopword list collapses "no Kafka in
# production" and "no Kafka experience at all" into one key, which is right, but
# it also collapses "German B2" and "German C1", which is not.
_NOISE = {
    "a", "an", "the", "no", "not", "any", "cv", "resume", "candidate", "candidates",
    "experience", "shown", "show", "shows", "missing", "lacks", "lacking", "on",
    "in", "of", "with", "for", "to", "and", "or", "is", "are", "has", "have",
    "evidence", "mention", "mentioned", "explicit", "explicitly", "listed",
}


def _tokens(text: str) -> frozenset:
    """Words that carry the meaning of a gap phrase.

    Dots are kept inside a token and stripped off the ends: "node.js" and "3.11"
    have to survive, while a sentence-ending "Airflow." must match a mid-sentence
    "Airflow" - it did not, and a lead the base already answered was asked about
    anyway because of the full stop.
    """
    words = re.findall(r"[a-z0-9+#.]+", (text or "").lower())
    cleaned = (w.strip(".") for w in words)
    return frozenset(w for w in cleaned if w not in _NOISE and len(w) > 1)


def _same_gap(left: frozenset, right: frozenset) -> bool:
    """Whether two gap phrasings are about the same thing.

    Overlap rather than exact match, because the scorer never phrases a gap
    identically twice: "Kubernetes not on CV", "no Kubernetes experience shown"
    and "Kubernetes missing" are one gap in three sittings. The threshold is high
    enough that "German B2" and "German C1" stay apart, which matters - those are
    different gaps with different answers.
    """
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= 0.6


def observations(breakdown: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The candidate-side leads in one score breakdown, as (gap, fix) pairs.

    Only `fixable_before_applying`. The other gap lists are left alone on
    purpose: `structural_gaps` are by definition things no answer can close, and
    `key_gaps` mirrors those plus must-have misses - harvesting either would fill
    the panel with questions whose honest answer is already known to be no.
    """
    out: List[Tuple[str, str]] = []
    for item in (breakdown or {}).get("fixable_before_applying") or []:
        if isinstance(item, dict):
            gap = str(item.get("gap") or "").strip()
            fix = str(item.get("fix") or "").strip()
        else:
            gap, fix = str(item).strip(), ""
        if gap:
            out.append((gap, fix))
    return out


def _lead_for(base: FactBase, gap: str) -> Optional[Fact]:
    """An existing lead about the same gap, if there is one."""
    wanted = _tokens(gap)
    for fact in base.facts:
        if fact.source == SOURCE_SCORER and not fact.confirmed:
            if _same_gap(wanted, _tokens(fact.claim)):
                return fact
    return None


def _already_answered(base: FactBase, gap: str) -> bool:
    """Whether the confirmed base already covers this gap.

    The scorer reads a CV, and the fact base holds a great deal a CV does not -
    every interview answer ever given. So a gap the scorer reports may have been
    answered months ago, and asking again would be the system failing to remember
    what it was told.
    """
    wanted = _tokens(gap)
    if not wanted:
        return True
    for fact in base.citable():
        known = _tokens(f"{fact.claim} {fact.your_words} {' '.join(fact.skills)}")
        if wanted <= known or _same_gap(wanted, known):
            return True
    return False


def harvest(base: FactBase, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Fold scored job rows into the fact base as unconfirmed leads.

    Each row is `{job_id, score_breakdown}` as stored in Supabase. Idempotent by
    job id: re-running over the whole corpus re-counts nothing, so this is safe
    to call after every scoring batch and again as a backfill.

    Returns counts for the log line. Nothing here raises - it runs at the tail of
    a scoring run whose real work is already saved.
    """
    added = merged = skipped = 0
    for row in rows or []:
        job_id = str(row.get("job_id") or "")
        breakdown = row.get("score_breakdown") or {}
        if not job_id or not isinstance(breakdown, dict):
            continue

        for gap, fix in observations(breakdown):
            existing = _lead_for(base, gap)
            if existing is not None:
                if job_id not in existing.seen_in:
                    existing.seen_in.append(job_id)
                    merged += 1
                continue
            if _already_answered(base, gap):
                skipped += 1
                continue
            if len(base.unconfirmed()) >= settings.MAX_SCORER_LEADS:
                # Full. Counts on the leads already held keep rising, so the
                # ranking stays live; only brand-new gaps wait. A panel of sixty
                # unanswered questions is not more useful than one of forty,
                # only longer.
                skipped += 1
                continue
            base.add(
                claim=gap,
                context=(f"Noticed by the job scorer. Suggested fix: {fix}" if fix
                         else "Noticed by the job scorer while scoring your queue."),
                tier=5, source=SOURCE_SCORER, confirmed=False, seen_in=[job_id],
            )
            added += 1
    return {"added": added, "merged": merged, "skipped": skipped}


def harvest_scored_batch(scored: Iterable[Tuple[str, Any]]) -> Dict[str, int]:
    """Harvest straight from a scoring run, then save. Never raises.

    Called at the tail of the scoring loop with the `(job_id, breakdown)` pairs
    it just produced, so leads appear as the scraped jobs come in rather than
    waiting for someone to open the tailoring page.

    On a CI runner there is no fact base to write to - `profile_facts.json` is
    personal and gitignored - so this finds nothing and says so. Nothing is lost:
    every breakdown is in Supabase, and `harvest_from_supabase` picks them up the
    next time the base is in front of the person who can answer them.
    """
    from tailor import facts as facts_mod

    try:
        base = facts_mod.load()
        if not base.facts:
            logging.info("No fact base on this machine; scorer leads not harvested "
                         "(they are recoverable from the stored breakdowns).")
            return {"added": 0, "merged": 0, "skipped": 0}

        rows = [
            {"job_id": job_id,
             "score_breakdown": (breakdown if isinstance(breakdown, dict)
                                 else breakdown.model_dump())}
            for job_id, breakdown in scored or []
        ]
        counts = harvest(base, rows)
        if counts["added"] or counts["merged"]:
            facts_mod.save(base)
        logging.info("Scorer leads: %d new, %d re-seen, %d skipped.",
                     counts["added"], counts["merged"], counts["skipped"])
        return counts
    except Exception as exc:  # noqa: BLE001 - the scores are already saved
        logging.warning("Harvesting scorer leads failed: %s", exc)
        return {"added": 0, "merged": 0, "skipped": 0}


def harvest_from_supabase(base: FactBase, limit: int = 500) -> Dict[str, int]:
    """Backfill leads from every breakdown already stored. The caller saves.

    This is the path that actually runs on a laptop: scoring happens in CI, where
    the fact base does not exist, so leads from those runs are picked up here the
    next time the tailoring page is opened.
    """
    try:
        from db import supabase_utils

        rows = supabase_utils.get_score_breakdowns(limit)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Could not read stored breakdowns: %s", exc)
        return {"added": 0, "merged": 0, "skipped": 0}
    return harvest(base, rows)
