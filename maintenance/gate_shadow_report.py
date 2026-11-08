"""Would the screen gates have been safe? Read-only report on their shadow data.

Run it with ``python -m maintenance.gate_shadow_report [--floor N]``, about 14 days
after the gates went out in shadow mode. Both gates record what they would have done
in ``score_breakdown.screen_gates`` while changing nothing, so jobs they would have
skipped were still fully scored and the real score can be compared.

Only fully scored rows are evidence. A screen-only row has a screen's guess, not a
score, and counting it would test the gate against itself.

The two gates are judged differently because they are different decisions:

- the rough-score floor is the owner's "don't score what lands under 30" rule, so it
  passes when under 2% of what it would skip scored 30+ and nothing it would skip was
  applied to;
- the C1 regex is a policy (fluent-German jobs are out), so it is only checked for
  false positives: hits the full scorer did not label C1-fluent, target under 5%.
"""

import argparse
import json
import logging
from typing import Any, Dict, List

import config
from db import supabase_utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

FLOOR_MAX_SKIP_30_PLUS = 0.02
C1_MAX_FALSE_POSITIVE_RATE = 0.05
MIN_ROWS = 30  # fewer rows than this cannot show a 2% or 5% rate either way


def summarize(rows: List[Dict[str, Any]], floor: int) -> Dict[str, Any]:
    """Counts and verdicts for both gates over rows carrying a screen_gates record."""
    full = []
    for row in rows:
        breakdown = row.get("score_breakdown") or {}
        gates = breakdown.get("screen_gates")
        if gates and not breakdown.get("screen_only") and row.get("resume_score") is not None:
            full.append((row, breakdown, gates))

    skipped = [(r, b) for r, b, g in full
               if g.get("rough_score") is not None and g["rough_score"] < floor]
    skip_30_plus = sum(1 for r, _ in skipped if r["resume_score"] >= 30)
    skip_applied = sum(1 for r, _ in skipped if r.get("application_date"))

    hits = [b for _, b, g in full if g.get("german_c1")]
    false_positives = sum(1 for b in hits if b.get("german_required") != "C1-fluent")
    fp_rate = round(false_positives / len(hits), 4) if hits else None

    # A failure counts on any sample; a pass needs MIN_ROWS behind it before the
    # owner flips enforcement on.
    if not skipped:
        verdict_floor = "no data"
    elif skip_30_plus / len(skipped) >= FLOOR_MAX_SKIP_30_PLUS or skip_applied:
        verdict_floor = "too strict"
    else:
        verdict_floor = "ok" if len(skipped) >= MIN_ROWS else "too little data"

    if not hits:
        verdict_c1 = "no data"
    elif fp_rate >= C1_MAX_FALSE_POSITIVE_RATE:
        verdict_c1 = "too strict"
    else:
        verdict_c1 = "ok" if len(hits) >= MIN_ROWS else "too little data"

    return {
        "floor": floor,
        "full_scored": len(full),
        "floor_would_skip": len(skipped),
        "floor_skip_scored_30_plus": skip_30_plus,
        "floor_skip_applied": skip_applied,
        "c1_hits_full_scored": len(hits),
        "c1_false_positives": false_positives,
        "c1_false_positive_rate": fp_rate,
        "verdict_floor": verdict_floor,
        "verdict_c1": verdict_c1,
    }


def fetch_rows() -> List[Dict[str, Any]]:
    """Every row with a screen_gates record, paged. Filtered in Python: the table is a
    few thousand rows and a JSON-path filter is easy to get subtly wrong."""
    rows, offset, page = [], 0, 1000
    while True:
        batch = (
            supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
            .select("job_id, resume_score, application_date, score_breakdown")
            .range(offset, offset + page - 1)
            .execute()
        ).data or []
        rows += [r for r in batch if (r.get("score_breakdown") or {}).get("screen_gates")]
        if len(batch) < page:
            return rows
        offset += page


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge the screen gates' shadow data.")
    parser.add_argument("--floor", type=int, default=config.FULL_SCORE_MIN_ROUGH,
                        help=f"Rough-score floor to test (default {config.FULL_SCORE_MIN_ROUGH}).")
    args = parser.parse_args(argv)
    print(json.dumps(summarize(fetch_rows(), args.floor), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
