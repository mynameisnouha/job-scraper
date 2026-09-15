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


def save(job: Dict[str, Any], result: Any,
         prior_rounds: Optional[List[Dict[str, Any]]] = None) -> str:
    """Persist a finished run: the documents, the argument, and the caveats.

    `prior_rounds` carries the history of a run being continued. A continuation
    returns only the rounds it actually ran, so without this the file would be
    rewritten as though the earlier rounds never happened - losing the argument
    that produced the draft being refined, which is the part worth keeping.
    """
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job": {
            "job_id": job.get("job_id"), "job_title": job.get("job_title"),
            "company": job.get("company"), "job_url": job.get("job_url"),
            "original_score": job.get("resume_score"),
        },
        "stopped_because": result.stopped_because,
        "judge_failed": getattr(result, "judge_failed", False),
        # Carried so the run can be extended later without re-raising objections
        # the earlier rounds already answered.
        "seen_keys": list(getattr(result, "seen_keys", [])),
        "structural_map": dict(getattr(result, "structural_map", {})),
        "structural": result.structural,
        # A paused run has to survive the page reload that shows its questions,
        # so the questions go on disk with everything else rather than living in
        # session state. Answering them elsewhere and coming back should still
        # work.
        "awaiting_answers": getattr(result, "awaiting_answers", False),
        "questions": [q.model_dump(mode="json") for q in getattr(result, "questions", [])],
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
        "rounds": list(prior_rounds or []) + [
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

        # The employment timeline: role, employer, dates. Supplied here rather
        # than left to the writer because dates are not competence claims and
        # therefore carry no fact ids - which meant nothing checked them. The
        # verifier walks summary, bullets and letter paragraphs, so a heading
        # reading "01/2026 - Present" for a role that ended in August would pass
        # every check in the package. Giving the writer the authoritative dates
        # removes the guesswork rather than adding another rule about it.
        if resume.get("experience"):
            details["experience_timeline"] = [
                {
                    "role": e.get("job_title", ""),
                    "employer": e.get("company", ""),
                    "dates": f"{e.get('start_date', '')} - {e.get('end_date', '')}".strip(" -"),
                }
                for e in resume["experience"]
            ]
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
