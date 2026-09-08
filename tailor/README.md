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
facts  ->  interview  ->  writer  ->  verify  ->  judge  -+
what is    ask about     compose    mechanical  object    |
true       the gaps      + cite     checks      as the    |
                                                employer  |
                          ^                               |
                          +---- revise, until nothing ----+
                                new comes back
```

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

### `loop.py` — stopping

Stops when a round raises **no objection the previous rounds had not already
raised**. Novelty runs out; a quality score never quite does. There is a hard cap
of three rounds regardless, because past round two the two models mostly
converge on each other's taste rather than on anything an employer would notice.

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
| `TAILOR_MAX_QUESTIONS` | 5 | |
| `TAILOR_FACTS_PATH` | `profile_facts.json` | |

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
