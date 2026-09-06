"""Assemble everything one application needs into a folder.

At 100 applications a week, five minutes each is over eight hours, so the point of
this step is the sixty seconds spent hunting for the same five answers every time:
Gehaltsvorstellung, earliest start, permit status, notice period, relocation.

No LLM and no generation. The pitch was written when the job was scored, the CV is
one of the PDFs that already exist, and the answers are constants. This module only
collects them.

Streamlit-free so the UI can call it and a test can too.
"""

import json
import logging
import os
import re
import shutil
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import config

OUTPUT_ROOT = os.path.join("output", "applications")

# The answers are personal — salary expectation, residence-permit status, notice
# period. This repository is public, so they live in an untracked JSON file beside
# candidate_profile.json rather than in config.py, which the plan suggested before
# the repository's visibility was checked. config.py holds the path, not the values.
ANSWER_FIELDS: List[Tuple[str, str]] = [
    ("gehaltsvorstellung", "Gehaltsvorstellung / salary expectation"),
    ("eintrittstermin", "Frühestmöglicher Eintrittstermin / earliest start date"),
    ("kuendigungsfrist", "Kündigungsfrist / notice period"),
    ("aufenthaltstitel", "Aufenthaltstitel / work authorization"),
    ("relocation", "Umzugsbereitschaft / relocation"),
    ("arbeitszeit", "Arbeitszeit / working hours"),
    ("standort", "Standort / current location"),
]

_SALARY_FIGURE = re.compile(r"\d[\d.,]{3,}")


def load_answers(path: Optional[str] = None) -> Dict[str, Any]:
    """The standing answers. Returns {} when the file is absent — a pack without
    them is still worth writing, and the reader is told what is missing."""
    path = path or getattr(config, "APPLICATION_ANSWERS_PATH", "application_answers.json")
    if not os.path.exists(path):
        logging.warning(f"No application answers at '{path}' — copy "
                        f"application_answers.json.example and fill it in.")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Could not read application answers from '{path}': {e}")
        return {}


def slugify(value: Optional[str], fallback: str = "unknown") -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", (value or "")).strip("_")
    return text[:60] or fallback


def choose_cv(job_title: Optional[str], routes=None, library_dir: Optional[str] = None,
              default: Optional[str] = None) -> Optional[str]:
    """Pick a pre-made CV by keyword on the job title.

    Routes are ordered [(keyword, filename), ...] and the first match wins, so the
    specific ones belong first. Returns None rather than guessing when nothing
    matches and no default is set — sending the wrong CV is worse than being asked
    to pick one.
    """
    routes = routes if routes is not None else getattr(config, "CV_KEYWORD_ROUTES", [])
    library_dir = library_dir or getattr(config, "CV_LIBRARY_DIR", "cv")
    default = default if default is not None else getattr(config, "CV_DEFAULT", None)

    title = (job_title or "").lower()
    for keyword, filename in routes:
        if keyword.lower() in title:
            return os.path.join(library_dir, filename)
    if default:
        return os.path.join(library_dir, default)
    return None


def stated_salary(breakdown: Dict[str, Any]) -> Optional[str]:
    """The band the JD stated, if it stated one with a number in it.

    The scorer fills salary_band with prose like "Not stated — cannot assess" when
    there is no figure, so a digit is the test.
    """
    band = str((breakdown or {}).get("salary_band") or "").strip()
    return band if band and _SALARY_FIGURE.search(band) else None


def build_answers_md(job: Dict[str, Any], breakdown: Dict[str, Any],
                     answers: Dict[str, Any]) -> str:
    lines = [f"# Application answers — {job.get('company') or 'Unknown company'}",
             "",
             f"**{job.get('job_title') or 'Unknown role'}**  ",
             f"{job.get('job_url') or ''}",
             ""]

    if not answers:
        lines += ["> No `application_answers.json` found. Copy",
                  "> `application_answers.json.example`, fill it in, and regenerate.", ""]

    for key, label in ANSWER_FIELDS:
        value = answers.get(key)
        lines.append(f"**{label}**  ")
        lines.append(f"{value}" if value else "_not set_")
        lines.append("")

    band = stated_salary(breakdown)
    if band:
        # Worth surfacing next to the standing figure: Arbeitsagentur states a band
        # for many postings, and an answer that ignores it is a worse answer.
        lines += ["---", "",
                  "## This posting states a salary band",
                  "",
                  f"> {band}",
                  "",
                  "Check your Gehaltsvorstellung against it before sending.", ""]

    for key, value in (answers.get("extra") or {}).items():
        lines += [f"**{key}**  ", f"{value}", ""]

    return "\n".join(lines).rstrip() + "\n"


def build_checklist_md(job: Dict[str, Any], breakdown: Dict[str, Any]) -> str:
    """The fixable-before-applying items for this job, as tickable tasks."""
    import job_view  # local import: keeps this module importable without Streamlit deps

    lines = [f"# Before sending — {job.get('company') or 'Unknown company'}", ""]

    wins = job_view.quick_wins(breakdown)
    if wins:
        lines.append("## Fix first (the CV does not show these yet)")
        lines.append("")
        lines += [f"- [ ] {item}" for item in wins]
        lines.append("")
    else:
        lines += ["No CV-fixable gaps were recorded for this job.", ""]

    gaps = job_view.cons(breakdown)
    if gaps:
        lines += ["## Known gaps — be ready to answer on these", ""]
        lines += [f"- {item}" for item in gaps]
        lines.append("")

    lines += ["## Always", "",
              "- [ ] CV filename says who you are, not `resume_final_v3`",
              "- [ ] Answers above pasted into the form",
              "- [ ] Applied direct rather than through an aggregator where possible",
              "- [ ] Marked applied in the dashboard", ""]
    return "\n".join(lines).rstrip() + "\n"


def pack_directory(job: Dict[str, Any], root: Optional[str] = None,
                   today: Optional[date] = None) -> str:
    root = root or OUTPUT_ROOT
    stamp = (today or date.today()).isoformat()
    return os.path.join(root, f"{slugify(job.get('company'))}_{stamp}")


def build_pack(job: Dict[str, Any], root: Optional[str] = None,
               answers: Optional[Dict[str, Any]] = None,
               today: Optional[date] = None) -> Dict[str, Any]:
    """Write the folder. Returns {"path", "files", "warnings"}.

    Never raises for missing content: a pack with four of five parts is still worth
    having in front of you, and the fifth is named in `warnings` so you know what to
    do by hand.
    """
    breakdown = job.get("score_breakdown") or {}
    answers = load_answers() if answers is None else answers

    directory = pack_directory(job, root=root, today=today)
    os.makedirs(directory, exist_ok=True)
    written, warnings = [], []

    def write(name: str, content: str) -> None:
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            handle.write(content)
        written.append(name)

    write("answers.md", build_answers_md(job, breakdown, answers))
    if not answers:
        warnings.append("No application_answers.json — answers.md is a blank template.")

    write("checklist.md", build_checklist_md(job, breakdown))

    pitch = (job.get("why_me_pitch") or "").strip()
    if pitch:
        write("why_me.txt", pitch + "\n")
    else:
        warnings.append("No why-me pitch stored for this job — write the intro by hand.")

    cv_source = choose_cv(job.get("job_title"))
    if cv_source and os.path.exists(cv_source):
        target = os.path.basename(cv_source)
        shutil.copy2(cv_source, os.path.join(directory, target))
        written.append(target)
    elif cv_source:
        warnings.append(f"CV '{cv_source}' is routed for this title but the file is missing.")
    else:
        warnings.append("No CV route matched this job title and no default is set "
                        "(CV_KEYWORD_ROUTES / CV_DEFAULT in config.py).")

    return {"path": directory, "files": written, "warnings": warnings}
