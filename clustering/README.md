# clustering — CV archetypes from the job corpus

Groups the postings worth applying to into a small number of archetypes, so you
maintain a few targeted base CVs instead of one generic one.

```
python -m clustering.run
```

Writes three artefacts into `output/`:

| file | contents |
|---|---|
| `archetypes.md` | the full report — one section per cluster |
| `assignments.csv` | every job with its archetype, confidence margin and URL |
| `clusters.json` | the same summaries, structured, read by the Streamlit page |

Then score your CV against those archetypes:

```
python -m clustering.cv_fit
```

which adds `output/cv_fit.json`. Both sets of results appear under
**CV Archetypes** in the Streamlit app (`streamlit run ui_app.py`).

Flags:

| flag | effect |
|---|---|
| `--force-extract` | ignore the cache and re-run the LLM extraction |
| `--k N` | force a specific number of clusters instead of selecting one |
| `--min-cluster-size N` | reject any k producing a cluster smaller than N (default 12) |

`CLUSTER_MIN_SCORE`, `CLUSTER_MAX_YEARS` and `CLUSTER_WORKERS` come from the
environment; everything else is in `settings.py`.

---

## The pipeline

```
corpus.py  ->  extract.py  ->  features.py  ->  cluster.py  ->  report.py
gate the       one LLM call    weighted        PCA +           lift over
corpus         per posting     block vector    k-means         corpus share
```

### 1. `corpus.py` — gate before clustering

Only jobs that pass the score, language and experience gates are used to fit the
archetypes.

The reason is that a scraped corpus is a sample of the job market, not of your
opportunity set. Postings you would never apply to are usually the majority, and
k-means has no notion of relevance — it minimises variance over whatever it is
given, so a large irrelevant mass will claim centroids of its own and push the
jobs you care about into a single undifferentiated cluster. Gating first makes
the clusters describe the decision you are actually making.

The trade-off is real and worth stating: the tighter the gate, the fewer
postings remain, and cluster structure gets harder to establish as n falls. The
gate should be the loosest one that still excludes jobs you would not apply to.

### 2. `extract.py` — normalise to a closed vocabulary

Each posting gets one cheap LLM call that fills the fixed schema in `schema.py`.

**Why not embed the job descriptions directly.** Text similarity clusters
whatever varies most in the text, and in a job corpus that is rarely the work.
Ads share boilerplate — benefits, equal-opportunity statements, company blurb —
that is long, highly similar within an employer, and unrelated to requirements.
In a corpus written in more than one language, language dominates outright: the
strongest signal available to a bag-of-words or embedding model is which
language a document is in, so clusters separate by language before they separate
by role. Extraction discards surface form and keeps requirements, which is the
only part a CV responds to.

**Why the vocabulary is closed.** Free-text extraction spells the same
requirement several ways across postings. Those spellings become separate
columns, each sparse, and two postings that mean the same thing end up far
apart — the feature space fragments and no clustering can recover from it.
Forcing every field to a fixed enum is what makes distances between jobs mean
anything.

The cost of a closed vocabulary is that the model will occasionally reach for a
value outside it. `schema.py` handles the two cases differently: unknown entries
in the *list* fields are dropped, because losing an entire posting over one
invented skill throws away everything else it contained; unknown values in
*single-choice* context fields fail the posting instead, because those have no
neutral value and a fabricated one would pull a centroid. Only `domain` gets a
fallback, since it has a genuine none-of-these bucket.

Profiles are cached by `job_id`, so re-runs cost nothing and only new postings
reach the model.

### 3. `features.py` — blocked, weighted, rarity-scaled

Fields are grouped into blocks (skills, function, deliverable, team context,
seniority, company stage, domain). Each block is L2-normalised on its own and
then multiplied by a weight from `settings.BLOCK_WEIGHTS`.

This matters because flat one-hot concatenation lets a block's influence be set
by its column count. A skill taxonomy has many columns and a deliverable
taxonomy has few, so concatenating them raw gives skills several times the pull
for no reason anyone chose. Normalising per block first makes influence an
explicit setting.

The weights encode one judgement: skills decide whether you *can* do the job,
but function and deliverable decide what the CV has to *look like*. Two postings
with an identical stack but different deliverables need different CVs, so
context is weighted close to skills rather than well below them.

Skills additionally get IDF weighting. A skill required by almost every posting
in the corpus carries no information about which archetype a posting belongs to;
a rare one carries a great deal.

### 4. `cluster.py` — k-means, then check it

PCA first. In a wide, mostly-binary space distances concentrate — every pair of
points drifts toward the same distance — and k-means is nothing but a distance
argument, so it loses its grip. Projecting onto the components that carry the
variance restores contrast.

**Why k-means and not deep clustering.** DEC, IDEC and the autoencoder-based
family need thousands of samples to fit a reconstruction objective before the
clustering head does anything useful. A gated job corpus is typically hundreds
of postings at most. A deep model on that would fit noise, give clusters that
move with the random seed, and leave no interpretable centroid to write a CV
from. The high-capacity step in this pipeline is the LLM extraction, which is
where semantic understanding belongs; once postings are normalised the remaining
geometry is simple enough that k-means is the right size of tool.

**Choosing k.** Silhouette alone is the wrong criterion and tends to keep rising
with k on soft data, which hands you more archetypes than you could maintain
CVs for. `recommend_k` selects on **bootstrap stability** first — refit on random
subsamples, scored against the full-data fit with adjusted Rand index — and uses
silhouette only to break ties. A minimum cluster size acts as a hard gate, on
the grounds that a CV maintained for a handful of postings costs more than it
returns.

### 5. `report.py` — read the clusters back in words

Clustering happens in an IDF-scaled, block-weighted, PCA-projected space where
no coordinate has a plain-English meaning. Everything reported to a human is
therefore recomputed from the raw profile fields as shares of postings.

Distinctiveness is reported as **lift**: a cluster's share of a feature divided
by the corpus share. A skill present in most of a cluster is not interesting if
it is present in most of the corpus; a skill several times over-represented is
what the CV should lead with.

---

## Reading the output honestly

Expect a **low silhouette**. Roles in a single field lie on a continuum rather
than in separate species, and overlapping requirements are the normal state of a
job market. That is a finding about the market, not a defect in the pipeline.
Two other numbers carry more weight:

- **Stability (ARI)** — would you get the same clusters from slightly different
  data? At a few hundred samples this is what decides whether a partition is
  real. A sharp drop in stability as k increases marks where genuine structure
  ends.
- **Assignment margin** (per job, in the CSV) — how much closer a posting sits
  to its own centroid than to the runner-up. Low-margin postings sit between two
  archetypes; route those by eye rather than by rule.

The question these clusters have to answer is not "are they statistically
crisp" — nothing is validating them against ground truth. It is whether routing
a posting to one of N base CVs beats using one generic CV. A soft partition
clears that bar, because it changes what appears in the top third of page one.

---

## Scoring a CV against the archetypes — `cv_fit.py`

The CV is mapped onto the same closed vocabulary as the postings, by the same
model, under the same taxonomy rules. That last point is not optional: the two
sides are compared directly, so a rule applied to one and not the other shows up
as a fake gap. `schema.TAXONOMY_NOTES` is shared verbatim by both extractors for
exactly this reason — when it was missing, a posting wanting a neural network
was tagged `deep_learning` while a CV that had fine-tuned a transformer was not,
and the artefact surfaced as the single largest reported gap.

Two ideas shape the output.

**Gaps are weighted by demand.** Coverage is the share of an archetype's
*demand* the CV answers, not the share of its skills. Missing something 90% of
postings require is a different problem from missing something 15% mention, and
an unweighted checklist would rate them the same.

**Having a skill is not the same as a reader seeing it.** Skills are tiered by
evidence strength, and the boundary that matters is between tier 3 (on the CV in
some form) and tier 4 (true of you, but absent from the CV). Tier-4 items are
the cheapest points available — recovered by editing rather than by learning —
so they are reported separately and a second coverage figure shows the ceiling
if they were simply written down.

Results split into three buckets, because they call for different responses:
**have it** (nothing to do), **true but not on the CV** (an edit), and
**missing** (a project to build, or a reason to deprioritise the archetype). A
fourth list names skills the archetype essentially never asks for — not a
weakness, but page space that could be reclaimed in that version of the CV.

Two limits to read it against. Coverage percentages are not comparable across
archetypes with different denominators: an archetype demanding seven skills is
easier to cover than one demanding fourteen, so read the gap counts alongside
the percentage. And the tier assignments come from a model reading the CV — the
`cache/cv_profile.json` file lists exactly what it concluded, and is worth
checking before trusting a gap.

---

## Wiring it into the pipeline

The archetypes are not a replacement for the per-job tailoring in
`resume/custom_resume_generator.py` — they are the base it starts from. That
base is currently a single `resume.json` (`config.BASE_RESUME_PATH`). The payoff
is one base CV per archetype plus an archetype field set on the job at scoring
time, so tailoring begins from the right document. A rewrite can reorder and
reword; it cannot fix a research CV that started life as a product CV.

Re-run periodically rather than per scrape — archetypes describe a market, and
markets move slower than a scrape cycle. If two archetypes drift together over
time, drop to fewer CVs: a few maintained documents beat several stale ones.
