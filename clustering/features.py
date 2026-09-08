"""Turning JobProfiles into vectors k-means can measure distances in.

The design decision worth understanding: this is a *blocked* feature space, not
one flat concatenation of one-hot columns.

Each block (skills, function, deliverable, ...) is L2-normalised on its own and
then multiplied by a weight from settings.BLOCK_WEIGHTS. That makes a block's
pull on the clustering a number you set deliberately, rather than an accident of
how many columns it happens to occupy. Flat one-hot encoding gives the 38-column
skill block roughly six times the influence of the 6-column deliverable block for
no reason other than column count - and since "what do they want you to build"
is exactly the context that decides a CV's shape, letting it be outvoted by
stack keywords would reproduce the failure this whole module exists to avoid.

Skills additionally get a rarity weight, because a must-have that 90% of the
corpus shares carries no information about which archetype a job belongs to.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np

from clustering import settings
from clustering.schema import (
    CompanyStage,
    Deliverable,
    Domain,
    JobProfile,
    PrimaryFunction,
    Seniority,
    Skill,
    TeamContext,
)

SKILLS: List[str] = [s.value for s in Skill]
FUNCTIONS: List[str] = [f.value for f in PrimaryFunction]
DELIVERABLES: List[str] = [d.value for d in Deliverable]
TEAMS: List[str] = [t.value for t in TeamContext]
STAGES: List[str] = [c.value for c in CompanyStage]
SENIORITIES: List[str] = [s.value for s in Seniority]
DOMAINS: List[str] = [d.value for d in Domain]


@dataclass
class FeatureSpace:
    """The matrix plus everything needed to read a centroid back in words."""

    matrix: np.ndarray                 # (n_jobs, n_features), block-weighted
    job_ids: List[str]
    feature_names: List[str]
    block_slices: Dict[str, slice]
    skill_idf: np.ndarray              # rarity weight per skill, for reporting


def _l2_normalise_rows(block: np.ndarray) -> np.ndarray:
    """Scale each row to unit length, leaving all-zero rows alone.

    An all-zero row is a real state - a posting that named no skill from the
    vocabulary - and dividing it by its zero norm would produce NaN and take the
    whole run down.
    """
    norms = np.linalg.norm(block, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return block / norms


def _multi_hot(values: Sequence[str], vocabulary: List[str]) -> np.ndarray:
    row = np.zeros(len(vocabulary))
    index = {v: i for i, v in enumerate(vocabulary)}
    for value in values:
        if value in index:
            row[index[value]] = 1.0
    return row


def _one_hot(value: str, vocabulary: List[str]) -> np.ndarray:
    return _multi_hot([value], vocabulary)


def build(profiles: Dict[str, JobProfile], job_ids: List[str]) -> FeatureSpace:
    """Assemble the weighted block matrix in a fixed, reproducible column order."""
    ids = [j for j in job_ids if j in profiles]
    n = len(ids)

    must = np.zeros((n, len(SKILLS)))
    nice = np.zeros((n, len(SKILLS)))
    function = np.zeros((n, len(FUNCTIONS)))
    deliverable = np.zeros((n, len(DELIVERABLES)))
    team = np.zeros((n, len(TEAMS)))
    stage = np.zeros((n, len(STAGES)))
    seniority = np.zeros((n, len(SENIORITIES)))
    domain = np.zeros((n, len(DOMAINS)))

    for i, job_id in enumerate(ids):
        p = profiles[job_id]
        must[i] = _multi_hot([s.value for s in p.must_have_skills], SKILLS)
        nice[i] = _multi_hot([s.value for s in p.nice_to_have_skills], SKILLS)

        function[i] = _one_hot(p.primary_function.value, FUNCTIONS)
        if p.secondary_function is not None:
            # Half credit: a genuinely split role should sit between two
            # archetypes rather than being forced onto one of them.
            function[i] += 0.5 * _one_hot(p.secondary_function.value, FUNCTIONS)

        deliverable[i] = _one_hot(p.deliverable.value, DELIVERABLES)
        team[i] = _one_hot(p.team_context.value, TEAMS)
        stage[i] = _one_hot(p.company_stage.value, STAGES)
        seniority[i] = _one_hot(p.seniority.value, SENIORITIES)
        domain[i] = _one_hot(p.domain.value, DOMAINS)

    # Rarity weighting on skills. Python appears in almost every addressable
    # posting, so its presence says nothing about which cluster a job belongs to;
    # inference_optimization appears rarely and says a great deal. Smoothed IDF,
    # applied to must-haves and nice-to-haves alike so the two stay comparable.
    document_frequency = (must > 0).sum(axis=0)
    skill_idf = np.log((n + 1) / (document_frequency + 1)) + 1.0
    must = must * skill_idf
    nice = nice * skill_idf

    blocks = [
        ("must_have_skills", must, [f"must:{s}" for s in SKILLS]),
        ("nice_to_have_skills", nice, [f"nice:{s}" for s in SKILLS]),
        ("function", function, [f"fn:{f}" for f in FUNCTIONS]),
        ("deliverable", deliverable, [f"deliv:{d}" for d in DELIVERABLES]),
        ("team_context", team, [f"team:{t}" for t in TEAMS]),
        ("seniority", seniority, [f"sen:{s}" for s in SENIORITIES]),
        ("company_stage", stage, [f"stage:{c}" for c in STAGES]),
        ("domain", domain, [f"dom:{d}" for d in DOMAINS]),
    ]

    parts: List[np.ndarray] = []
    names: List[str] = []
    slices: Dict[str, slice] = {}
    cursor = 0
    for name, block, labels in blocks:
        weighted = _l2_normalise_rows(block) * settings.BLOCK_WEIGHTS[name]
        parts.append(weighted)
        names.extend(labels)
        slices[name] = slice(cursor, cursor + block.shape[1])
        cursor += block.shape[1]

    return FeatureSpace(
        matrix=np.hstack(parts),
        job_ids=ids,
        feature_names=names,
        block_slices=slices,
        skill_idf=skill_idf,
    )


def raw_indicator_matrix(profiles: Dict[str, JobProfile], job_ids: List[str]) -> np.ndarray:
    """Unweighted 0/1 must-have matrix, for reporting cluster skill shares.

    Centroids in the weighted space are not readable as percentages - the IDF and
    row normalisation see to that - so anything shown to a human comes from here
    instead.
    """
    ids = [j for j in job_ids if j in profiles]
    out = np.zeros((len(ids), len(SKILLS)))
    for i, job_id in enumerate(ids):
        out[i] = _multi_hot([s.value for s in profiles[job_id].must_have_skills], SKILLS)
    return out


def effective_dimensionality(matrix: np.ndarray) -> float:
    """Participation ratio of the covariance spectrum.

    Reported because it is the honest answer to "how many independent things is
    this clustering actually seeing?" - if 60 columns collapse to an effective
    3, asking for k=6 is asking for structure that is not in the data.
    """
    centred = matrix - matrix.mean(axis=0)
    eigenvalues = np.linalg.svd(centred, compute_uv=False) ** 2
    if eigenvalues.sum() == 0:
        return 0.0
    return float(eigenvalues.sum() ** 2 / (eigenvalues ** 2).sum())


def sparsity_report(profiles: Dict[str, JobProfile]) -> Dict[str, float]:
    """Share of postings naming each skill as a must-have."""
    n = max(len(profiles), 1)
    counts: Dict[str, int] = {s: 0 for s in SKILLS}
    for p in profiles.values():
        for skill in p.must_have_skills:
            counts[skill.value] += 1
    return {k: v / n for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}


def mean_profile_size(profiles: Dict[str, JobProfile]) -> float:
    if not profiles:
        return 0.0
    return sum(len(p.must_have_skills) for p in profiles.values()) / len(profiles)


def entropy(distribution: Sequence[float]) -> float:
    """Shannon entropy in bits, used to flag a block that carries no variation."""
    total = sum(distribution)
    if total <= 0:
        return 0.0
    return -sum((p / total) * math.log2(p / total) for p in distribution if p > 0)
