"""Per-job bundle: the only folder the fenced auto-apply worker may read.

Everything the worker needs is copied in (job, answers, CV, optional letter,
playbook) and nothing else, so a prompt-injected page cannot talk the worker
into reading repo secrets. The only credentials written are the ATS account's.
"""
import json
import logging
import os
import shutil
from typing import Mapping, Optional

import config
from apply import documents, playbooks
from review import application_pack
from tailor import store


class BundleError(Exception):
    """The bundle cannot be built; the runner stores the reason as `failed`."""


def _safe_dir(work_root: str, job_id) -> str:
    """<work_root>/<job_id>, refusing ids that could point outside work_root.

    The bundle dir is emptied with rmtree, so a bad id (empty, '..', a path) must
    never resolve to work_root or anything above it.
    """
    jid = str(job_id or "").strip()
    if not jid or ".." in jid or "/" in jid or "\\" in jid or jid == ".":
        raise BundleError(f"invalid job id {job_id!r}")
    root = os.path.abspath(work_root)
    path = os.path.abspath(os.path.join(root, jid))
    if os.path.dirname(path) != root:
        raise BundleError(f"invalid job id {job_id!r}")
    return path


def build(job: dict, work_root: str, env: Mapping[str, str] = os.environ,
          auto_submit: Optional[bool] = None) -> str:
    """Write the bundle for `job` and return its directory.

    `job` is the full row (incl. description, why_me_pitch, score_breakdown); the
    runner supplies it, nothing is fetched here. auto_submit=None derives the flag
    from the ATS; False forces it off (review runs).
    """
    bundle = _safe_dir(work_root, job.get("job_id"))
    job_id = str(job["job_id"]).strip()

    description = (job.get("description") or "").strip()
    if not description:
        raise BundleError("job description missing")
    cv_src = application_pack.choose_cv(job.get("job_title")) or documents.generic_cv()
    if not cv_src or not os.path.isfile(cv_src):
        raise BundleError(f"CV missing: {cv_src}")

    if os.path.isdir(bundle):
        shutil.rmtree(bundle)
    os.makedirs(bundle)

    cv_name = "cv.pdf" if cv_src.lower().endswith(".pdf") else os.path.basename(cv_src)
    cv_dst = os.path.join(bundle, cv_name)
    shutil.copyfile(cv_src, cv_dst)

    letter_path = None
    tailored = store.load(job_id) or {}
    letter = (tailored.get("cover_letter_text") or "").strip()
    if letter:
        letter_path = os.path.join(bundle, "cover_letter.txt")
        with open(letter_path, "w", encoding="utf-8") as fh:
            fh.write(letter)

    generic_path = None
    generic_src = config.GENERIC_COVER_LETTER_PATH or documents.generic_cover_letter()
    if generic_src and os.path.isfile(generic_src):
        ext = os.path.splitext(generic_src)[1]
        generic_path = os.path.join(bundle, f"generic_cover_letter{ext}")
        shutil.copyfile(generic_src, generic_path)

    ats = (job.get("ats_type") or "").strip().lower()
    if auto_submit is None:
        auto_submit = ats in config.AUTO_SUBMIT_ATS
    breakdown = job.get("score_breakdown") or {}

    data = {
        "job_id": job_id,
        "job_title": job.get("job_title"),
        "company": job.get("company"),
        "apply_url": job.get("apply_url"),
        "ats_type": ats,
        "auto_submit": bool(auto_submit),
        "pitch": job.get("why_me_pitch"),
        "lead_with": breakdown.get("differentiators") or [],
        "description": description,
        "files": {
            "cv": os.path.abspath(cv_dst),
            "cover_letter": os.path.abspath(letter_path) if letter_path else None,
            "generic_cover_letter": os.path.abspath(generic_path) if generic_path else None,
        },
        "account": {"email": env.get("ATS_EMAIL"), "password": env.get("ATS_PASSWORD")},
    }
    with open(os.path.join(bundle, "job.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(bundle, "answers.json"), "w", encoding="utf-8") as fh:
        json.dump(application_pack.load_answers(), fh, indent=2, ensure_ascii=False)

    parts = []
    for p in playbooks.paths_for(ats):
        with open(p, "r", encoding="utf-8") as fh:
            parts.append(fh.read())
    with open(os.path.join(bundle, "playbook.md"), "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(parts))

    logging.info(f"Built apply bundle for {job_id} at {bundle}")
    return bundle
