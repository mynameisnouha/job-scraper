"""K-means over the profile feature space, with the checks that decide whether
the answer means anything.

On deep clustering, since it is the obvious alternative: no. DEC, IDEC and the
autoencoder-based family need thousands of samples to fit their reconstruction
objective before the clustering head does anything useful, and the addressable
corpus here is ~150 postings in a ~60 column space. A deep model would fit the
noise, produce clusters that change completely with the random seed, and - worst
for this purpose - give no interpretable centroid to write a CV from. The
expensive, high-capacity step in this pipeline is the LLM extraction, which is
where the semantic understanding belongs. Once the postings are normalised the
remaining geometry is simple enough that k-means is the right size of tool.

What is *not* optional at this sample size is knowing how much to trust the
partition, which is what the stability and margin machinery below is for.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score, silhouette_samples

from clustering import settings


@dataclass
class ClusterResult:
    k: int
    labels: np.ndarray
    silhouette: float
    silhouette_per_sample: np.ndarray
    stability: float                   # mean ARI across bootstrap resamples
    stability_std: float
    inertia: float
    sizes: List[int]
    margins: np.ndarray                # per-job confidence, see assignment_margins
    centroids: np.ndarray
    model: KMeans = field(repr=False)


def reduce_dimensions(matrix: np.ndarray, variance: float = None) -> tuple[np.ndarray, PCA]:
    """Project onto the components carrying `variance` of the total.

    Distance concentration is the reason this is here, not speed. In a wide,
    mostly-binary space every pair of points drifts toward the same distance, and
    k-means - which is nothing but a distance argument - loses its grip. Dropping
    the near-empty directions restores contrast between the clusters that do exist.
    """
    variance = settings.PCA_VARIANCE if variance is None else variance
    max_components = min(matrix.shape) - 1
    pca = PCA(n_components=min(max_components, matrix.shape[1]), random_state=settings.RANDOM_STATE)
    projected = pca.fit_transform(matrix)
    cumulative = np.cumsum(pca.explained_variance_ratio_)
    keep = int(np.searchsorted(cumulative, variance) + 1)
    keep = max(2, min(keep, projected.shape[1]))
    return projected[:, :keep], pca


def assignment_margins(matrix: np.ndarray, model: KMeans) -> np.ndarray:
    """How much closer each job is to its own centroid than to the runner-up.

    Normalised to 0-1: 0 means the job sits exactly between two archetypes, 1
    means it is unambiguously one of them. This is the number that tells you
    which postings a routing rule can decide on its own and which need your eye -
    and with a low silhouette it is far more actionable than the silhouette,
    because it is per-job rather than an average over a partition you already
    know is soft.
    """
    distances = model.transform(matrix)
    nearest = np.partition(distances, 1, axis=1)[:, :2]
    closest, runner_up = nearest[:, 0], nearest[:, 1]
    denominator = np.where(runner_up == 0, 1.0, runner_up)
    return (runner_up - closest) / denominator


def bootstrap_stability(
    matrix: np.ndarray,
    k: int,
    reference_labels: np.ndarray,
    resamples: int = None,
    fraction: float = None,
) -> tuple[float, float]:
    """Refit on random 80% subsamples; score agreement with the full-data fit.

    This is the check that matters at n~150. A silhouette says how tight the
    clusters are; stability says whether you would get the same clusters at all
    from slightly different data. A partition that reshuffles when you drop 20%
    of the jobs is not something to build a CV on, however tight it looks.
    """
    resamples = settings.STABILITY_RESAMPLES if resamples is None else resamples
    fraction = settings.STABILITY_SAMPLE_FRACTION if fraction is None else fraction

    rng = np.random.default_rng(settings.RANDOM_STATE)
    n = matrix.shape[0]
    size = max(k + 1, int(n * fraction))
    scores: List[float] = []

    for _ in range(resamples):
        idx = rng.choice(n, size=size, replace=False)
        subset = matrix[idx]
        if len(np.unique(subset, axis=0)) <= k:
            continue
        fit = KMeans(n_clusters=k, n_init=10, random_state=int(rng.integers(1 << 30))).fit(subset)
        scores.append(adjusted_rand_score(reference_labels[idx], fit.labels_))

    if not scores:
        return 0.0, 0.0
    return float(np.mean(scores)), float(np.std(scores))


def fit(matrix: np.ndarray, k: int) -> ClusterResult:
    model = KMeans(
        n_clusters=k, n_init=settings.KMEANS_N_INIT, random_state=settings.RANDOM_STATE
    ).fit(matrix)
    labels = model.labels_
    sil = float(silhouette_score(matrix, labels)) if k > 1 else 0.0
    per_sample = silhouette_samples(matrix, labels) if k > 1 else np.zeros(len(labels))
    stability, stability_std = bootstrap_stability(matrix, k, labels)
    return ClusterResult(
        k=k,
        labels=labels,
        silhouette=sil,
        silhouette_per_sample=per_sample,
        stability=stability,
        stability_std=stability_std,
        inertia=float(model.inertia_),
        sizes=[int((labels == c).sum()) for c in range(k)],
        margins=assignment_margins(matrix, model),
        centroids=model.cluster_centers_,
        model=model,
    )


def sweep(matrix: np.ndarray, k_range=None) -> Dict[int, ClusterResult]:
    k_range = settings.K_RANGE if k_range is None else k_range
    return {k: fit(matrix, k) for k in k_range if k < matrix.shape[0]}


def recommend_k(results: Dict[int, ClusterResult], min_cluster_size: int = 12) -> Optional[int]:
    """Pick k on stability first, silhouette second, usability as a hard gate.

    Silhouette alone is the wrong criterion here and reliably picks too many
    clusters: on soft data it tends to rise monotonically with k, so following it
    hands you seven archetypes you would have to maintain seven CVs for. A
    cluster smaller than min_cluster_size is rejected outright - not because it
    is statistically wrong, but because a CV maintained for eight postings costs
    more than it returns.
    """
    viable = {
        k: r for k, r in results.items()
        if k >= 2 and min(r.sizes) >= min_cluster_size
    }
    if not viable:
        return None
    return max(viable, key=lambda k: (viable[k].stability, viable[k].silhouette))
