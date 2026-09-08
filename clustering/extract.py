"""One cheap LLM call per posting, turning prose into a JobProfile.

This is the step that makes the clustering work at all. The alternative -
embedding the job description directly - was tried first and failed: TF-IDF over
raw JDs scored a 0.04 silhouette and produced clusters that were ~70% pure by
*language*, separating German ads from English ads rather than Data Engineer
from AI Engineer. Half this corpus is German, so any text-similarity method
spends its variance on that split before it reaches anything about the work.

Extracting to a closed vocabulary first throws the surface language away and
keeps the requirements, which is the only part a CV responds to.

Results are cached by job_id, so a re-run costs nothing and only new postings are
sent to the model.
"""

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from scoring.llm_client import screen_client
from clustering import settings
from clustering.schema import TAXONOMY_NOTES, JobProfile

SYSTEM_PROMPT = """You normalise job postings into a fixed schema so that different \
postings for the same kind of work end up looking the same.

Rules that matter more than they look:

1. Extract what the posting SAYS, not what a role with this title usually wants. \
If a "Data Scientist" ad never mentions SQL, SQL is not a must-have. Padding \
profiles with the expected stack is the single fastest way to make every job \
look identical, which destroys the whole exercise.

2. must_have vs nice_to_have follows the posting's own framing: requirements \
sections, "you bring", "Sie bringen mit", "Anforderungen" are must-haves; \
"von Vorteil", "wuenschenswert", "a plus", "bonus" are nice-to-haves.

3. primary_function is about how the week is actually spent, not the title. \
An "AI Engineer" who maintains ingestion pipelines is build_data_platform. \
A "Data Scientist" who ships a recommender into production is build_ai_product. \
Read the responsibilities, not the header.

4. seniority follows the responsibilities. A "Senior" title asking for 2 years \
and offering mentoring is mid_3_5, not senior_5_plus.

5. german_required is the level the job DEMANDS. A posting written in German that \
never states a language requirement is "unstated", NOT "C1-fluent". Never infer \
the requirement from the language of the ad.

6. Postings are German or English. Always answer in English regardless.

Be decisive. Every single-choice field must be filled with the closest match.

""" + TAXONOMY_NOTES

USER_TEMPLATE = """## POSTING

Title: {title}
Company: {company}
Location: {location}
Advertised level: {level}

---
{description}
---

Normalise this posting into the schema."""

_cache_lock = threading.Lock()


def _load_cache() -> Dict[str, Any]:
    if not os.path.exists(settings.PROFILE_CACHE):
        return {}
    try:
        with open(settings.PROFILE_CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        logging.warning("Profile cache unreadable; starting fresh.")
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    os.makedirs(settings.CACHE_DIR, exist_ok=True)
    tmp = settings.PROFILE_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, settings.PROFILE_CACHE)


def _extract_one(job: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    job_id = job["job_id"]
    prompt = USER_TEMPLATE.format(
        title=job.get("job_title") or "N/A",
        company=job.get("company") or "N/A",
        location=job.get("location") or "N/A",
        level=job.get("level") or "not stated",
        description=(job.get("description") or "")[: settings.MAX_DESCRIPTION_CHARS],
    )
    try:
        raw = screen_client.generate_content(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            response_format=JobProfile,
            temperature=0.0,
            # No prompt caching: the rubric above is identical on every call, but
            # it is well under the minimum cacheable prefix length, so asking for
            # a cache only produces a "no cache activity" warning per job.
            cache_system=False,
        )
        profile = JobProfile.model_validate_json(raw)
        return job_id, profile.model_dump(mode="json")
    except ValidationError as exc:
        logging.warning("Profile failed validation for %s: %s", job_id, exc)
    except Exception as exc:  # noqa: BLE001 - one bad job must not kill the run
        logging.warning("Extraction failed for %s: %s", job_id, exc)
    return job_id, None


def extract_profiles(
    jobs: List[Dict[str, Any]], force: bool = False
) -> Dict[str, JobProfile]:
    """Extract a JobProfile per job, reusing the cache for anything already seen."""
    cache = {} if force else _load_cache()
    todo = [j for j in jobs if j["job_id"] not in cache]
    logging.info(
        "Extraction: %d jobs, %d cached, %d to fetch", len(jobs), len(jobs) - len(todo), len(todo)
    )

    if todo:
        done = 0
        with ThreadPoolExecutor(max_workers=settings.EXTRACTION_WORKERS) as pool:
            futures = {pool.submit(_extract_one, job): job for job in todo}
            for future in as_completed(futures):
                job_id, payload = future.result()
                done += 1
                if payload is not None:
                    with _cache_lock:
                        cache[job_id] = payload
                        # Checkpoint periodically: an interrupted run should not
                        # throw away the calls it already paid for.
                        if done % 20 == 0:
                            _save_cache(cache)
                if done % 20 == 0 or done == len(todo):
                    logging.info("  extracted %d/%d", done, len(todo))
        _save_cache(cache)

    profiles: Dict[str, JobProfile] = {}
    for job in jobs:
        payload = cache.get(job["job_id"])
        if payload is None:
            continue
        try:
            profiles[job["job_id"]] = JobProfile.model_validate(payload)
        except ValidationError:
            # A cached profile from an older schema version. Drop it rather than
            # silently clustering on a stale shape.
            logging.warning("Stale cached profile for %s, ignoring.", job["job_id"])
    logging.info("Usable profiles: %d/%d", len(profiles), len(jobs))
    return profiles
