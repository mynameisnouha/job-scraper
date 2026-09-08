"""Reading the clusters back out in words, which is the only form a CV can use.

Everything here reports on the *unweighted* profile fields. The clustering
happens in an IDF-scaled, block-weighted, PCA-projected space where a coordinate
has no plain-English meaning; quoting those numbers at a human would be precise
and useless. So the geometry decides the grouping, and then the grouping is
described in shares of postings.

Distinctiveness is reported as lift - a cluster's share of a feature divided by
the corpus share. A skill in 95% of a cluster is not interesting if it is in 92%
of everything; a skill in 40% of a cluster and 8% of the corpus is what the CV
should lead with.
"""

import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np

from clustering import features, settings
from clustering.cluster import ClusterResult
from clustering.schema import JobProfile

AMBIGUOUS_MARGIN = 0.15


def _lift(cluster_share: float, corpus_share: float) -> float:
    return cluster_share / corpus_share if corpus_share > 0 else 0.0


def _distinctive_skills(
    indicator: np.ndarray, member_idx: List[int], top_n: int = 8
) -> List[tuple[str, float, float]]:
    """(skill, share in cluster, lift vs corpus), ranked by how much they separate.

    Ranked on share * log(lift) rather than lift alone: pure lift promotes a
    skill held by three postings in a cluster of forty, which is a curiosity, not
    a heading on a CV.
    """
    corpus_share = indicator.mean(axis=0)
    cluster_share = indicator[member_idx].mean(axis=0)
    scored = []
    for j, name in enumerate(features.SKILLS):
        if cluster_share[j] == 0:
            continue
        lift = _lift(cluster_share[j], corpus_share[j])
        weight = cluster_share[j] * np.log(max(lift, 1e-6) + 1e-9) if lift > 0 else -1
        scored.append((name, float(cluster_share[j]), float(lift), float(weight)))
    scored.sort(key=lambda t: -t[3])
    return [(n, s, l) for n, s, l, _ in scored[:top_n]]


def _share(values: List[str], top_n: int = 4) -> List[tuple[str, float]]:
    counts = Counter(values)
    total = max(len(values), 1)
    return [(v, c / total) for v, c in counts.most_common(top_n)]


def summarise_clusters(
    result: ClusterResult,
    job_ids: List[str],
    profiles: Dict[str, JobProfile],
    jobs_by_id: Dict[str, Dict[str, Any]],
    indicator: np.ndarray,
) -> List[Dict[str, Any]]:
    """One dict per cluster, holding everything the report and the CV brief need."""
    out: List[Dict[str, Any]] = []
    for c in range(result.k):
        member_idx = [i for i in range(len(job_ids)) if result.labels[i] == c]
        members = [job_ids[i] for i in member_idx]
        member_profiles = [profiles[j] for j in members]
        scores = [jobs_by_id[j].get("resume_score") or 0 for j in members]

        # Exemplars: closest to the centroid, so they read as the archetype
        # rather than as its edge cases.
        ranked = sorted(member_idx, key=lambda i: -result.margins[i])

        ambiguous = [
            (job_ids[i], float(result.margins[i]))
            for i in member_idx
            if result.margins[i] < AMBIGUOUS_MARGIN
        ]

        # The requirement phrases the postings themselves lead with. Free text,
        # so it only aggregates where wording happens to repeat - anything seen
        # once is a single posting's phrasing, not a property of the cluster.
        phrases = Counter()
        for p in member_profiles:
            for phrase in p.top_requirements:
                phrases[phrase.strip().lower()] += 1
        common_requirements = [(p, n) for p, n in phrases.most_common(10) if n >= 2]

        out.append({
            "cluster": c,
            "size": len(members),
            "job_ids": members,
            "mean_score": float(np.mean(scores)) if scores else 0.0,
            "max_score": max(scores) if scores else 0,
            "n_score_55_plus": sum(1 for s in scores if s >= 55),
            "common_requirements": common_requirements,
            # Every skill this cluster asks for, with the share of postings that
            # ask. distinctive_skills is a top-8 view for reading; this is the
            # full demand curve, which is what a CV has to be scored against -
            # a gap only matters in proportion to how many postings want it.
            "skill_demand": [
                (features.SKILLS[j], float(indicator[member_idx].mean(axis=0)[j]))
                for j in range(len(features.SKILLS))
                if indicator[member_idx].mean(axis=0)[j] > 0
            ],
            "median_years_required": float(np.median(
                [p.years_required for p in member_profiles])) if member_profiles else 0.0,
            "silhouette": float(result.silhouette_per_sample[member_idx].mean()),
            "mean_margin": float(result.margins[member_idx].mean()),
            "distinctive_skills": _distinctive_skills(indicator, member_idx),
            "functions": _share([p.primary_function.value for p in member_profiles]),
            "deliverables": _share([p.deliverable.value for p in member_profiles]),
            "teams": _share([p.team_context.value for p in member_profiles]),
            "stages": _share([p.company_stage.value for p in member_profiles]),
            "seniorities": _share([p.seniority.value for p in member_profiles], 5),
            "domains": _share([p.domain.value for p in member_profiles], 5),
            "german": _share([p.german_required for p in member_profiles], 5),
            "exemplars": [
                {
                    "job_id": job_ids[i],
                    "title": jobs_by_id[job_ids[i]].get("job_title", ""),
                    "company": jobs_by_id[job_ids[i]].get("company", ""),
                    "score": jobs_by_id[job_ids[i]].get("resume_score"),
                    "summary": profiles[job_ids[i]].role_summary,
                    "margin": float(result.margins[i]),
                }
                for i in ranked[:6]
            ],
            "ambiguous": ambiguous,
        })
    return out


def cluster_label(summary: Dict[str, Any]) -> str:
    """A short human name, built from the dominant function and deliverable."""
    fn = summary["functions"][0][0] if summary["functions"] else "unknown"
    deliv = summary["deliverables"][0][0] if summary["deliverables"] else ""
    pretty = {
        "build_ai_product": "AI Product Engineer",
        "ml_engineering": "ML Engineer",
        "build_data_platform": "Data Platform Engineer",
        "analytics_insight": "Analytics / Insight",
        "research_science": "Applied Scientist",
        "consulting_delivery": "Consultant / Delivery",
        "software_engineering": "Software Engineer",
        "ops_automation": "Automation Engineer",
    }.get(fn, fn)
    qualifier = {
        "production_system": "production",
        "recurring_analysis": "recurring analysis",
        "prototype_poc": "prototype",
        "research_output": "research",
        "client_deliverable": "client work",
        "internal_tooling": "internal tooling",
    }.get(deliv, "")
    return f"{pretty} ({qualifier})" if qualifier else pretty


def write_markdown(
    path: str,
    result: ClusterResult,
    sweep_results: Dict[int, ClusterResult],
    summaries: List[Dict[str, Any]],
    corpus_stats: Dict[str, Any],
) -> None:
    lines: List[str] = []
    add = lines.append

    add("# CV archetypes from the job corpus\n")
    add(f"Fitted on **{corpus_stats['n_addressable']} addressable postings** "
        f"(score >= {settings.MIN_SCORE}, no C1-German gate, "
        f"<= {settings.MAX_YEARS_REQUIRED} years required) "
        f"out of {corpus_stats['n_scored']} scored.\n")
    add(f"Mean must-have skills per posting: {corpus_stats['mean_skills']:.1f}. "
        f"Effective dimensionality of the feature space: "
        f"{corpus_stats['effective_dim']:.1f} of {corpus_stats['n_features']} columns "
        f"({corpus_stats['n_components']} PCA components retained).\n")

    add("\n## Choosing k\n")
    add("| k | silhouette | stability (ARI) | sizes | smallest |")
    add("|---|---|---|---|---|")
    for k in sorted(sweep_results):
        r = sweep_results[k]
        add(f"| {k} | {r.silhouette:.3f} | {r.stability:.3f} ± {r.stability_std:.3f} "
            f"| {sorted(r.sizes, reverse=True)} | {min(r.sizes)} |")
    add(f"\n**Selected k = {result.k}** — highest bootstrap stability among the "
        f"options where every cluster is large enough to be worth a maintained CV.\n")

    add("\n## The archetypes\n")
    for s in summaries:
        add(f"\n### Cluster {s['cluster']} — {cluster_label(s)}\n")
        add(f"**{s['size']} postings** · mean score {s['mean_score']:.1f} "
            f"(max {s['max_score']}, {s['n_score_55_plus']} at 55+) · "
            f"mean assignment margin {s['mean_margin']:.2f}\n")

        add("\n*Leads with:* " + ", ".join(
            f"`{n}` ({sh:.0%}, {l:.1f}x corpus)" for n, sh, l in s["distinctive_skills"]) + "\n")
        add("\n*Function:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["functions"]))
        add("  \n*Deliverable:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["deliverables"]))
        add("  \n*Team:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["teams"]))
        add("  \n*Employer:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["stages"]))
        add("  \n*Seniority:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["seniorities"]))
        add("  \n*Domain:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["domains"]))
        add("  \n*German asked:* " + ", ".join(f"{v} {p:.0%}" for v, p in s["german"]))
        add(f"  \n*Median years required:* {s['median_years_required']:.0f}\n")

        if s["common_requirements"]:
            add("\n*Requirements these postings lead with:*\n")
            for phrase, count in s["common_requirements"]:
                add(f"- {phrase} ({count} postings)")
            add("")

        add("\n*Most representative postings:*\n")
        for e in s["exemplars"]:
            add(f"- **{e['title']}** — {e['company']} (score {e['score']}, "
                f"margin {e['margin']:.2f})  \n  {e['summary']}")
        if s["ambiguous"]:
            add(f"\n*{len(s['ambiguous'])} postings sit near a boundary "
                f"(margin < {AMBIGUOUS_MARGIN}) and could equally belong elsewhere.*")
        add("")

    add("\n## How to use this\n")
    add("Each cluster becomes one base CV. Tailoring per job still runs on top of "
        "it — the archetype decides which document the tailoring starts from, "
        "which is the part an LLM rewrite cannot fix after the fact.\n")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_json(
    path: str,
    result: ClusterResult,
    sweep_results: Dict[int, ClusterResult],
    summaries: List[Dict[str, Any]],
    corpus_stats: Dict[str, Any],
) -> None:
    """The same summaries, structured, for anything that is not a human reading prose.

    The Streamlit page reads this rather than parsing archetypes.md, so the
    report stays a document and the UI stays a view over data.
    """
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "min_score": settings.MIN_SCORE,
            "max_years_required": settings.MAX_YEARS_REQUIRED,
            "excluded_german_levels": sorted(settings.EXCLUDE_GERMAN_LEVELS),
            "block_weights": settings.BLOCK_WEIGHTS,
        },
        "corpus": corpus_stats,
        "chosen_k": result.k,
        "silhouette": result.silhouette,
        "stability": result.stability,
        "stability_std": result.stability_std,
        "ambiguous_margin": AMBIGUOUS_MARGIN,
        "k_sweep": [
            {
                "k": k,
                "silhouette": sweep_results[k].silhouette,
                "stability": sweep_results[k].stability,
                "stability_std": sweep_results[k].stability_std,
                "sizes": sorted(sweep_results[k].sizes, reverse=True),
            }
            for k in sorted(sweep_results)
        ],
        "clusters": [dict(s, label=cluster_label(s)) for s in summaries],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)


def write_assignments(
    path: str,
    result: ClusterResult,
    job_ids: List[str],
    profiles: Dict[str, JobProfile],
    jobs_by_id: Dict[str, Dict[str, Any]],
    summaries: List[Dict[str, Any]],
) -> None:
    labels = {s["cluster"]: cluster_label(s) for s in summaries}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "job_id", "cluster", "archetype", "margin", "confident",
            "score", "title", "company", "primary_function", "deliverable",
            "team_context", "seniority", "german_required", "role_summary", "job_url",
        ])
        for i, job_id in enumerate(job_ids):
            p = profiles[job_id]
            job = jobs_by_id[job_id]
            c = int(result.labels[i])
            writer.writerow([
                job_id, c, labels[c], f"{result.margins[i]:.3f}",
                "yes" if result.margins[i] >= AMBIGUOUS_MARGIN else "no",
                job.get("resume_score"), job.get("job_title"), job.get("company"),
                p.primary_function.value, p.deliverable.value, p.team_context.value,
                p.seniority.value, p.german_required, p.role_summary, job.get("job_url"),
            ])
