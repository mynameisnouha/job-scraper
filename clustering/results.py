"""Reading a finished clustering run back off disk.

Deliberately free of Streamlit, matching review/calibration.py: the logic is
testable on its own and ui_app.py only renders what these functions return.

The run artefacts are the interface. Nothing here re-clusters or calls an LLM,
so opening the page is cheap and never spends money - a page that silently
triggered a 250-call extraction because someone clicked a tab would be a bad
surprise. Re-running is an explicit `python -m clustering.run`.
"""

import csv
import json
import os
from typing import Any, Dict, List, Optional

from clustering import settings

CLUSTERS_PATH = os.path.join(settings.OUTPUT_DIR, "clusters.json")
ASSIGNMENTS_PATH = os.path.join(settings.OUTPUT_DIR, "assignments.csv")
CV_FIT_PATH = os.path.join(settings.OUTPUT_DIR, "cv_fit.json")


def available() -> bool:
    """Has a run produced output yet?"""
    return os.path.exists(CLUSTERS_PATH) and os.path.exists(ASSIGNMENTS_PATH)


def load_summary() -> Optional[Dict[str, Any]]:
    if not os.path.exists(CLUSTERS_PATH):
        return None
    try:
        with open(CLUSTERS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def load_assignments() -> List[Dict[str, Any]]:
    """Every clustered job, with score coerced to int for sorting."""
    if not os.path.exists(ASSIGNMENTS_PATH):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(ASSIGNMENTS_PATH, "r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    row["score"] = int(row.get("score") or 0)
                except (TypeError, ValueError):
                    row["score"] = 0
                try:
                    row["margin"] = float(row.get("margin") or 0)
                except (TypeError, ValueError):
                    row["margin"] = 0.0
                row["cluster"] = int(row.get("cluster") or 0)
                rows.append(row)
    except OSError:
        return []
    return rows


def load_cv_fit() -> Optional[Dict[str, Any]]:
    """The CV-vs-archetype scoring, if `python -m clustering.cv_fit` has been run."""
    if not os.path.exists(CV_FIT_PATH):
        return None
    try:
        with open(CV_FIT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def fit_for_cluster(cv_fit: Optional[Dict[str, Any]], cluster: int) -> Optional[Dict[str, Any]]:
    if not cv_fit:
        return None
    for fit in cv_fit.get("fits", []):
        if fit.get("cluster") == cluster:
            return fit
    return None


def fit_is_stale(cv_fit: Optional[Dict[str, Any]], summary: Optional[Dict[str, Any]]) -> bool:
    """Was the CV scored against a different clustering run than the one on screen?

    Both files are written independently, so a re-run of the clustering leaves an
    old cv_fit.json in place pointing at cluster ids that may now mean something
    else. Showing those numbers against the current archetypes would be worse
    than showing nothing, so the UI checks before rendering them.
    """
    if not cv_fit or not summary:
        return False
    fitted = {f.get("cluster") for f in cv_fit.get("fits", [])}
    current = {c.get("cluster") for c in summary.get("clusters", [])}
    if fitted != current:
        return True
    # Labels are derived from cluster contents, so a changed label means the
    # cluster itself changed even when the ids happen to line up.
    labels = {c["cluster"]: c.get("label") for c in summary.get("clusters", [])}
    return any(f.get("label") != labels.get(f.get("cluster")) for f in cv_fit.get("fits", []))


def jobs_in_cluster(
    assignments: List[Dict[str, Any]], cluster: int, confident_only: bool = False
) -> List[Dict[str, Any]]:
    """Cluster members, highest score first."""
    rows = [r for r in assignments if r["cluster"] == cluster]
    if confident_only:
        rows = [r for r in rows if r.get("confident") == "yes"]
    return sorted(rows, key=lambda r: -r["score"])


def top_skills(cluster_summary: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    """Distinctive skills as dicts, ready for a table.

    Lift is carried through rather than dropped for tidiness: a skill present in
    most of a cluster means nothing if it is present in most of the corpus, and
    the lift column is the only thing that distinguishes those two cases.
    """
    return [
        {"Skill": name, "Share of cluster": share, "Lift vs corpus": lift}
        for name, share, lift in cluster_summary.get("distinctive_skills", [])[:limit]
    ]


def context_rows(cluster_summary: Dict[str, Any]) -> List[Dict[str, str]]:
    """The non-skill half of a cluster's identity, flattened for display."""
    fields = [
        ("Function", "functions"),
        ("Deliverable", "deliverables"),
        ("Team", "teams"),
        ("Employer", "stages"),
        ("Seniority", "seniorities"),
        ("Domain", "domains"),
        ("German asked", "german"),
    ]
    rows = []
    for label, key in fields:
        parts = cluster_summary.get(key) or []
        if parts:
            rows.append({
                "Dimension": label,
                "Breakdown": ", ".join(f"{value} {share:.0%}" for value, share in parts),
            })
    return rows


def health(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Whether this run's partition is trustworthy enough to build CVs on.

    Thresholds are judgement calls, stated here rather than buried in the UI so
    they can be argued with. Stability is the gate that matters at a few hundred
    samples; the silhouette is reported but never gates, because a low silhouette
    is the expected state for job postings and would fire on every honest run.
    """
    stability = summary.get("stability") or 0.0
    if stability >= 0.85:
        verdict, tone = "Stable — the same clusters survive resampling.", "good"
    elif stability >= 0.65:
        verdict, tone = (
            "Moderately stable — treat cluster edges as soft, the cores as real.", "warn"
        )
    else:
        verdict, tone = (
            "Unstable — this partition moves when the data does. Re-run with a "
            "different k, or gather more postings before writing CVs from it.", "bad"
        )
    return {"stability": stability, "verdict": verdict, "tone": tone}
