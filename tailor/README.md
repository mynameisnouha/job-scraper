# tailor — a CV and cover letter per posting, written from facts

Generates a tailored CV and Anschreiben for one job, from a fact base about you,
argued over by a simulated hiring manager until the objections stop being new.

Use it from the Streamlit app: `streamlit run ui_app.py` → **Generate CV**.

---

## The goal

Not "make the CV sound better". The goal is narrower and more useful:

> **Stop losing points for things that are true and simply unwritten.**

A CV is a lossy summary of a person. It drops whatever did not fit on the page
the last time it was edited, and a screener — human or model — can only judge
what it can see. In this repository's own corpus, the highest-scoring unapplied
posting docked points for *"PyTorch — not mentioned on resume"* against a
candidate who had fine-tuned a model with QLoRA. The skill was there. The
sentence was not.

That is a false negative, and false negatives are worth fixing. Manufacturing
qualifications is not, and everything below is arranged so that the second thing
cannot happen while the first does.

A second goal follows from the first: the output has to read like a person wrote
it. Not because generic CVs offend, but because a document assembled from
interchangeable phrases carries no information, and the reader has fifty of them.

---

## How it runs

```
job scorer ---> leads ------> you answer --+   a lead is a question:
every scraped   what the      in your own   |   never citable until
posting         market keeps  words         |   you have answered it
                asking for                  v
     facts  ->  interview  ->  writer  ->  verify  ->  judge  -+
     what is    ask about     compose     mechanical  object   |
     true       the gaps      + cite      checks      as the   |
                                                      employer |
                                ^                              |
                                +--- revise, until nothing ----+
                                |    new comes back            |
                                |                              v
                                +--- facts <-- you <-- the objections no
                                                       rewrite can answer
```

### `harvest.py` — what the scorer already noticed

The job scorer reads every scraped posting against your CV and records, per job,
the gaps where **the substance is probably there and the CV just does not show
it** — `fixable_before_applying`, which the scoring prompt defines as tier-4
evidence. Those observations were computed a few hundred times and read once
each, on the job that produced them.

Across the corpus they are a much better signal than on any one posting. One
posting wanting Airflow is that posting's taste; fourteen is a gap worth an
evening. Harvesting folds them into the fact base as they come in — deduped by
meaning, not by string, and counted by how many postings hit them.

**Nothing harvested is a fact.** Leads land `confirmed=False`, and an unconfirmed
entry is not citable: it is absent from `base.render()` so the writer never sees
it, and absent from `base.ids()` so the verifier rejects any line citing it. The
scorer read a CV and a posting, not your memory — it can say "no Airflow shown",
it cannot say whether you have used Airflow. Only you can. Answering a lead runs
your answer through the same converter as an interview answer, so what enters the
base is your sentence about what you did, in your words, at the tier your answer
supports — and the lead itself is dropped.

A lead you say is not true is dropped for good. The "base only grows" rule is
about facts; a lead is a question, and a question that keeps coming back after
you have answered it is a nag.

Where it runs, and why there are two paths: scoring happens in GitHub Actions,
where `profile_facts.json` does not exist — it is personal and gitignored. So
`harvest_scored_batch` runs at the tail of every scoring batch and quietly finds
nothing to write to in CI, while `harvest_from_supabase` reads the stored
breakdowns back the first time you open the tailoring page in a session. Nothing
is lost either way: the breakdowns are in Supabase, the harvest is idempotent by
job id, and no model is called on either path — it is string handling and a
dictionary.

### Scoring reads the base too

The scoring rubric has always had a rule for tier-4 evidence — *a must-have at
evidence tier 4 (true but not written on the CV) costs HALF weight and belongs
under fixable_before_applying* — and nothing could populate it. So every answer
you gave the tailoring interview scored as tier 5, **not done, at full weight**,
against a posting that asked for it.

`FactBase.render_for_scoring()` fills it: confirmed facts at tier 4, plus any
interview answer, rendered as an `EVIDENCE NOT ON THE RESUME` block that sits
after the resume in the scorer's cached system prompt.

Three properties of that block are load-bearing.

**It is beside the resume, never inside it.** Merging the two would make
`fixable_before_applying` and the gap between `p_first_round_interview.as_is` and
`.after_fixes` all report that the CV already shows these things. It does not,
and those fields are what tell you which line to go and add.

**Leads cannot reach it.** `render_for_scoring` serves `citable()`, so an
unconfirmed lead is withheld — and on this path that matters more than anywhere
else, because a lead came *from* the scorer. Feeding one back would have the
scorer read its own guess as evidence, stop reporting the gap, and raise the
score with nothing having become true.

**Facts already on the CV are left out.** The scorer is reading the CV;
repeating it back spends tokens saying nothing.

Scoring runs in CI, where the fact base does not exist, so it is supplied there
the way the candidate profile already is: a `TAILOR_FACTS_JSON` repository
secret, falling back to the local file. Unlike the profile, a missing fact base
is normal and never fails a run — it only means tier-4 evidence goes unseen.

One consequence worth knowing: adding a fact changes what the *unchanged* CV
scores, so scores from before and after are not strictly comparable, and the
holdout baseline cache is keyed on the evidence block for exactly that reason.
`rescore_existing.yml` re-scores the corpus when you want them level again.

### `facts.py` — the fact base

Atomic, id'd, tiered statements about you, in `profile_facts.json` (gitignored —
it is personal, and this repo is public). Seeded once from your parsed CV plus
`candidate_profile.json`, then grown by the interview.

This is the asset. Everything else in the package is disposable; the fact base
accumulates and makes every later application cheaper. It is never rewritten on
regeneration, only appended to.

Tiers carry the distinction the rest of the package turns on:

| tier | meaning |
|---|---|
| 1 | shipped to production, with a number |
| 2 | built and working, no number |
| 3 | coursework, personal project, or a bare skills-list entry |
| 4 | **true of you, but written nowhere on your CV** |
| 5 | not done |

Tier 4 is the interesting one. Those are points available for the cost of typing.

### `interview.py` — asking about the gaps

The only step that adds information rather than rearranging it, and where most
of the value is.

It diffs a posting's requirements against the fact base and asks you up to five
questions about what is missing — specific enough to answer in a sentence,
ordered by what the posting actually requires, and biased toward questions where
"yes" is plausible. A candidate who fine-tuned with QLoRA probably used PyTorch,
so that question earns its place; asking someone with no infrastructure work
about Kubernetes wastes one of very few questions on a near-certain no.

Your answers become facts. Permanently. Answer once, and every future
application has it.

Answers that are a "no" produce nothing — the conversion step is explicitly
forbidden from softening a no into a partial yes, because one invented fact
contaminates every application written afterwards.

### `writer.py` — composing

Selects, orders, and phrases. **Every claim-bearing line carries the ids of the
facts it rests on.** It may not state anything the fact base does not contain.

Ordering is where the real gain is. The top third of page one decides whether
the rest is read, so the facts answering this posting's must-haves go there, in
the posting's own vocabulary where a fact genuinely supports it — "4-bit
quantisation, 25% faster inference" presented as "LLM inference optimisation" is
translation, not invention.

Lines about availability, notice period or location cite the reserved id
`personal`: those come from `application_answers.json`, are not claims about
competence, and requiring a fact for them made the writer either fabricate a
citation or drop the closing paragraph of the Anschreiben.

### `verify.py` — the honesty guarantee

Deterministic. No LLM. Three checks:

1. every claim-bearing line cites at least one fact id, and every id exists;
2. every number in a line appears in a fact that line cites;
3. no phrase from a banned list.

This is code rather than a prompt on purpose. An optimisation loop with
something to maximise drifts — "working student" softens into "engineer", "30%
memory reduction" becomes "substantial gains", contribution slides into
ownership. Each step is defensible alone, which is exactly why a model asked to
police it waves them through. A regex does not.

Check 3 is also the honest answer to "make it not sound AI-generated". Asking a
model *"does this read as machine-written?"* is unreliable in both directions,
and a writer told to sound human adds performative roughness rather than
removing the tells. A fixed blocklist is crude, but it is checkable and it never
argues back. The rest of the anti-generic work is structural: a document built
from specific cited facts, reusing your own phrasing, has nowhere to put
"results-driven professional with a proven track record".

### `judge.py` — the hiring manager

Reads the application cold, as the manager for that specific posting, and emits
**objections, not a score**.

That is the central design decision. A score invites optimisation, and
optimising a document against a language model until the model approves produces
a document that language models approve of. Since the queue's scorer is *also* a
language model, closing that loop would raise the reported number without
touching what the number is a proxy for. Objections behave differently: each is
specific, can be answered or honestly declined, and **the list runs out** — which
gives the loop an end instead of an asymptote.

Objections marked `structural` — a qualification you do not have — are reported
to you once and never sent back to the writer. Asking a writer to fix a missing
qualification only invites it to blur the wording until the complaint goes away.

**Two ways this step fails quietly, both guarded.** A failed judge call does not
just lose a review: the loop treats it as a reason to stop, so the run ends after
one unreviewed draft that still looks like a finished result. Both were seen in
testing on the same posting.

- The model returns a verdict that *validates* but says nothing — every field at
  its schema default, including `would_interview: "no"`. Treated as a non-answer
  and retried, because a hiring manager reading a real application always
  produces something.
- The model wraps its tool arguments in the markup it would use to describe a
  call, so a list field arrives as a string and validation fails. Unwrapped
  before validation; if the salvaged text is not valid JSON it is handed back
  untouched and still rejected, so nothing is silently accepted.

When the review genuinely cannot be reached, the run is flagged and the page says
in plain terms that the draft was never reviewed, rather than presenting a
first draft with no objections as a clean bill of health.

### `loop.py` — stopping

Stops when a round raises **no objection the previous rounds had not already
raised**. Novelty runs out; a quality score never quite does.

In practice that stop rarely fires, and the cap is what ends the loop. Each
rewrite hands the judge fresh surface to complain about, so rounds kept
producing new objections (5, then 9, then 6 on one run) rather than converging.
The cap is therefore **two rounds** by default, lowered from three after
watching what the third bought: round one does the work, round two answers the
first real objections, and round three mostly trades one set of quibbles for
another at the cost of two more calls — one of them to the expensive judge.
`TAILOR_MAX_ROUNDS` raises it if you disagree.

### The question step between rounds

Rewriting can only rearrange what the fact base already holds. So when the judge
objects that a claim has no scale attached, or that nothing shows who owned the
deployment, and the fact base genuinely does not say — no further round fixes
it. The writer may only use facts it can cite, and the verifier stops it if it
tries; the objection simply survives every remaining round, unanswered.

Those objections are questions for the candidate, not instructions for the
writer. With `probe=True`, a round that raises any is followed by a short
interview built from the judge's own objections, capped at
`TAILOR_MAX_PROBE_QUESTIONS` (3). It is aimed far better than the opening
interview, which reads the posting cold: by this point a hiring manager has read
an actual draft and said what is missing from it.

This is the only step in the loop that adds information rather than moving it
around, which is also why it is the one most likely to move the score. The rest
of the loop is presentation.

Two rules keep it honest, both inherited from the opening interview: a blank
answer is a **no** and produces no facts, and answers become facts in the
candidate's own words, available to every later application rather than to this
posting alone. A question that gets a "no" is not wasted — the objection behind
it is real, and it is reported as something to know before spending an evening
on the application.

The step is skipped on the final round, where the answers would have no rewrite
left to reach, and style flags are never put to the candidate — a line that
reads as machine-written is the writer's to fix.

Answering needs a human, so the loop offers two ways to get one. Pass `ask` and
it is called with the questions mid-run. Without one, the run **pauses**:
`awaiting_answers` is set, the questions and the draft so far come back, and the
caller resumes with `Continuation` once they are answered — the same path as
"add a round", so the draft already on screen is revised rather than rewritten
from scratch. The Streamlit page uses the pause; anything scripted will want
`ask`.

---

## Cost

A run is a handful of large calls, so three things keep the bill down without
touching output quality.

**Prompt caching.** The fact base is the expensive part of every writer call —
several thousand tokens — and it is byte-identical on every round, every repair,
and every job. It lives in the system prompt with caching on, so the first call
pays for it and the rest read it at a fraction of the price. The posting does the
same for the judge, identical across that run's rounds. Anything that varies
stays in the user message, because caching is a prefix match and one changed
byte would invalidate the whole thing.

One caveat worth knowing: a cache *write* costs more than a plain read, so a
single-call-and-stop run is slightly worse off. The saving arrives from the
second call onwards, which any two-round run reaches, and caches survive between
jobs — a session generating several CVs reuses the same fact-base prefix
throughout.

**A cached baseline.** The "before" score is a property of one posting and one
unchanged CV, so it cannot move between regenerations of the same job — but it
is a full scoring call, half the cost of the comparison. It is cached against a
hash of the CV text, so editing your CV invalidates it and nothing else does.

**One fewer round**, as above: two calls saved, one of them the judge's.

Measured on one posting, these together cut the input-token bill by roughly two
thirds, with every call in the run served from cache.

---

## Scoring: the same scorer, used carefully

The finished CV is scored by the queue's own scorer
(`scoring.score_jobs.get_resume_score_from_ai`) rather than a new one. Reusing it
is what makes the number mean anything — it is the same judgement the whole
queue is ranked by, where a purpose-built scorer would be comparable to nothing.

Two rules keep that from becoming circular:

**The writer never sees it.** It runs once, after the loop, and the result goes
to you rather than back into the process.

**Both CVs are scored in the same session.** The score already stored on a job
row is *not* a valid baseline. The scorer runs at non-zero temperature, its
prompt has changed over the life of the corpus, and rows were scored across many
separate runs. On the first posting tested here, the unchanged original CV
scored **68** when the row was written and **42** on a fresh call — so a tailored
CV that had genuinely improved looked like a 16-point regression, purely from
baseline drift. Scored head to head instead, the same pair read 42 → 52.

Only the difference means anything. Neither number is an absolute.

And even the difference is a sanity check, not a result: writer and scorer are
both language models, so some of any gain is agreement rather than
employability. Replies from employers are the only real evidence, which is what
the Calibration page is for.

---

## What this cannot do

Worth knowing before spending an evening on an application.

The judge reports **structural** objections separately for exactly this reason:
they are real, and no rewrite closes them. On the first posting tested, those
were a start date over a year out, three named MLOps tools never used, and
TensorFlow. A CV cannot fix any of them, and a process that appeared to would be
lying to you.

The tailoring moves `must_have_coverage` and `evidence_strength` — surfacing
what you have. It does not move availability, language level, or years of
full-time experience.

---

## Configuration

| variable | default | notes |
|---|---|---|
| `TAILOR_WRITER_MODEL` | `config.LLM_MODEL` (Sonnet) | |
| `TAILOR_JUDGE_MODEL` | `anthropic/claude-opus-5` | deliberately not the writer's model |
| `TAILOR_MAX_ROUNDS` | 3 | |
| `TAILOR_MAX_QUESTIONS` | 5 | opening interview, reading the posting cold |
| `TAILOR_MAX_SCORER_LEADS` | 40 | unconfirmed scorer leads held at once |
| `TAILOR_MAX_PROBE_QUESTIONS` | 3 | mid-loop, off the judge's objections — it interrupts a run in flight, so it has to be worth stopping for |
| `TAILOR_FACTS_PATH` | `profile_facts.json` | |
| `TAILOR_FACTS_JSON` | unset | the fact base as JSON, for CI — read by the scorer, not by this package |

On the judge model: a model reviewing its own output approves its own habits,
which is precisely what this loop exists to catch. The default therefore pairs a
Sonnet writer with an Opus judge.

That is a weaker separation than two different providers would give — same
training lineage, so some blind spots survive — but it is a real improvement over
self-review and needs no second API key. It showed immediately: on the first
application tested, the Sonnet judge passed the line *"Managed experiment
tracking and model versioning using MLOps practices"*, which was stretched from a
bare `MLOps` entry in a skills list. The Opus judge raised it as a major
objection on the same document.

Opus rejects any sampling temperature but 1. `settings.temperature_for` gives way
where a model refuses rather than failing the run, so a judge model can be
swapped in without checking what it accepts first.
