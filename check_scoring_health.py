"""
Is the scorer actually producing the fields calibration depends on?

Run: python check_scoring_health.py [--days 7] [--limit 500]

Written after ~34% of fully-scored jobs came back with no competitive_context
(every v2 field was optional in the JSON schema, so the model could skip them —
fixed in 737d897). This tells you whether that fix took, without opening
Supabase.

Screened-out jobs are counted separately: they never get a full scoring pass, so
their missing fields are correct, not a failure.
"""
import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import supabase_utils

logging.basicConfig(level=logging.WARNING, format="%(levelname)s - %(message)s")

REQUIRED_FIELDS = ["competitive_context", "dimension_scores", "one_line_verdict"]


def classify(breakdown):
    """screened_out | complete | degraded — degraded is the failure we care about."""
    if not breakdown:
        return "unscored"
    if breakdown.get("screen_only"):
        return "screened_out"
    missing = [f for f in REQUIRED_FIELDS if not breakdown.get(f)]
    return "complete" if not missing else "degraded"


def missing_fields(breakdown):
    return [f for f in REQUIRED_FIELDS if not (breakdown or {}).get(f)]


def fetch(limit):
    return supabase_utils.get_scored_jobs_for_health_check(limit=limit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Only look at jobs scraped in the last N days")
    parser.add_argument("--limit", type=int, default=500, help="Max rows to inspect")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    rows = fetch(args.limit)

    recent = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row.get("scraped_at")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            recent.append(row)

    if not recent:
        print(f"No scored jobs in the last {args.days} days (looked at {len(rows)} rows).")
        return

    kinds = Counter(classify(r.get("score_breakdown")) for r in recent)
    fully_scored = kinds["complete"] + kinds["degraded"]

    oldest = min(str(r.get("scraped_at"))[:10] for r in recent)
    newest = max(str(r.get("scraped_at"))[:10] for r in recent)
    print(f"Window: {oldest} → {newest}")
    print("NOTE: jobs scored before the required-fields fix are expected to be degraded.")
    print("      Narrow with --days 1 after a scoring run to judge the fix itself.\n")
    print(f"Scored jobs in the last {args.days} days: {len(recent)}")
    print(f"  screened out (no full scoring, expected): {kinds['screened_out']}")
    print(f"  fully scored:                            {fully_scored}")

    if not fully_scored:
        print("\nNothing was fully scored in this window — run the scorer, then re-check.")
        return

    rate = kinds["degraded"] / fully_scored
    print(f"    complete:  {kinds['complete']}")
    print(f"    DEGRADED:  {kinds['degraded']}  ({rate * 100:.0f}% of fully scored)")

    if kinds["degraded"]:
        field_counts = Counter()
        for row in recent:
            b = row.get("score_breakdown") or {}
            if classify(b) == "degraded":
                field_counts.update(missing_fields(b))
        print("\n  Missing fields:")
        for field, n in field_counts.most_common():
            print(f"    {field}: {n}")
        print("\n  Examples:")
        shown = 0
        for row in recent:
            b = row.get("score_breakdown") or {}
            if classify(b) == "degraded" and shown < 5:
                print(f"    {row.get('job_id')} — {(row.get('job_title') or '')[:50]} "
                      f"(missing: {', '.join(missing_fields(b))})")
                shown += 1

    print()
    if rate == 0:
        print("HEALTHY — every fully-scored job in this window carries the calibration fields.")
    elif rate < 0.05:
        print("OK — a few stragglers; the repair pass is likely catching most of them.")
    else:
        print(f"{rate * 100:.0f}% degraded in this window.")
        print("If these were all scored BEFORE the required-fields fix, that is the old "
              "behaviour and expected — re-run with --days 1 after the next scoring run.")
        print("If they were scored after it, the fix is not holding: check the scoring logs "
              "for 'failed validation … retrying' and whether the repair pass is failing too.")


if __name__ == "__main__":
    main()
