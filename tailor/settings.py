"""Knobs for the tailoring loop.

Separate from the top-level config.py: nothing here touches the scrape/score/apply
pipeline, and a bad value costs one regeneration rather than a corrupted queue.
"""

import os

import config

# --- Models -------------------------------------------------------------------
#
# Writer and judge should not be the same model. A model reviewing its own
# output shares its blind spots and approves its own habits, which is precisely
# the failure this loop exists to catch.
#
# The default pairs the Sonnet writer with an Opus judge. A different model in
# the same family is a weaker separation than a different provider would be -
# they share training lineage, so some blind spots survive - but it is a real
# improvement over self-review and needs no second API key. Point
# TAILOR_JUDGE_MODEL at another provider if you ever have one.
WRITER_MODEL = os.getenv("TAILOR_WRITER_MODEL", config.LLM_MODEL)
JUDGE_MODEL = os.getenv("TAILOR_JUDGE_MODEL", "anthropic/claude-opus-5")

# Models that reject anything but temperature=1. Sampling temperature is not
# worth failing a whole run over, so the judge asks for what it wants and this
# quietly gives way where the model refuses.
FIXED_TEMPERATURE_MODELS = ("claude-opus-5",)


def temperature_for(model: str, preferred: float) -> float:
    """The temperature to actually send, given what `model` will accept."""
    if any(name in (model or "") for name in FIXED_TEMPERATURE_MODELS):
        return 1.0
    return preferred

# --- Loop ---------------------------------------------------------------------
#
# Three rounds. Objections that survive that long are usually structural - a
# missing qualification the CV cannot invent - and past round two the two models
# mostly converge on each other's taste rather than on anything an employer would
# notice.
MAX_ROUNDS = int(os.getenv("TAILOR_MAX_ROUNDS", "3"))

# How many repair attempts the writer gets when the deterministic verifier
# rejects its output. Two: a genuine slip is fixed on the first retry, and a
# model that fails twice is failing systematically, so raising the number just
# burns tokens.
MAX_REPAIR_ATTEMPTS = 2

# Questions per interview round. Five is about the limit of what gets answered
# properly in one sitting; beyond that answers get terse and the facts are worse.
MAX_QUESTIONS = int(os.getenv("TAILOR_MAX_QUESTIONS", "5"))

# --- Files --------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The fact base. Personal, and this repository is public, so it is gitignored
# alongside candidate_profile.json.
FACTS_PATH = os.getenv("TAILOR_FACTS_PATH", os.path.join(_ROOT, "profile_facts.json"))
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# --- Voice --------------------------------------------------------------------
#
# Phrases that mark a CV as machine-written. This is a blocklist rather than a
# detector on purpose: asking an LLM "does this read as AI-generated?" is
# unreliable in both directions, and a model told to sound human tends to add
# performative roughness rather than remove the actual tells. A fixed list is
# crude but it is checkable, deterministic, and it never argues back.
BANNED_PHRASES = [
    "leverage", "leveraging", "spearheaded", "passionate about", "results-driven",
    "detail-oriented", "team player", "dynamic environment", "cutting-edge",
    "state-of-the-art", "seamlessly", "robust solutions", "delve", "delved",
    "in today's", "fast-paced", "synergy", "synergies", "utilize", "utilized",
    "showcasing", "underscore", "underscores", "testament to", "pivotal",
    "meticulous", "meticulously", "navigate the", "landscape of", "realm of",
    "wealth of experience", "proven track record", "hit the ground running",
    "i am excited to", "i am thrilled", "excited about the opportunity",
    "perfect fit", "ideal candidate", "furthermore", "moreover", "additionally,",
]
