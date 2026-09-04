import time
import json
import logging
from typing import List, Optional, Dict, Any
import requests
import io
import pdfplumber
import os

import config
import supabase_utils
from llm_client import primary_client, screen_client
from models import ScoreBreakdown, ScreenResult, PitchOutput

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---

def format_resume_to_text(resume_data: Dict[str, Any]) -> str:
    """
    Formats the structured resume data dictionary into a plain text string.
    """
    if not resume_data:
        return "Resume data is not available."

    lines = []

    # Basic Info
    lines.append(f"Name: {resume_data.get('name', 'N/A')}")
    lines.append(f"Email: {resume_data.get('email', 'N/A')}")
    if resume_data.get('phone'): lines.append(f"Phone: {resume_data['phone']}")
    if resume_data.get('location'): lines.append(f"Location: {resume_data['location']}")
    if resume_data.get('links'):
        links_str = ", ".join(f"{k}: {v}" for k, v in resume_data['links'].items() if v)
        if links_str: lines.append(f"Links: {links_str}")
    lines.append("\n---\n")

    # Summary
    if resume_data.get('summary'):
        lines.append("Summary:")
        lines.append(resume_data['summary'])
        lines.append("\n---\n")

    # Skills
    if resume_data.get('skills'):
        lines.append("Skills:")
        lines.append(", ".join(resume_data['skills']))
        lines.append("\n---\n")

    # Experience
    if resume_data.get('experience'):
        lines.append("Experience:")
        for exp in resume_data['experience']:
            lines.append(f"\n* {exp.get('job_title', 'N/A')} at {exp.get('company', 'N/A')}")
            if exp.get('location'): lines.append(f"  Location: {exp['location']}")
            date_range = f"{exp.get('start_date', '?')} - {exp.get('end_date', 'Present')}"
            lines.append(f"  Dates: {date_range}")
            if exp.get('description'):
                lines.append("  Description:")
                # Indent description lines
                desc_lines = exp['description'].split('\n')
                lines.extend([f"    - {line.strip()}" for line in desc_lines if line.strip()])
        lines.append("\n---\n")

    # Education
    if resume_data.get('education'):
        lines.append("Education:")
        for edu in resume_data['education']:
            degree_info = f"{edu.get('degree', 'N/A')}"
            if edu.get('field_of_study'): degree_info += f", {edu['field_of_study']}"
            lines.append(f"\n* {degree_info} from {edu.get('institution', 'N/A')}")
            year_range = f"{edu.get('start_year', '?')} - {edu.get('end_year', 'Present')}"
            lines.append(f"  Years: {year_range}")
        lines.append("\n---\n")

    # Projects
    if resume_data.get('projects'):
        lines.append("Projects:")
        for proj in resume_data['projects']:
            lines.append(f"\n* {proj.get('name', 'N/A')}")
            if proj.get('description'): lines.append(f"  Description: {proj['description']}")
            if proj.get('technologies'): lines.append(f"  Technologies: {', '.join(proj['technologies'])}")
        lines.append("\n---\n")

    # Certifications
    if resume_data.get('certifications'):
        lines.append("Certifications:")
        for cert in resume_data['certifications']:
            cert_info = f"{cert.get('name', 'N/A')}"
            if cert.get('issuer'): cert_info += f" ({cert['issuer']})"
            if cert.get('year'): cert_info += f" - {cert['year']}"
            lines.append(f"* {cert_info}")
        lines.append("\n---\n")

    # Languages
    if resume_data.get('languages'):
        lines.append("Languages:")
        lines.append(", ".join(resume_data['languages']))
        lines.append("\n---\n")

    return "\n".join(lines)


CANDIDATE_PROFILE = """- Education: AI Engineering Master's from University of Passau (in progress/completed)
- Current role: Working Student Data Scientist at Daimler Buses (Mercedes-Benz Group)
- Languages: English (C2), German (A2, improving toward B1)
- Career stage: Early-career, seeking first full-time Data Science / AI role"""

# --- v2 candidate profile: the full structured input the v2 rating spec expects. ---
# effective FTE-years = full_time + 0.5*(working_student + internship + thesis), per the
# ledger note that recruiters discount part-time/intern time by roughly half.
EXPERIENCE_LEDGER = {
    "full_time_professional_months": 0,
    "working_student_months": 24,
    "internship_months": 14,
    "thesis_or_research_months": 6,
}
EFFECTIVE_FTE_YEARS = round((
    EXPERIENCE_LEDGER["full_time_professional_months"]
    + 0.5 * EXPERIENCE_LEDGER["working_student_months"]
    + 0.5 * EXPERIENCE_LEDGER["internship_months"]
    + 0.5 * EXPERIENCE_LEDGER["thesis_or_research_months"]
) / 12, 1)

CANDIDATE_PROFILE_V2 = f"""
### Availability
- Earliest full-time start: 2027-03-01 (Master's thesis contract, 30h/week, Sept 2026 - Feb 2027)
- Open to part-time bridge work now
- Hard deadline to have a signed contract: 2027-02-28

### Work authorization
- Current status: German student residence permit (Aufenthaltserlaubnis zu Studienzwecken)
- Needs sponsorship for full-time work: NO — the permit converts to a work-based residence
  permit upon signing a qualifying employment contract in Germany. This is a standard permit
  conversion, not an employer-run sponsorship process. Only fails this gate if the JD explicitly
  requires an EU passport / existing unrestricted EU work permission / security clearance that
  a converted permit cannot satisfy.
- EU Blue Card: WANTS one, not just eligible. Degree requirement is satisfied (Master's from a
  German university). The only variable is whether the offered salary clears the Blue Card gross
  annual salary threshold (roughly €45k for standard occupations / roughly €41k for shortage
  occupations incl. IT/STEM — figures move yearly, treat as approximate, note when a salary_band
  is close to the line). A role whose stated salary clears the threshold is a genuine plus, not
  just nice-to-have: it gets her onto the Blue Card instead of a standard permit, which unlocks
  faster permanent residency and EU-wide mobility. Reflect this under differentiation/environment_fit
  or as a fixable/structural note when salary is stated and below threshold — do NOT treat it as
  a hard gate; she does not need the Blue Card to accept the offer, only wants it.
- Authorized without a new application only in: Germany

### Experience ledger (do NOT sum into one "years of experience" figure)
- Full-time professional: {EXPERIENCE_LEDGER['full_time_professional_months']} months
- Working student: {EXPERIENCE_LEDGER['working_student_months']} months
- Internship: {EXPERIENCE_LEDGER['internship_months']} months
- Thesis / research: {EXPERIENCE_LEDGER['thesis_or_research_months']} months
- Effective FTE-years for seniority comparison (part-time/intern time discounted ~50%): {EFFECTIVE_FTE_YEARS}

### Languages
- English: C2
- French: B2
- German: A2, improving toward B1

### Location
- Base: Passau, Germany
- Willing to relocate to: Munich, Berlin, Hamburg, Stuttgart
- Remote OK

### Evidence index (what can actually be pointed at, not just plausibly claimed)
tier_1 = shipped in production with a metric, on the CV | tier_2 = built and working, on the CV, no metric |
tier_3 = personal project/coursework, linkable | tier_4 = true but not currently written anywhere a reader can see | tier_5 = not done
- LLM fine-tuning: tier_1
- Inference optimization: tier_1
- FastAPI backends: tier_2
- AWS ECS deployment: tier_2
- Structured LLM output: tier_2
- LLM evaluation frameworks: tier_5
- Automated testing: tier_4 (true, not on CV)
- Daily use of AI coding tools (Claude Code): tier_4 (true, not on CV)
- Frontend: tier_5
- Healthcare domain: tier_5
"""


def screen_job_with_ai(job_details: Dict[str, Any]) -> Optional[ScreenResult]:
    """
    Cheap fast-model screen: does this job clear the hard gates at all?
    Returns a ScreenResult, or None if the call failed (job stays unscored and is retried next run).
    """
    description = (job_details.get('description') or '')[:3000]
    if not description.strip():
        return None

    prompt = f"""You are a fast job-screening filter for an early-career AI/Data Science candidate in Germany.

## CANDIDATE
{CANDIDATE_PROFILE}

## RULES — set passes=false if ANY of these apply:
1. The JD explicitly requires fluent/native German (fließend/verhandlungssicher/muttersprachlich). A JD merely written in German with no stated level does NOT fail.
2. The JD explicitly requires 4+ years of professional experience.
3. The role is NOT in data science / machine learning / AI / data or software engineering (e.g. sales, finance, accounting, mechanical engineering, marketing, nursing).
4. The role is explicitly Senior / Staff / Principal / Lead / Head of.

Otherwise passes=true. rough_score is a quick 0-100 fit estimate; reason is one short sentence.

## JOB
Title: {job_details.get('job_title', 'N/A')}
Company: {job_details.get('company', 'N/A')}
Level: {job_details.get('level', 'N/A')}

{description}
"""
    try:
        result_text = screen_client.generate_content(
            prompt=prompt,
            response_format=ScreenResult,
            temperature=0.0,
        )
        return ScreenResult.model_validate_json(result_text)
    except Exception as e:
        logging.error(f"Screening failed for job_id {job_details.get('job_id')}: {e}")
        return None


def run_screening_phase() -> list:
    """
    Screens up to JOBS_TO_SCREEN_PER_RUN unscored jobs with the cheap model.
    Screen failures are written to the DB immediately with a capped score and a 'skip'
    breakdown, so they never consume a full scoring call. Returns the passers
    (at most JOBS_TO_SCORE_PER_RUN) for full scoring.
    """
    screen_limit = getattr(config, 'JOBS_TO_SCREEN_PER_RUN', config.JOBS_TO_SCORE_PER_RUN)
    jobs = supabase_utils.get_jobs_to_score(screen_limit)
    if not jobs:
        return []

    logging.info(f"--- Screening Phase: {len(jobs)} jobs with {getattr(config, 'LLM_SCREEN_MODEL', config.LLM_MODEL)} ---")
    passers = []
    screened_out = 0
    consecutive_errors = 0

    for job in jobs:
        job_id = job.get('job_id')
        if not job_id:
            continue

        result = screen_job_with_ai(job)
        if result is None:
            consecutive_errors += 1
            if consecutive_errors >= 3:
                logging.error("3 consecutive screening failures — likely an LLM auth/config problem with the screen model. "
                              "Falling back to full scoring without screening.")
                return supabase_utils.get_jobs_to_score(config.JOBS_TO_SCORE_PER_RUN)
            continue
        consecutive_errors = 0

        if result.passes:
            passers.append(job)
            logging.info(f"  SCREEN PASS  ({result.rough_score:3d}) {job.get('job_title')} — {result.reason}")
        else:
            screened_out += 1
            capped = min(result.rough_score, 49)
            breakdown_lite = {
                "overall_score": capped,
                "recommendation": "skip",
                "reasoning": f"Screened out: {result.reason}",
                "key_gaps": [result.reason],
                "screen_only": True,
            }
            logging.info(f"  SCREEN FAIL  ({capped:3d}) {job.get('job_title')} — {result.reason}")
            supabase_utils.update_job_score(job_id, capped, resume_score_stage="initial",
                                            score_breakdown=breakdown_lite)

    logging.info(f"--- Screening done: {len(passers)} passed, {screened_out} screened out, "
                 f"{len(jobs) - len(passers) - screened_out} errored (will retry next run) ---")
    return passers[:config.JOBS_TO_SCORE_PER_RUN]


def generate_why_me_pitch(resume_text: str, job_details: Dict[str, Any], breakdown: ScoreBreakdown) -> Optional[str]:
    """
    For strong matches: 3-4 first-person sentences mapping the candidate's strongest
    evidence to the job's top requirements. Used as an Easy-Apply message / email intro
    and as the skeleton of a cover letter (Anschreiben).
    """
    prompt = f"""Write a first-person "why me" pitch (3-4 sentences, no greeting, no sign-off) for this application.

Rules:
- Map the candidate's STRONGEST concrete evidence (Daimler Buses working student role, specific projects, specific skills) to the job's top 2-3 requirements.
- Only use facts present in the resume below. No fabrication, no generic filler ("I am a motivated team player").
- Confident but factual tone. Write in the language of the job description ({breakdown.jd_language}).
- These skills were identified as the candidate's best matches for this job: {', '.join(breakdown.key_matching_skills[:5]) or 'see resume'}.

--- RESUME ---
{resume_text}
--- END RESUME ---

--- JOB ---
Title: {job_details.get('job_title', 'N/A')}
Company: {job_details.get('company', 'N/A')}

{(job_details.get('description') or '')[:4000]}
--- END JOB ---
"""
    try:
        result_text = primary_client.generate_content(
            prompt=prompt,
            response_format=PitchOutput,
            temperature=0.4,
        )
        pitch = PitchOutput.model_validate_json(result_text).pitch.strip()
        logging.info(f"  Pitch for {job_details.get('job_id')}: {pitch[:120]}...")
        return pitch or None
    except Exception as e:
        logging.error(f"Pitch generation failed for job_id {job_details.get('job_id')}: {e}")
        return None


def get_resume_score_from_ai(resume_text: str, job_details: Dict[str, Any]) -> Optional[ScoreBreakdown]:
    """
    Scores a job against the resume using structured LLM output.
    Returns the full ScoreBreakdown (use .overall_score for the number) or None if scoring fails.
    """
    if not resume_text or not job_details or not job_details.get('description'):
        logging.warning(f"Missing resume text or job description for job_id {job_details.get('job_id')}. Skipping scoring.")
        return None

    job_company = job_details.get('company', 'N/A')
    job_title = job_details.get('job_title', 'N/A')
    job_description = job_details.get('description', 'N/A')
    job_level = job_details.get('level', 'N/A')
    job_id = job_details.get('job_id', 'N/A')

    logging.info(f"Scoring job_id: {job_id} | {job_title} @ {job_company} | Level: {job_level}")

    prompt = f"""
You are a job-fit rating engine. Score inflation is the primary failure mode: this score decides
where a candidate with LIMITED time spends application effort. A score that is too high costs
her hours on an application that was never viable. Optimise for calibration, not encouragement.
Never round up to make a job look more viable than it is.

## CANDIDATE PROFILE
{CANDIDATE_PROFILE_V2}

## HARD GATES — evaluate first, they CAP the score regardless of skills match
- work_authorization: fails ONLY if the JD explicitly requires an EU passport / existing
  unrestricted EU work permission / security clearance that a converted student residence permit
  cannot satisfy. Standard "must be eligible to work in the EU" language does NOT fail this —
  she will be eligible once she signs. -> cap 25 if failed.
- availability: her earliest full-time start is 2027-03-01. Judge the employer's likely urgency
  from company size/tone: startup <~50 people or "ASAP"/"immediate start"/urgency language ->
  cap 55. Mid-size, no stated urgency -> cap 70. Large company with structured intake, graduate
  programme, or a stated future start date -> no cap. A stated start date compatible with
  2027-03 (or a part-time bridge role) -> no cap.
- location: on-site/hybrid required somewhere outside Passau/Munich/Berlin/Hamburg/Stuttgart and
  not remote -> cap 30.
- working_language: if the day-to-day working language, OR the language of the product's users
  and data, requires German above A2/beginning-B1 level for daily work -> cap 55. A JD merely
  written in German with English as the stated internal working language does NOT trigger this.
  In practice German employers are frequently more flexible on this than the JD wording implies,
  especially when a candidate's English is native-level fluent (C2) — do not treat a JD written in
  German, or a "German preferred/von Vorteil" line, as proof the role is closed to an English-only
  candidate.
- seniority_floor: if the JD states a minimum years-of-FTE-experience the effective FTE-years
  ({EFFECTIVE_FTE_YEARS}) cannot meet -> cap 40.
- disqualifier_match: for each clear match against an explicit "this role is not for you if" /
  "you will struggle here if" section in the JD, subtract 8 points.

For each gate report: gate name, result (pass/fail/unknown), the cap it implies, a one-line
detail, whether it's negotiable, and how (e.g. "offer part-time bridge from now until March").

## REQUIREMENT EXTRACTION
Parse the JD into a weighted list of requirements. A must_have is worth 4x a nice_to_have.
A requirement repeated across sections (e.g. Tasks AND Must-have) gets a 1.5x emphasis
multiplier — employers repeat what they actually screen on. A missing must_have where the
candidate's evidence tier is 5 (not done) costs the FULL weight — there is no partial credit for
"learnable," everything is learnable, that is not the question. A must_have at evidence tier 4
(true but not written on the CV) costs HALF weight and belongs under fixable_before_applying,
not under structural gaps. Cross-check every claimed match against the evidence index above —
if you can't point to a specific tier for it, treat it as tier 4 or 5.

## DIMENSION SCORES (0-100 each)
- must_have_coverage (35%): weighted must-haves met, by evidence tier
- evidence_strength (15%): are matches tier 1-2, or inferred/assumed? Penalise inference
- nice_to_have_coverage (10%): weighted nice-to-haves met
- seniority_fit (15%): experience ledger vs the role's real level, part-time discounted
- environment_fit (10%): company stage/pace/autonomy vs candidate's actual track record
- domain_fit (5%): industry/regulatory familiarity (note: healthcare domain is tier_5 — not done)
- differentiation (10%): of the estimated applicant pool, what fraction has her strongest asset
  (LLM fine-tuning + inference optimization are tier_1, rare in most applicant pools)?

## COMPETITIVE CONTEXT
Estimate applicant volume (remote EU English-language roles pull the highest volume — 5-20x
on-site equivalents), the modal competing candidate's profile, her percentile in that pool, and
a calibrated p_first_round_interview for as_is (CV today) and after_fixes (every
fixable_before_applying item done). If p_first_round_interview.as_is is below 0.10, the
recommendation cannot be apply_now or apply_after_fixes regardless of the raw score.

## GAP BUCKETS — every gap goes in exactly one
- fixable_before_applying: substance exists, CV doesn't show it (tier 4 evidence). One evening
  of editing. State the exact CV line to add.
- fixable_in_two_weeks: a weekend project or write-up closes it.
- structural_gaps: cannot be fixed before this application closes (e.g. availability date,
  German level, healthcare domain, work authorization).

## CALIBRATION ANCHORS (do not compress everything into 60-85)
- 90-100: hireable without interview, exceeds must-haves with tier-1 evidence, no gate capped
- 75-89: clearly interviewable, all must-haves met, only nice-to-haves missing
- 60-74: plausible interview, one must-have missing/weak, no hard gate capped
- 45-59: stretch, two-plus must-haves missing OR one hard gate capped
- 25-44: a hard gate failed; only worth it if the gate is negotiable
- 0-24: do not apply

## OUTPUT FIELDS (also fill the standard skills/experience/education fields below for compatibility)
- german_required: 'C1-fluent' ONLY if fluent/native/verhandlungssicher German is explicitly
  demanded. 'B2' if intermediate named. 'nice-to-have' if listed as a plus. 'none' if not
  mentioned or English is the stated working language. 'unclear' otherwise.
- years_experience_required: minimum years explicitly required (0 if not stated / entry-level).
- jd_language: 'en', 'de', or 'mixed'.
- visa_sponsorship_mentioned / sponsorship_signal: explicit / implied / absent / explicitly_excluded.
- company_stage, working_language_of_product (distinct from jd_language — what language are the
  USERS and DATA in?), remote_scope, regulatory_context, salary_band, posting_age_days,
  is_agency_or_staffing_firm. If a salary is stated, note in salary_band whether it looks like it
  clears the EU Blue Card threshold (see candidate profile) — if it clearly does, add it to
  differentiators as a Blue Card-qualifying offer; if it clearly doesn't and salary is the only
  thing named, do NOT treat as a gap, just note it factually.
- application_effort_hours + application_effort_estimate (a 3-essay-question form is a different
  bet than a CV upload).
- expected_value = p_first_round_interview.after_fixes / application_effort_hours.
- calibration_check: must_haves_total, must_haves_met_tier_1_or_2, hard_gates_failed,
  cap_applied (null or the number/reason), score_before_cap, final_score,
  would_a_skeptical_recruiter_agree ("yes"/"no" + why), confidence (high/medium/low),
  confidence_reason (e.g. "remote policy ambiguous; sponsorship not stated"). When a field is
  genuinely unknown, set confidence low and name the one question that would resolve it —
  do not resolve it optimistically.
- one_line_verdict: the honest bottom line in one sentence.
- key_matching_skills / key_gaps: kept for the dashboard — key_gaps should mirror the most
  important structural_gaps + must-have misses.
- recommendation: 'apply_now' (score >=75, no gate capped), 'apply_after_fixes' (score >=60 after
  the fixable items are notionally applied), 'apply_if_gate_negotiable' (a gate capped the score
  but it's negotiable), or 'skip'.

--- RESUME ---
{resume_text}
--- END RESUME ---

--- JOB DESCRIPTION ---
Job Title: {job_title}
Company: {job_company}
Level: {job_level}

{job_description}
--- END JOB DESCRIPTION ---

Think step by step: gates first, then requirement extraction, then dimension scores, then
competitive context, then the final calibrated score. Never describe a missing must-have as
"learnable" as a way of discounting it — state the gap, then separately state whether it's
fixable before the deadline.
"""

    try:
        logging.info(f"Requesting structured score for job_id: {job_id}")
        score_text = primary_client.generate_content(
            prompt=prompt,
            response_format=ScoreBreakdown,
            temperature=0.3,
        )

        breakdown = ScoreBreakdown.model_validate_json(score_text)

        # --- Hard gates: requirements the candidate cannot clear today. ---
        # Enforced in code (not left entirely to the LLM's judgement) so a great skills
        # match can't float an unwinnable job into the apply queue. The LLM already applies
        # its own caps inline; this is a deterministic backstop using facts we trust more
        # than LLM arithmetic (the experience ledger, the German gate wording).
        score_before_cap = breakdown.overall_score
        caps = []

        if breakdown.german_required == "C1-fluent":
            caps.append((55, "JD requires fluent/native German (candidate is A2-B1, but C2 English "
                              "and employer flexibility on this in practice keep it from being a hard block)"))
        if breakdown.years_experience_required and breakdown.years_experience_required > EFFECTIVE_FTE_YEARS:
            caps.append((40, f"JD requires {breakdown.years_experience_required}+ years; "
                              f"effective FTE-years is {EFFECTIVE_FTE_YEARS}"))
        if breakdown.disqualifier_matches:
            penalty = 8 * len(breakdown.disqualifier_matches)
            breakdown.overall_score = max(0, breakdown.overall_score - penalty)
            logging.info(f"  DISQUALIFIER MATCHES for {job_id}: -{penalty} for {breakdown.disqualifier_matches}")

        if caps:
            hardest_cap, cap_reason = min(caps, key=lambda c: c[0])
            gate_reasons = [reason for _, reason in caps]
            logging.info(f"  HARD GATE for {job_id}: {'; '.join(gate_reasons)} — capping at {hardest_cap}.")
            breakdown.overall_score = min(breakdown.overall_score, hardest_cap)
            if breakdown.recommendation not in ("apply_if_gate_negotiable",):
                breakdown.recommendation = "skip"
            breakdown.reasoning = f"HARD GATE: {'; '.join(gate_reasons)}. {breakdown.reasoning}"

        p_as_is = ((breakdown.competitive_context or {}).get("p_first_round_interview") or {}).get("as_is")
        if p_as_is is not None and p_as_is < 0.10 and breakdown.recommendation in ("apply_now", "apply_after_fixes"):
            logging.info(f"  P(interview) as_is={p_as_is} < 0.10 for {job_id} — downgrading recommendation.")
            breakdown.recommendation = "apply_if_gate_negotiable" if caps else "skip"

        breakdown.calibration_check.setdefault("score_before_cap", score_before_cap)
        breakdown.calibration_check["final_score"] = breakdown.overall_score
        if caps:
            breakdown.calibration_check["cap_applied"] = min(c[0] for c in caps)

        logging.info(f"=== SCORE BREAKDOWN for {job_id} ===")
        logging.info(f"  Overall:        {breakdown.overall_score}/100")
        logging.info(f"  Skills Match:   {breakdown.skills_match_score}/100")
        logging.info(f"  Experience:     {breakdown.experience_score}/100")
        logging.info(f"  Education:      {breakdown.education_score}/100")
        logging.info(f"  Language:       {breakdown.language_fit}")
        logging.info(f"  German req:     {breakdown.german_required} | Years req: {breakdown.years_experience_required} | JD lang: {breakdown.jd_language} | Visa: {breakdown.visa_sponsorship_mentioned}")
        logging.info(f"  Matching:       {breakdown.key_matching_skills}")
        logging.info(f"  Gaps:           {breakdown.key_gaps}")
        logging.info(f"  Recommendation: {breakdown.recommendation}")
        logging.info(f"  Reasoning:      {breakdown.reasoning}")
        if breakdown.hard_gates:
            failed = [g for g in breakdown.hard_gates if g.get("result") == "fail"]
            if failed:
                logging.info(f"  Gates failed:   {[g.get('gate') for g in failed]}")
        if breakdown.dimension_scores:
            logging.info(f"  Dimensions:     {breakdown.dimension_scores}")
        if breakdown.competitive_context:
            p = breakdown.competitive_context.get("p_first_round_interview") or {}
            logging.info(f"  P(interview):   as_is={p.get('as_is')} after_fixes={p.get('after_fixes')}")
        if breakdown.one_line_verdict:
            logging.info(f"  Verdict:        {breakdown.one_line_verdict}")

        # expected_value ranks the apply queue: interview odds per hour of effort spent.
        p_after_fixes = ((breakdown.competitive_context or {}).get("p_first_round_interview") or {}).get("after_fixes")
        if p_after_fixes is not None and breakdown.application_effort_hours:
            breakdown.expected_value = round(p_after_fixes / breakdown.application_effort_hours, 3)

        return breakdown

    except Exception as e:
        logging.error(f"Error scoring job_id {job_id}: {e}")
        return None


def extract_text_from_pdf_url(pdf_url: str) -> Optional[str]:
    """
    Downloads a PDF from a URL and extracts text from it.
    """
    if not pdf_url:
        logging.warning("No PDF URL provided for text extraction.")
        return None
    try:
        logging.info(f"Downloading resume from URL: {pdf_url}")
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()  # Raise an exception for bad status codes

        # Resume exports can be plain text now (see custom_resume_generator.format_resume_compact)
        # instead of PDF — handle that directly rather than feeding text into pdfplumber.
        if pdf_url.lower().endswith(".txt"):
            text = response.text.strip()
            if not text:
                logging.warning(f"Downloaded text resume at {pdf_url} was empty.")
                return None
            logging.info(f"Successfully extracted text resume from URL: {pdf_url[:70]}...")
            return text

        logging.info(f"Successfully downloaded PDF. Extracting text...")
        text = ""
        with io.BytesIO(response.content) as pdf_file:
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        
        if not text.strip():
            logging.warning(f"Extracted no text from PDF at {pdf_url}. The PDF might be image-based or empty.")
            return None
            
        logging.info(f"Successfully extracted text from PDF URL: {pdf_url[:70]}...")
        return text.strip()

    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading PDF from {pdf_url}: {e}")
        return None
    except pdfplumber.exceptions.PDFSyntaxError: # Catch specific pdfplumber error
        logging.error(f"Error: Could not open PDF from {pdf_url}. It might be corrupted or not a PDF.")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred while extracting text from PDF URL {pdf_url}: {e}")
        return None

def rescore_jobs_with_custom_resume():
    """Fetches jobs with custom resumes and re-scores them."""
    logging.info("--- Starting Job Re-scoring with Custom Resumes ---")
    rescore_start_time = time.time()

    jobs_to_rescore = supabase_utils.get_jobs_to_rescore(config.JOBS_TO_SCORE_PER_RUN)
    if not jobs_to_rescore:
        logging.info("No jobs require re-scoring with custom resumes at this time.")
        logging.info("--- Job Re-scoring Finished (No Jobs) ---")
        return

    logging.info(f"Processing {len(jobs_to_rescore)} jobs for re-scoring...")
    successful_rescores = 0
    failed_rescores = 0

    for i, job in enumerate(jobs_to_rescore):
        job_id = job.get('job_id')
        resume_link = job.get('resume_link')
        customized_resume_id = job.get('customized_resume_id')

        if not job_id:
            logging.warning(f"Skipping re-scoring for job due to missing job_id: {job}")
            failed_rescores += 1
            continue

        logging.info(f"--- Re-scoring Job {i+1}/{len(jobs_to_rescore)} (ID: {job_id}) ---")

        custom_resume_text = None

        # Try to get resume data from database first
        if customized_resume_id:
            logging.info(f"Targeting customized_resume_id: {customized_resume_id}")
            db_resume_data = supabase_utils.get_customized_resume(customized_resume_id)
            if db_resume_data:
                logging.info(f"Successfully retrieved customized resume data from DB for job {job_id}")
                custom_resume_text = format_resume_to_text(db_resume_data)
            else:
                logging.warning(f"Could not find customized resume data in DB for ID {customized_resume_id}. Falling back to PDF.")

        # Fallback to PDF extraction if DB retrieval failed or ID was missing
        if not custom_resume_text and resume_link:
            logging.info(f"Attempting to extract text from custom resume PDF from {resume_link[:70]}...")
            custom_resume_text = extract_text_from_pdf_url(resume_link)

        if not custom_resume_text:
            logging.error(f"Failed to obtain custom resume text for job_id {job_id} from both DB and PDF. Skipping.")
            failed_rescores += 1
            if i < len(jobs_to_rescore) - 1:
                logging.debug(f"Waiting {config.LLM_REQUEST_DELAY_SECONDS} seconds before next job...")
                time.sleep(config.LLM_REQUEST_DELAY_SECONDS)
            continue
        
        logging.debug(f"Custom resume text for job {job_id} (first 200 chars): {custom_resume_text[:200]}")
        breakdown = get_resume_score_from_ai(custom_resume_text, job)

        if breakdown is not None:
            if supabase_utils.update_job_score(job_id, breakdown.overall_score, resume_score_stage="custom",
                                               score_breakdown=breakdown.model_dump()):
                successful_rescores += 1
            else:
                failed_rescores += 1 
        else:
            failed_rescores += 1 

        if i < len(jobs_to_rescore) - 1: 
            logging.debug(f"Waiting {config.LLM_REQUEST_DELAY_SECONDS} seconds before next API call...")
            time.sleep(config.LLM_REQUEST_DELAY_SECONDS)

    rescore_end_time = time.time()
    logging.info("--- Job Re-scoring Finished ---")
    logging.info(f"Successfully re-scored: {successful_rescores}")
    logging.info(f"Failed/Skipped re-scores: {failed_rescores}")
    logging.info(f"Total re-scoring time: {rescore_end_time - rescore_start_time:.2f} seconds")

# --- Main Execution ---

def main():
    """Main function to score jobs based on the target resume."""
    logging.info("--- Starting Job Scoring Script ---")
    overall_start_time = time.time()

    # --- Phase 1: Initial Scoring with Default Resume ---
    logging.info("--- Phase 1: Initial Scoring with Default Resume ---")
    initial_score_start_time = time.time()
    
    resume_path = getattr(config, 'BASE_RESUME_PATH', 'resume.json')
    
    # Try fetching resume from Supabase first, fall back to local file
    default_resume_data = supabase_utils.get_base_resume()
    
    if default_resume_data:
        logging.info("Successfully loaded base resume from Supabase database.")
    elif os.path.exists(resume_path):
        logging.info(f"Supabase fetch failed. Falling back to local file: {resume_path}")
        try:
            with open(resume_path, 'r', encoding='utf-8') as f:
                default_resume_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read or decode {resume_path}: {e}")
            default_resume_data = None
    else:
        logging.error(f"Base resume not found in Supabase or at '{resume_path}'. Please run the 'Parse Resume' workflow first.")

    default_resume_text = None
    if default_resume_data:
        # 2. Format Resume to Text
        default_resume_text = format_resume_to_text(default_resume_data)
        logging.info("Default resume data formatted to text.")

        # 3. Fetch Jobs to Score — via the cheap screening pass when enabled,
        #    so the expensive scorer only sees jobs that clear the hard gates.
        if getattr(config, 'SCREENING_ENABLED', False):
            jobs_to_score_initially = run_screening_phase()
        else:
            jobs_to_score_initially = supabase_utils.get_jobs_to_score(config.JOBS_TO_SCORE_PER_RUN)
        if not jobs_to_score_initially:
            logging.info("No jobs require initial scoring at this time.")
        else:
            logging.info(f"Processing {len(jobs_to_score_initially)} jobs for initial scoring...")
            successful_initial_scores = 0
            failed_initial_scores = 0

            # 4. Loop Through Jobs and Score Them
            for i, job in enumerate(jobs_to_score_initially):
                job_id = job.get('job_id')
                if not job_id:
                    logging.warning("Found job data without job_id during initial scoring. Skipping.")
                    failed_initial_scores +=1
                    continue

                logging.info(f"--- Initial Scoring Job {i+1}/{len(jobs_to_score_initially)} (ID: {job_id}) ---")
                breakdown = get_resume_score_from_ai(default_resume_text, job)

                if breakdown is not None:
                    if supabase_utils.update_job_score(job_id, breakdown.overall_score, resume_score_stage="initial",
                                                       score_breakdown=breakdown.model_dump()):
                        successful_initial_scores += 1
                        # Strong matches get a "why me" pitch for the application message / Anschreiben
                        if breakdown.overall_score >= 70 and breakdown.recommendation in ("apply_now", "apply_after_fixes"):
                            pitch = generate_why_me_pitch(default_resume_text, job, breakdown)
                            if pitch:
                                supabase_utils.update_job_pitch(job_id, pitch)
                    else:
                        failed_initial_scores += 1
                else:
                    failed_initial_scores += 1

                if i < len(jobs_to_score_initially) - 1:
                    logging.debug(f"Waiting {config.LLM_REQUEST_DELAY_SECONDS} seconds before next API call...")
                    time.sleep(config.LLM_REQUEST_DELAY_SECONDS)
            
            if successful_initial_scores == 0 and failed_initial_scores > 0:
                logging.error(
                    "ALL initial scoring attempts failed. This usually means an LLM auth/config problem "
                    f"(model='{config.LLM_MODEL}'). Check that the API key secret matches the model's provider "
                    "(e.g. ANTHROPIC_API_KEY for anthropic/* models)."
                )

            initial_score_end_time = time.time()
            logging.info("--- Initial Scoring Phase Finished ---")
            logging.info(f"Successfully initially scored: {successful_initial_scores}")
            logging.info(f"Failed/Skipped initial scores: {failed_initial_scores}")
            logging.info(f"Total initial scoring time: {initial_score_end_time - initial_score_start_time:.2f} seconds")

    # # --- Phase 2: Re-scoring with Custom Resumes ---
    rescore_jobs_with_custom_resume() 

    # --- Phase 3: Manual Jobs from JSON file ---
    # Imported here (not at module top) to avoid a circular import with manual_jobs.
    import manual_jobs
    if default_resume_text:
        manual_success, manual_failed = manual_jobs.process_manual_jobs(default_resume_text)
        if manual_success + manual_failed > 0:
            logging.info(f"Manual jobs: {manual_success} scored, {manual_failed} failed.")

    overall_end_time = time.time()
    logging.info("--- Job Scoring Script Finished (All Phases) ---")
    logging.info(f"Total script execution time: {overall_end_time - overall_start_time:.2f} seconds")


if __name__ == "__main__":
    if not config.LLM_API_KEY:
        logging.error("LLM_API_KEY environment variable not set. (Also accepts GEMINI_API_KEY / GEMINI_FIRST_API_KEY)")
    elif not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        logging.error("Supabase URL or Key environment variable not set.")
    else:
        main()