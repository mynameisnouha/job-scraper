"""Find which companies publish a career feed, and switch polling on for them.

    python -m maintenance.detect_ats [--limit 50] [--dry-run]

Seeds the companies table from every company already in `jobs`, plus an optional
`target_companies.json` (see the .example). Each company without a feed is probed
by guessing its slug on every ATS adapter; the first feed with at least one posting
wins. A miss is stored with its date and not retried for ATS_PROBE_RETRY_DAYS, so a
run costs a bounded number of requests and never an LLM call.

A target entry with ats_type and ats_slug is saved as given, without probing.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set, Tuple

import config
from db import supabase_utils
from sources import dedup
from sources.ats import ADAPTERS

TARGETS_PATH = "target_companies.json"
MAX_CANDIDATES = 4


def slug_candidates(company: Optional[str]) -> List[str]:
    """Plausible feed slugs for a company name: joined, hyphenated, first word."""
    words = dedup.normalize_company(company).split()
    if not words:
        return []
    candidates = ["".join(words), "-".join(words)]
    # The first word alone finds "deutschebahn" as "deutsche"-less brands like
    # "zalando", but a short first word ("the", "dr") would match other companies.
    if len(words) > 1 and len(words[0]) >= 4:
        candidates.append(words[0])
    # Short or digit-only slugs ("1-1", "2501") belong to someone else as often as
    # not: the 2026-10-17 dry run matched '1&1' to an unrelated Workable account.
    # A slug becomes a hostname label on some ATSs ({slug}.recruitee.com), and DNS
    # labels stop at 63 characters.
    candidates = [c for c in candidates
                  if len(c.replace("-", "")) >= 3 and any(ch.isalpha() for ch in c) and len(c) <= 63]
    return list(dict.fromkeys(candidates))[:MAX_CANDIDATES]


def _belongs_to(company: str, adapter, raws: list, known_titles: Set[str]) -> bool:
    """Is this feed really the company's? A guessed slug is often someone else's:
    on 2026-10-17 personio:amazon was a small agency and greenhouse:abacus a
    different ABACUS. The feed's own company name settles it when it matches
    exactly; otherwise (a partial match, or a feed that names no company) one of
    its titles must be a title already stored for this company."""
    target = dedup.normalize_company(company)
    name_of = getattr(adapter, "company_name_of", lambda raw: None)
    names = {dedup.normalize_company(n) for n in (name_of(raw) for raw in raws) if n}
    if target in names:
        return True
    if names and not any(target.startswith(f"{n} ") or n.startswith(f"{target} ") for n in names):
        return False
    titles = {dedup.normalize_title(adapter.title_of(raw)) for raw in raws}
    return bool(titles & (known_titles or set()))


def probe(company: str, adapters=None, known_titles: Optional[Set[str]] = None) -> Optional[Tuple[str, str]]:
    """(ats_type, slug) of the first feed that has postings and belongs to the
    company, or None. `known_titles` are the normalized titles already stored for it."""
    adapters = adapters if adapters is not None else ADAPTERS
    for slug in slug_candidates(company):
        for ats_type, adapter in adapters.items():
            raws = adapter.fetch(slug)
            if raws and _belongs_to(company, adapter, raws, known_titles or set()):
                return ats_type, slug
    return None


def _load_targets(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [t for t in data if isinstance(t, dict) and t.get("name")]
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Could not read {path}: {e}")
        return []


def _probed_recently(row: dict, now: datetime) -> bool:
    stamp = row.get("last_probe_at")
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - when < timedelta(days=config.ATS_PROBE_RETRY_DAYS)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=50, help="Companies to probe this run.")
    parser.add_argument("--dry-run", action="store_true", help="Probe, print, write nothing.")
    args = parser.parse_args(argv)
    # Company names come from everywhere; a Windows console (cp1252) cannot print
    # them all, and one unprintable name must not end the run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    now = datetime.now(timezone.utc)
    known = {row["name_normalized"]: row for row in supabase_utils.get_all_companies()}
    titles_by_company = supabase_utils.get_job_titles_by_company()
    targets = _load_targets(TARGETS_PATH)

    seeds = {}
    for name in supabase_utils.get_distinct_job_companies() + [t["name"] for t in targets]:
        norm = dedup.normalize_company(name)
        if norm and norm not in seeds:
            seeds[norm] = name
    new_rows = [{"name": name, "name_normalized": norm}
                for norm, name in seeds.items() if norm not in known]
    if new_rows and not args.dry_run:
        supabase_utils.upsert_companies(new_rows)
    for row in new_rows:
        known[row["name_normalized"]] = row

    pinned = set()
    for target in targets:
        if target.get("ats_type") and target.get("ats_slug"):
            norm = dedup.normalize_company(target["name"])
            pinned.add(norm)
            if not args.dry_run:
                supabase_utils.record_probe(norm, target["ats_type"], target["ats_slug"], "manual")

    to_probe = [row for norm, row in known.items()
                if norm not in pinned and not row.get("ats_type") and not _probed_recently(row, now)]
    to_probe = to_probe[:args.limit]

    found, missed = {}, 0
    for index, row in enumerate(to_probe):
        if index and not args.dry_run:
            time.sleep(config.ATS_COMPANY_DELAY_SECONDS)
        hit = probe(row["name"], known_titles=titles_by_company.get(row["name_normalized"], set()))
        if hit:
            found[hit[0]] = found.get(hit[0], 0) + 1
        else:
            missed += 1
        print(f"{row['name']}: {f'{hit[0]}:{hit[1]}' if hit else 'no feed'}")
        if not args.dry_run:
            if hit:
                supabase_utils.record_probe(row["name_normalized"], hit[0], hit[1], "found")
            else:
                supabase_utils.record_probe(row["name_normalized"], None, None, "not_found")

    print(f"\nProbed {len(to_probe)} | found {sum(found.values())} {found} | no feed {missed}"
          f" | pinned {len(pinned)}" + (" | DRY RUN, nothing written" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
