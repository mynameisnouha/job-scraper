"""Saving generated applications, and gathering the non-fact inputs they need.

Two jobs, both about the edges of the pipeline rather than its middle: what goes
on disk when a run finishes, and where the header details come from.

Generated applications are kept per job id. A regenerated application overwrites
the previous one - versions of a CV you did not send are not worth keeping, and
the fact base, which is the part with lasting value, is stored separately and
only ever grows.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tailor import settings
from tailor.documents import Application, render_cover_letter, render_cv


def _path(job_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(job_id))
    return os.path.join(settings.OUTPUT_DIR, f"{safe}.json")


def save(job: Dict[str, Any], result: Any) -> str:
    """Persist a finished run: the documents, the argument, and the caveats."""
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job": {
            "job_id": job.get("job_id"), "job_title": job.get("job_title"),
            "company": job.get("company"), "job_url": job.get("job_url"),
            "original_score": job.get("resume_score"),
        },
        "stopped_because": result.stopped_because,
        "structural": result.structural,
        "score_before": result.score_before,
        "score_after": result.score_after,
        "score_note": result.score_note,
        "cv_text": render_cv(result.application.cv) if result.application else "",
        "cover_letter_text": (
            render_cover_letter(result.application.cover_letter) if result.application else ""
        ),
        "application": (
            result.application.model_dump(mode="json") if result.application else None
        ),
        "rounds": [
            {
                "number": r.number,
                "repairs": r.repairs,
                "verifier_ok": r.verification.ok if r.verification else False,
                "verifier_problems": r.verification.summary() if r.verification else "",
                "new_objections": r.new_objections,
                "would_interview": r.verdict.would_interview if r.verdict else None,
                "standout": r.verdict.standout if r.verdict else "",
                "ai_tells": r.verdict.ai_tells if r.verdict else [],
            }
            for r in result.rounds
        ],
    }
    path = _path(job.get("job_id"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def load(job_id: str) -> Optional[Dict[str, Any]]:
    path = _path(job_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def personal_details() -> Dict[str, Any]:
    """Name, contact, availability - the parts of an application that are not claims.

    These come from the stored CV and application_answers.json rather than from
    the fact base, because they are not evidence of anything and should not be
    citable. Keeping them out means the verifier's "every line cites a fact" rule
    can stay absolute instead of growing exceptions for the address block.
    """
    details: Dict[str, Any] = {}

    try:
        from db import supabase_utils

        resume = supabase_utils.get_base_resume() or {}
        for key in ("name", "email", "phone", "location", "links", "languages",
                    "education", "certifications"):
            if resume.get(key):
                details[key] = resume[key]
    except Exception as exc:  # noqa: BLE001
        logging.warning("Could not read the base resume for personal details: %s", exc)

    answers_path = os.path.join(
        os.path.dirname(settings.FACTS_PATH), "application_answers.json"
    )
    if os.path.exists(answers_path):
        try:
            with open(answers_path, "r", encoding="utf-8") as fh:
                answers = json.load(fh)
            details["availability"] = {
                k: v for k, v in answers.items()
                if k in ("eintrittstermin", "arbeitszeit", "standort", "relocation")
            }
        except (ValueError, OSError):
            logging.warning("application_answers.json unreadable; availability omitted.")

    return details
