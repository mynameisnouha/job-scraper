"""End-to-end run: python -m clustering.run [--force-extract] [--k N]

Writes a markdown report and a per-job assignment CSV into clustering/output/.
"""

import argparse
import logging
import os
import sys

import numpy as np

from clustering import cluster, corpus, features, report, settings
from clustering.extract import extract_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description="Cluster addressable jobs into CV archetypes.")
    parser.add_argument("--force-extract", action="store_true",
                        help="Re-run the LLM extraction instead of using the cache.")
    parser.add_argument("--k", type=int, default=None,
                        help="Force a specific k instead of selecting one.")
    parser.add_argument("--min-cluster-size", type=int, default=12,
                        help="Reject any k that produces a cluster smaller than this.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )

    scored = corpus.fetch_scored_jobs()
    jobs = corpus.select_addressable(scored)
    if len(jobs) < 20:
        logging.error("Only %d addressable jobs — too few to fit archetypes. "
                      "Lower CLUSTER_MIN_SCORE or scrape more.", len(jobs))
        return 1

    profiles = extract_profiles(jobs, force=args.force_extract)
    if len(profiles) < 20:
        logging.error("Only %d usable profiles extracted.", len(profiles))
        return 1

    job_ids = [j["job_id"] for j in jobs if j["job_id"] in profiles]
    jobs_by_id = {j["job_id"]: j for j in jobs}

    space = features.build(profiles, job_ids)
    indicator = features.raw_indicator_matrix(profiles, job_ids)
    reduced, pca = cluster.reduce_dimensions(space.matrix)

    logging.info("Feature space: %d jobs x %d columns -> %d PCA components "
                 "(effective dimensionality %.1f)",
                 space.matrix.shape[0], space.matrix.shape[1], reduced.shape[1],
                 features.effective_dimensionality(space.matrix))

    sweep = cluster.sweep(reduced)
    print("\n k   silhouette   stability (ARI)      sizes")
    for k in sorted(sweep):
        r = sweep[k]
        print(f" {k}   {r.silhouette:8.3f}   {r.stability:.3f} +/- {r.stability_std:.3f}"
              f"   {sorted(r.sizes, reverse=True)}")

    chosen = args.k or cluster.recommend_k(sweep, min_cluster_size=args.min_cluster_size)
    if chosen is None:
        logging.error("No k produced clusters all at least %d jobs.", args.min_cluster_size)
        return 1
    result = sweep.get(chosen) or cluster.fit(reduced, chosen)
    print(f"\nSelected k = {chosen}")

    summaries = report.summarise_clusters(result, space.job_ids, profiles, jobs_by_id, indicator)

    corpus_stats = {
        "n_scored": len(scored),
        "n_addressable": len(jobs),
        "mean_skills": features.mean_profile_size(profiles),
        "effective_dim": features.effective_dimensionality(space.matrix),
        "n_features": space.matrix.shape[1],
        "n_components": reduced.shape[1],
    }

    md_path = os.path.join(settings.OUTPUT_DIR, "archetypes.md")
    csv_path = os.path.join(settings.OUTPUT_DIR, "assignments.csv")
    json_path = os.path.join(settings.OUTPUT_DIR, "clusters.json")
    report.write_markdown(md_path, result, sweep, summaries, corpus_stats)
    report.write_assignments(csv_path, result, space.job_ids, profiles, jobs_by_id, summaries)
    report.write_json(json_path, result, sweep, summaries, corpus_stats)

    print()
    for s in summaries:
        confident = sum(1 for m in result.margins[[i for i in range(len(space.job_ids))
                                                   if result.labels[i] == s["cluster"]]]
                        if m >= report.AMBIGUOUS_MARGIN)
        print(f"cluster {s['cluster']}: {report.cluster_label(s):42s} "
              f"n={s['size']:3d}  mean_score={s['mean_score']:5.1f}  "
              f"confident={confident}/{s['size']}")
        print("    leads with: " + ", ".join(
            f"{n} ({sh:.0%}, {l:.1f}x)" for n, sh, l in s["distinctive_skills"][:5]))

    print(f"\nWrote {md_path}\nWrote {csv_path}\nWrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
