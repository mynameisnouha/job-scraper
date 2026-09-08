"""Scoring one CV against each archetype: what is there, what is not.

Run it with ``python -m clustering.cv_fit`` after a clustering run. It writes
``output/cv_fit.json``, which the Streamlit page renders.

Two ideas carry the whole module.

**Gaps are weighted by demand.** Missing a skill that 90% of an archetype's
postings require is a different problem from missing one that 8% mention, so
coverage is the share of *demand* met rather than the share of skills held. An
unweighted checklist would rank a rare nice-to-have alongside a universal
must-have and give you a number that moves for the wrong reasons.

**A skill you have is not the same as a skill they can see.** The CV is scored
on what a reader could verify from it, and skills that are true but unwritten
are reported separately - those are the cheapest points available, recoverable
by editing rather than by learning. Both numbers are reported: what the CV
scores today, and the ceiling it reaches if everything already true were simply
written down.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from clustering import settings
from clustering.schema import TAXONOMY_NOTES, Skill

# A skill is only counted as demanded by an archetype above this share of its
# postings. Below it, a "gap" is one or two employers' wishlist rather than a
# property of the archetype, and listing those buries the real gaps.
DEMAND_FLOOR = 0.15

# Below this share, an archetype effectively never asks for a skill, and a
# candidate carrying it is spending page space for nothing. The band between
# this and DEMAND_FLOOR is neither a gap nor dead weight - a minority of
# postings want it - so it is deliberately reported in neither direction.
IGNORED_DEMAND = 0.05

# Tiers 1-3 are visible on the CV; 4 is true but unwritten; 5 is absent. The
# split between 3 and 4 is the one that matters here, because it separates
# "learn this" from "type this".
VISIBLE_TIERS = {1, 2, 3}
UNWRITTEN_TIER = 4

CV_FIT_PATH = os.path.join(settings.OUTPUT_DIR, "cv_fit.json")
CV_PROFILE_CACHE = os.path.join(settings.CACHE_DIR, "cv_profile.json")


class CVSkill(BaseModel):
    skill: Skill
    tier: int = Field(
        ...,
        ge=1,
        le=5,
        description=(
            "Evidence tier. 1 = shipped in production WITH a metric, stated on the CV. "
            "2 = built and working, on the CV, no metric. 3 = coursework or personal "
            "project, on the CV. 4 = true of the candidate but NOT written anywhere on "
            "the CV. 5 = not done at all - omit these entirely."
        ),
    )
    evidence: str = Field(
        "", description="The CV line that backs this, max 15 words. Empty for tier 4."
    )


class CVProfile(BaseModel):
    skills: List[CVSkill] = Field(default_factory=list)
    headline: str = Field("", description="One sentence describing the candidate, max 20 words.")


SYSTEM_PROMPT = """You map a CV onto a fixed skill vocabulary so it can be compared \
against job requirements.

Rules:

1. Only list a skill if the CV or the supplied profile notes actually support it. \
Do not infer a skill because it usually accompanies another one. Listing \
kubernetes because the CV mentions docker is exactly the error to avoid.

2. The tier is about EVIDENCE STRENGTH, and the boundary that matters most is \
between 3 and 4:
   - tiers 1-3 mean a reader could verify it from the CV text itself
   - tier 4 means it is genuinely true of the candidate but a reader of the CV \
would never know
   If the skill appears ANYWHERE in the CV text - including as a bare entry in a \
skills list with no project behind it - it is at most tier 3 and NEVER tier 4. \
Tier 4 is only for skills absent from the CV entirely but supported by the \
profile notes. Never invent tier 4 entries.

3. Omit tier 5 entirely. A skill the candidate does not have should simply be \
absent from the list.

4. Be strict about tier 1: it requires production deployment AND a stated \
metric. "Built a model" with no number is tier 2.

5. The headline names the candidate's focus, not their tenure. Never state a \
total years-of-experience figure in it: the profile notes keep working-student, \
internship and thesis months separate deliberately, and summing them into "N \
years" misrepresents the candidate in the one line a reader sees first.

""" + TAXONOMY_NOTES


def _load_cv_text() -> Dict[str, Any]:
    """The base CV plus the profile notes, which carry the tier-4 information."""
    from db import supabase_utils

    resume = supabase_utils.get_base_resume() or {}

    profile_notes = ""
    profile_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "candidate_profile.json")
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            # profile_v2 carries the evidence index - the only place that records
            # what is true but missing from the CV, which is the whole basis of
            # the "free wins" bucket.
            profile_notes = "\n".join(
                str(v) for k, v in data.items() if k in ("short_profile", "profile_v2")
            )
        except (ValueError, OSError):
            logging.warning("candidate_profile.json unreadable; tier-4 gaps will be missed.")

    return {"resume": resume, "profile_notes": profile_notes}


def extract_cv_profile(force: bool = False) -> Optional[CVProfile]:
    """Map the CV onto the Skill vocabulary. Cached; one LLM call when cold."""
    if not force and os.path.exists(CV_PROFILE_CACHE):
        try:
            with open(CV_PROFILE_CACHE, "r", encoding="utf-8") as fh:
                return CVProfile.model_validate(json.load(fh))
        except (ValueError, OSError):
            logging.warning("CV profile cache unreadable; re-extracting.")

    from scoring.llm_client import screen_client

    payload = _load_cv_text()
    if not payload["resume"]:
        logging.error("No base resume found in Supabase. Run resume.resume_parser first.")
        return None

    prompt = (
        "## CV\n"
        + json.dumps(payload["resume"], ensure_ascii=False, indent=1)
        + "\n\n## PROFILE NOTES (source of truth for what is true but NOT on the CV)\n"
        + payload["profile_notes"]
        + "\n\nMap this candidate onto the skill vocabulary."
    )
    try:
        raw = screen_client.generate_content(
            prompt=prompt, system_prompt=SYSTEM_PROMPT,
            response_format=CVProfile, temperature=0.0,
        )
        profile = CVProfile.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logging.error("CV extraction failed: %s", exc)
        return None

    os.makedirs(settings.CACHE_DIR, exist_ok=True)
    with open(CV_PROFILE_CACHE, "w", encoding="utf-8") as fh:
        json.dump(profile.model_dump(mode="json"), fh, ensure_ascii=False, indent=1)
    return profile


def score_against_cluster(
    cluster_summary: Dict[str, Any], cv: CVProfile, demand_floor: float = DEMAND_FLOOR
) -> Dict[str, Any]:
    """How well this CV answers one archetype's demand.

    Returns the two coverage numbers and the three buckets a reader acts on:
    what is covered, what is true but unwritten, and what is genuinely missing.
    """
    tiers = {s.skill.value: s.tier for s in cv.skills}
    evidence = {s.skill.value: s.evidence for s in cv.skills}

    demanded = [
        (name, share) for name, share in cluster_summary.get("skill_demand", [])
        if share >= demand_floor
    ]
    total_demand = sum(share for _, share in demanded)

    covered, unwritten, missing = [], [], []
    met_visible = met_with_unwritten = 0.0

    for name, share in demanded:
        tier = tiers.get(name)
        row = {"skill": name, "demand": share, "tier": tier,
               "evidence": evidence.get(name, "")}
        if tier in VISIBLE_TIERS:
            covered.append(row)
            met_visible += share
            met_with_unwritten += share
        elif tier == UNWRITTEN_TIER:
            unwritten.append(row)
            met_with_unwritten += share
        else:
            missing.append(row)

    # Ranked by demand: the gap worth closing first is the one the most postings
    # in this archetype ask for, not the one that sounds most impressive.
    for bucket in (covered, unwritten, missing):
        bucket.sort(key=lambda r: -r["demand"])

    # Skills the candidate has that this archetype essentially never asks for.
    # Not a gap - it is page space being spent on the wrong archetype.
    #
    # Measured against IGNORED_DEMAND, not against the demand floor. Using the
    # floor here was wrong in a way worth keeping a note about: a small cluster
    # concentrates its demand into few skills, so almost everything lands below
    # the floor and the candidate's strongest assets got reported as "not
    # wanted" purely for sitting at 12% instead of 15%. Only a skill hardly any
    # posting mentions is genuinely off-target.
    all_demand = dict(cluster_summary.get("skill_demand", []))
    off_target = sorted(
        (
            {"skill": s.skill.value, "tier": s.tier,
             "demand": all_demand.get(s.skill.value, 0.0)}
            for s in cv.skills
            if s.tier in VISIBLE_TIERS
            and all_demand.get(s.skill.value, 0.0) < IGNORED_DEMAND
        ),
        key=lambda r: (r["tier"], -r["demand"]),
    )

    return {
        "cluster": cluster_summary["cluster"],
        "label": cluster_summary.get("label", ""),
        "size": cluster_summary["size"],
        "coverage": met_visible / total_demand if total_demand else 0.0,
        "coverage_if_written": met_with_unwritten / total_demand if total_demand else 0.0,
        "n_demanded": len(demanded),
        "covered": covered,
        "unwritten": unwritten,
        "missing": missing,
        "off_target": off_target,
    }


def build_report(clusters: List[Dict[str, Any]], cv: CVProfile) -> Dict[str, Any]:
    fits = [score_against_cluster(c, cv) for c in clusters]
    fits.sort(key=lambda f: -f["coverage"])
    return {
        "headline": cv.headline,
        "demand_floor": DEMAND_FLOOR,
        "cv_skills": [
            {"skill": s.skill.value, "tier": s.tier, "evidence": s.evidence}
            for s in sorted(cv.skills, key=lambda s: (s.tier, s.skill.value))
        ],
        "fits": fits,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from clustering import results

    summary = results.load_summary()
    if not summary:
        logging.error("No clustering run found. Run `python -m clustering.run` first.")
        return 1

    cv = extract_cv_profile()
    if cv is None:
        return 1

    report = build_report(summary.get("clusters", []), cv)
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    with open(CV_FIT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print(f"\nCV: {report['headline']}")
    print(f"{len(report['cv_skills'])} skills mapped "
          f"({sum(1 for s in report['cv_skills'] if s['tier'] == UNWRITTEN_TIER)} true but unwritten)\n")
    for fit in report["fits"]:
        print(f"{fit['label']:44s} coverage {fit['coverage']:5.0%} "
              f"-> {fit['coverage_if_written']:5.0%} if written "
              f"({len(fit['missing'])} real gaps of {fit['n_demanded']} demanded)")
        if fit["missing"]:
            print("    biggest gaps: " + ", ".join(
                f"{m['skill']} ({m['demand']:.0%} of postings)" for m in fit["missing"][:4]))
    print(f"\nWrote {CV_FIT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
