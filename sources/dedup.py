"""Canonical identity for a job posting, across sources.

The same opening reaches us from several places — LinkedIn and Arbeitsagentur both
carry it, and Arbeitsagentur relists it under new reference numbers — and each copy
arrives with its own job_id, its own score, and its own position in the queue. One
YPOG role landed four times and was applied to twice.

Identity is normalized(company) + normalized(title), confirmed by either a matching
city or a similar description. Both halves matter: company+title alone would merge
two genuinely different openings that share a title at a large employer, while
description similarity alone is unreliable when one source gives clean prose and
another gives HTML converted to markdown with a facts header prepended.

Streamlit-free, DB-free, no LLM. Pure functions over dicts.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Applying direct beats applying through an aggregator, so when the same posting
# exists in several places the employer-nearest URL wins. Lower sorts first.
SOURCE_PRIORITY = {
    "ats": 0,
    "arbeitsagentur": 1,
    "linkedin": 2,
}
UNKNOWN_SOURCE_PRIORITY = 9

# Descriptions of the same job differ a lot between sources — markdown conversion,
# a Rahmendaten header, truncation at 4000 chars — so this is deliberately lenient.
# It exists to veto a bad merge, not to prove a good one.
DESCRIPTION_SIMILARITY_THRESHOLD = 0.30
# Anything short of an exact title in an agreed city has to clear a much higher bar.
# Measured against the live corpus, the loose path at 0.30 merged 'Software Engineer
# Conversational AI' in Berlin with 'AI Engineer / Software Engineer' in Cologne —
# one employer, two genuinely different openings. A false merge hides a job
# permanently, which is worse than leaving a duplicate in the queue.
STRICT_SIMILARITY_THRESHOLD = 0.60

_LEGAL_SUFFIXES = re.compile(
    r"\b(gmbh\s*&\s*co\.?\s*kgaa|gmbh\s*&\s*co\.?\s*kg|gmbh|mbh|ag|se|kgaa|kg|ohg|ug|e\.?\s?v|"
    r"inc|incorporated|llc|ltd|limited|plc|b\.?v|n\.?v|s\.?a|s\.?r\.?l|oy|ab|as|aps|sp\.?\s?z\s?o\.?o)\b\.?",
    re.IGNORECASE,
)

# (m/w/d) and its two dozen cousins, plus the German gender-inclusive suffixes.
_GENDER_MARKERS = re.compile(
    r"\((?:[mwfdxa]\s*[/|,\-]\s*)+[mwfdxa]\)"        # (m/w/d), (f/m/x), (m,w,d)
    r"|\(\s*all\s+genders?\s*\)"
    r"|\(\s*(?:m|w|f|d|x)\s*\)"
    r"|\bm\s*/\s*w\s*/\s*[dx]\b"
    r"|\bw\s*/\s*m\s*/\s*[dx]\b"
    r"|\bf\s*/\s*m\s*/\s*[dx]\b"
    r"|\bd\s*/\s*m\s*/\s*w\b"
    r"|\bmwd\b|\bwmd\b|\bfmd\b",
    re.IGNORECASE,
)
_INCLUSIVE_SUFFIX = re.compile(r"(?<=\w)[*:_·](?:in|innen|r)\b", re.IGNORECASE)
_SENIORITY_PARENS = re.compile(r"\(\s*(junior|senior)\s*\)", re.IGNORECASE)

_STOPWORDS = {
    "und", "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "für", "mit", "bei", "von", "zu", "im", "in", "auf", "als", "ist", "sind", "wir",
    "sie", "du", "uns", "unsere", "unser", "ihre", "ihr", "sich", "auch", "oder",
    "the", "and", "for", "with", "you", "your", "our", "we", "are", "is", "to", "of",
    "in", "on", "at", "as", "an", "a", "be", "will", "that", "this", "it", "from",
}


def normalize_company(value: Optional[str]) -> str:
    """'eWolff GmbH & Co. KG' and 'ewolff' collapse to the same key."""
    text = (value or "").lower()
    text = re.sub(r"[.,]", " ", text)
    text = _LEGAL_SUFFIXES.sub(" ", text)
    text = re.sub(r"[^a-z0-9äöüß ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(value: Optional[str]) -> str:
    """'(Junior) Data Engineer (m/w/d)' and 'Data Engineer' collapse to the same key.

    Gender markers are noise for identity — the same posting carries (m/w/d) on one
    board and nothing on another — and a parenthesised (Junior) is how one source
    writes what another puts in the seniority field.
    """
    text = (value or "").lower()
    text = _GENDER_MARKERS.sub(" ", text)
    text = _SENIORITY_PARENS.sub(" ", text)
    text = _INCLUSIVE_SUFFIX.sub("", text)
    text = re.sub(r"[^a-z0-9äöüß+# ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_location(value: Optional[str]) -> str:
    """City only. Sources disagree on everything after it — region, postcode, country."""
    text = (value or "").split(",")[0].lower()
    text = re.sub(r"\b\d{4,5}\b", " ", text)          # postcodes
    text = re.sub(r"[^a-z0-9äöüß ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _content_words(text: Optional[str]) -> set:
    words = re.findall(r"[a-zäöüß0-9]{4,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def description_similarity(left: Optional[str], right: Optional[str]) -> float:
    """Jaccard overlap of content words, 0-1.

    Word sets rather than sequence matching: the same posting can be reordered,
    re-headed and truncated between sources, and order carries no signal here.
    """
    a, b = _content_words(left), _content_words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# A title that carries one of these and one that does not are different openings,
# however similar the rest of the text. Seniority is the thing the candidate is
# screened on, so merging across it would hide a role she cannot apply for behind
# one she can — or the reverse.
_SENIORITY_WORDS = {
    "junior", "senior", "lead", "principal", "staff", "head", "chief", "director",
    "werkstudent", "praktikum", "praktikant", "intern", "internship", "trainee",
    "ausbildung", "azubi", "absolvent", "graduate", "master", "bachelor", "thesis",
}


def canonical_key(job: Dict[str, Any]) -> Tuple[str, str]:
    """The blocking key: employer only.

    Title is deliberately not part of it. The same posting is titled 'AI/ML
    Engineer' on one board and 'AI/ML Engineer – Generative AI' on another, and an
    exact-title block would file those as separate openings — which is the failure
    this step exists to fix. Employers have few postings each, so blocking on the
    company alone stays cheap.
    """
    return (normalize_company(job.get("company")),)


def _titles_match(left_title: str, right_title: str) -> Tuple[bool, bool]:
    """(compatible, exact). Compatible means one title is the other plus a suffix.

    Returns exactness too, because an exact match may lean on a shared city while
    a subset match has to be carried by the description.
    """
    if not left_title or not right_title:
        return False, False
    if left_title == right_title:
        return True, True

    left_words, right_words = set(left_title.split()), set(right_title.split())
    shorter, longer = sorted((left_words, right_words), key=len)
    if len(shorter) < 2 or not shorter <= longer:
        return False, False
    # 'Data Engineer' and 'Senior Data Engineer' are not the same opening.
    if (longer - shorter) & _SENIORITY_WORDS:
        return False, False
    return True, False


def is_duplicate(left: Dict[str, Any], right: Dict[str, Any],
                 threshold: float = DESCRIPTION_SIMILARITY_THRESHOLD) -> bool:
    """Same employer and role, confirmed by city or by description.

    Three paths, in descending confidence:

    * identical title in the same city — enough on its own. Agency boilerplate makes
      the two descriptions look different (one Hays posting pair scored 0.16) while
      the posting is plainly the same one.
    * identical title, cities unknown or different — the description has to carry it.
      A large employer does post the same title in Berlin and Munich as separate
      openings, so only near-identical text should collapse them.
    * one title is the other plus a suffix — the weakest signal, so it needs both an
      agreed city and near-identical text.

    A false merge is worse than a missed one: the duplicate stays visible and can be
    dismissed by hand, whereas a wrongly merged job disappears from the queue.
    """
    if canonical_key(left) != canonical_key(right) or not canonical_key(left)[0]:
        return False

    compatible, exact = _titles_match(normalize_title(left.get("job_title")),
                                     normalize_title(right.get("job_title")))
    if not compatible:
        return False

    left_city = normalize_location(left.get("location"))
    right_city = normalize_location(right.get("location"))
    same_city = bool(left_city) and left_city == right_city

    if exact and same_city:
        return True

    similarity = description_similarity(left.get("description"), right.get("description"))
    if exact:
        return similarity >= max(threshold, STRICT_SIMILARITY_THRESHOLD)
    return same_city and similarity >= max(threshold, STRICT_SIMILARITY_THRESHOLD)


def group_duplicates(jobs: Iterable[Dict[str, Any]],
                     threshold: float = DESCRIPTION_SIMILARITY_THRESHOLD) -> List[List[Dict[str, Any]]]:
    """Cluster jobs into duplicate groups. Only groups of 2+ are returned.

    Blocked on the canonical key first, so this stays linear in practice rather
    than comparing every posting against every other one.
    """
    blocks: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for job in jobs:
        key = canonical_key(job)
        if not all(key):
            continue
        blocks.setdefault(key, []).append(job)

    groups = []
    for block in blocks.values():
        if len(block) < 2:
            continue
        remaining = list(block)
        while remaining:
            seed = remaining.pop(0)
            cluster = [seed]
            rest = []
            for other in remaining:
                if is_duplicate(seed, other, threshold):
                    cluster.append(other)
                else:
                    rest.append(other)
            remaining = rest
            if len(cluster) > 1:
                groups.append(cluster)
    return groups


def _source_rank(job: Dict[str, Any]) -> int:
    return SOURCE_PRIORITY.get(job.get("provider"), UNKNOWN_SOURCE_PRIORITY)


def has_application_history(job: Dict[str, Any]) -> bool:
    """Has this row been acted on? Application state cannot be reconstructed."""
    return bool(job.get("application_stage")) or job.get("status") == "applied"


def choose_survivor(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which row to keep.

    Application history outranks everything: that row records where the application
    actually went, and merging it away would lose the only copy of that fact. After
    that, the row nearest the employer, then one that already carries a score (so a
    merge does not throw away an LLM call), then the fullest description.
    """
    return sorted(
        group,
        key=lambda job: (
            not has_application_history(job),
            _source_rank(job),
            job.get("resume_score") is None,
            -len(job.get("description") or ""),
            str(job.get("scraped_at") or ""),
        ),
    )[0]


def preferred_apply_url(group: List[Dict[str, Any]]) -> Optional[str]:
    """The employer-nearest URL in the group. Applying direct beats an aggregator."""
    with_urls = [job for job in group if job.get("job_url")]
    if not with_urls:
        return None
    return sorted(with_urls, key=_source_rank)[0].get("job_url")


def merge_group(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Collapse a duplicate group into one row plus the alt_sources to record.

    Returns {"survivor", "duplicates", "alt_sources", "apply_url"}. `apply_url` is
    None when the survivor has already been applied to — its job_url is then the
    record of where the application actually went, and rewriting it would falsify
    that. The better URL still lands in alt_sources.
    """
    survivor = choose_survivor(group)
    duplicates = [job for job in group if job is not survivor]

    alt_sources = []
    seen = {survivor.get("job_url")}
    for job in sorted(duplicates, key=_source_rank):
        url = job.get("job_url")
        if url in seen:
            continue
        seen.add(url)
        alt_sources.append({
            "provider": job.get("provider"),
            "job_id": job.get("job_id"),
            "job_url": url,
            "scraped_at": job.get("scraped_at"),
        })

    best_url = preferred_apply_url(group)
    apply_url = None
    if best_url and best_url != survivor.get("job_url") and not has_application_history(survivor):
        apply_url = best_url

    return {
        "survivor": survivor,
        "duplicates": duplicates,
        "alt_sources": alt_sources,
        "apply_url": apply_url,
    }
