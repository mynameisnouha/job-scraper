"""Knobs for the clustering run, in one place.

These are separate from the top-level config.py on purpose: nothing here affects
the scrape/score/apply pipeline, and a bad value costs you one re-run rather
than a corrupted queue.
"""

import os

# --- Which jobs get a CV built for them ---------------------------------------
#
# The corpus is ~1300 scored jobs, but clustering all of them clusters the German
# job market, not your opportunity set: the mass is Traineeprogramme, Sales
# Engineer roles and C1-German Mittelstand posts you will never send a CV to, and
# they dominate every centroid. The archetypes have to be fitted to the jobs you
# would actually apply to, so the corpus is gated first.
MIN_SCORE = int(os.getenv("CLUSTER_MIN_SCORE", "30"))

# German at C1 is the one gate that is not negotiable on a timescale that matters
# for this job search, so those postings are out of the archetype fit entirely.
EXCLUDE_GERMAN_LEVELS = {"C1-fluent"}

# A senior req is negotiable at the margin; a 5+ year req is not, at zero
# full-time years. Kept loose because the score gate above already does most of
# the filtering.
MAX_YEARS_REQUIRED = int(os.getenv("CLUSTER_MAX_YEARS", "3"))


# --- Extraction ---------------------------------------------------------------
EXTRACTION_WORKERS = int(os.getenv("CLUSTER_WORKERS", "5"))
# Truncation guard. The median JD is ~4k chars; the tail runs to 20k+ of benefits
# boilerplate that carries no requirement signal and costs tokens.
MAX_DESCRIPTION_CHARS = 9000


# --- Feature weights ----------------------------------------------------------
#
# Each block is L2-normalised to unit length on its own before being scaled by
# the weight below, so a block's influence on the distance is set here and NOT by
# how many columns it happens to have. Without that, the 38-column skill block
# would drown the 6-column deliverable block no matter what you wanted.
#
# The ratios encode a judgement, and it is the judgement most worth arguing with:
# skills decide whether you *can* do the job, function and deliverable decide
# what the CV has to *look like*. Two jobs with the same stack but different
# deliverables need different CVs, so context is weighted nearly as high as skills.
BLOCK_WEIGHTS = {
    "must_have_skills": 1.00,
    "nice_to_have_skills": 0.30,   # a signal, but the CV is not built for it
    "function": 0.90,              # primary (+ half-credit secondary)
    "deliverable": 0.55,
    "team_context": 0.50,
    "seniority": 0.40,
    "company_stage": 0.30,
    "domain": 0.25,                # deliberately low: a CV should not split by industry
}


# --- Clustering ---------------------------------------------------------------
K_RANGE = (2, 3, 4, 5, 6, 7)
RANDOM_STATE = 0
KMEANS_N_INIT = 50
# Bootstrap resamples for the stability check. At n~150 this is the number that
# actually decides whether a k is real, so it is worth more than the silhouette.
STABILITY_RESAMPLES = 60
STABILITY_SAMPLE_FRACTION = 0.8

# Retain this share of variance when reducing before k-means. Distances in a
# 60-column mostly-binary space concentrate (everything is roughly equidistant),
# which flattens k-means; projecting first restores usable contrast.
PCA_VARIANCE = 0.90

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
PROFILE_CACHE = os.path.join(CACHE_DIR, "profiles.json")
