"""Pulling the jobs worth building a CV for out of Supabase.

Selection happens here and nowhere else, so there is one answer to "which jobs
were these archetypes fitted to?" - a question you will want to answer later when
a cluster stops matching what the queue is showing you.
"""

import json
import logging
from typing import Any, Dict, List

from db import supabase_utils
from clustering import settings


def _breakdown(row: Dict[str, Any]) -> Dict[str, Any]:
    """score_breakdown comes back as a dict or a JSON string depending on driver."""
    raw = row.get("score_breakdown") or {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


def fetch_scored_jobs(page_size: int = 500) -> List[Dict[str, Any]]:
    """Every job that has been through full scoring, paged out of Supabase."""
    client = supabase_utils.supabase
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        resp = (
            client.table("jobs")
            .select(
                "job_id,job_title,company,level,location,provider,job_url,"
                "resume_score,score_breakdown,description,scraped_at"
            )
            .not_.is_("score_breakdown", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < page_size:
            break
        offset += page_size
    return rows


def select_addressable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply the gates in settings and report what each one removed.

    The per-gate counts are logged rather than just the final number, because a
    run where the German gate suddenly drops twice as many jobs is telling you
    something about the market, not about this code.
    """
    dropped = {"score": 0, "german": 0, "years": 0, "no_description": 0}
    kept: List[Dict[str, Any]] = []

    for row in rows:
        bd = _breakdown(row)
        if (row.get("resume_score") or 0) < settings.MIN_SCORE:
            dropped["score"] += 1
            continue
        if bd.get("german_required") in settings.EXCLUDE_GERMAN_LEVELS:
            dropped["german"] += 1
            continue
        if (bd.get("years_experience_required") or 0) > settings.MAX_YEARS_REQUIRED:
            dropped["years"] += 1
            continue
        if len(row.get("description") or "") < 300:
            # Too short to extract requirements from; including it would put a
            # near-empty profile vector at the origin and bend a centroid.
            dropped["no_description"] += 1
            continue
        kept.append(row)

    logging.info(
        "Corpus: %d scored -> %d addressable (dropped: score<%d %d, C1-German %d, "
        ">%dy experience %d, no description %d)",
        len(rows), len(kept), settings.MIN_SCORE, dropped["score"], dropped["german"],
        settings.MAX_YEARS_REQUIRED, dropped["years"], dropped["no_description"],
    )
    return kept


def load_addressable() -> List[Dict[str, Any]]:
    return select_addressable(fetch_scored_jobs())
