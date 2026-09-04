from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime

class Education(BaseModel):
    degree: str = ""
    field_of_study: str = ""
    institution: str = ""
    start_year: str = ""
    end_year: str = ""

class Experience(BaseModel):
    job_title: str = ""
    company: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""

class Project(BaseModel):
    name: str = ""
    description: str = ""
    technologies: List[str] = Field(default_factory=list)

class Certification(BaseModel):
    name: str = ""
    issuer: str = ""
    year: str = ""

class Links(BaseModel):
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""

class Resume(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""
    skills: List[str] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    links: Links = Field(default_factory=Links)

# --- Pydantic models for LLM structured output ---
class SummaryOutput(BaseModel):
    summary: str

class SkillsOutput(BaseModel):
    skills: List[str]

class ExperienceListOutput(BaseModel):
    experience: List[Experience]

class SingleExperienceOutput(BaseModel):
    experience: Experience

class ProjectListOutput(BaseModel):
    projects: List[Project]

class SingleProjectOutput(BaseModel):
    project: Project

class ValidationResponse(BaseModel):
    is_valid: bool
    reason: str

class ScreenResult(BaseModel):
    passes: bool = Field(..., description="True if the job clears all hard gates and deserves a full detailed evaluation")
    rough_score: int = Field(..., ge=0, le=100, description="Quick fit estimate 0-100")
    reason: str = Field(..., description="One short sentence: why it passes or fails")

class PitchOutput(BaseModel):
    pitch: str = Field(..., description="3-4 sentence first-person pitch mapping the candidate's strongest evidence to the job's top requirements")

class InterviewProbability(BaseModel):
    """
    The scorer's own odds of landing a first-round interview. Required (not
    optional-with-default) so the JSON schema forces the model to produce it:
    this is the number every calibration check is measured against, and when it
    was optional roughly a third of scored jobs came back without it.
    """
    as_is: float = Field(..., description="Probability of a first-round interview with the CV as it stands today, 0-1")
    after_fixes: float = Field(..., description="Probability once every fixable_before_applying item is done, 0-1")

    @field_validator("as_is", "after_fixes", mode="before")
    @classmethod
    def _normalize(cls, v):
        """
        Accept 15 as well as 0.15 — the model emits both for '15%'. Coercing
        here (rather than rejecting) keeps a whole score from being lost to a
        units slip. Clamped so a nonsense value can't leave the 0-1 range.
        """
        try:
            p = float(v)
        except (TypeError, ValueError):
            return v
        if p > 1:
            p = p / 100.0
        return min(max(p, 0.0), 1.0)


class CompetitiveContext(BaseModel):
    """Who else is applying, and how the candidate ranks against them."""
    estimated_applicant_volume: str = Field(..., description="Rough applicant count or band for this posting")
    modal_competitor: str = Field(..., description="The profile of the typical competing candidate")
    candidate_percentile: int = Field(..., description="Where the candidate sits in that pool, 0-100")
    p_first_round_interview: InterviewProbability = Field(..., description="Calibrated interview odds")


class DimensionScores(BaseModel):
    """The weighted sub-scores behind the headline number, 0-100 each."""
    must_have_coverage: int = Field(..., description="Weighted must-haves met, by evidence tier (35%)")
    evidence_strength: int = Field(..., description="Are matches tier 1-2, or inferred? (15%)")
    nice_to_have_coverage: int = Field(..., description="Weighted nice-to-haves met (10%)")
    seniority_fit: int = Field(..., description="Experience ledger vs the role's real level (15%)")
    environment_fit: int = Field(..., description="Company stage/pace vs the candidate's track record (10%)")
    domain_fit: int = Field(..., description="Industry/regulatory familiarity (5%)")
    differentiation: int = Field(..., description="How rare the candidate's strongest asset is in this pool (10%)")


class ScoreBreakdown(BaseModel):
    overall_score: int = Field(..., ge=0, le=100, description="Overall suitability score 0-100")
    skills_match_score: int = Field(..., ge=0, le=100, description="How well the candidate's skills match the job requirements")
    experience_score: int = Field(..., ge=0, le=100, description="Relevance of the candidate's experience level and domain")
    education_score: int = Field(..., ge=0, le=100, description="How well the candidate's education matches")
    language_fit: str = Field(..., description="Language assessment: e.g. 'Full match', 'Partial - B1 German may suffice', 'Mismatch - job requires fluent German'")
    # --- Hard requirements extracted verbatim from the JD (used for gating, not vibes) ---
    german_required: str = Field("unclear", description="German level the JD actually requires: 'none' (not mentioned / English ok), 'nice-to-have', 'B2', 'C1-fluent' (fluent/native/verhandlungssicher required), or 'unclear'")
    years_experience_required: int = Field(0, ge=0, description="Minimum years of professional experience the JD explicitly requires. 0 if not stated or entry-level.")
    jd_language: str = Field("en", description="Language the job description is written in: 'en', 'de', or 'mixed'")
    visa_sponsorship_mentioned: str = Field("unclear", description="Does the JD mention visa/relocation support: 'yes', 'no', or 'unclear'")
    key_matching_skills: List[str] = Field(default_factory=list, description="Top skills from resume that match the job")
    key_gaps: List[str] = Field(default_factory=list, description="Important skills/requirements the candidate lacks")
    recommendation: str = Field(..., description="One of: 'apply_now', 'apply_after_fixes', 'apply_if_gate_negotiable', 'skip'")
    reasoning: str = Field(..., description="2-3 sentence explanation of the score")

    # --- v2 fields: hard gates, weighted dimensions, competitive context, calibration ---
    hard_gates: List[Dict[str, Any]] = Field(default_factory=list,
        description="One entry per gate evaluated: {gate, result: pass/fail/unknown, cap_applied, detail, negotiable, how}")
    dimension_scores: DimensionScores = Field(...,
        description="0-100 per dimension — every dimension must be scored")
    differentiators: List[str] = Field(default_factory=list, description="What the candidate has that the median applicant likely does not")
    disqualifier_matches: List[str] = Field(default_factory=list, description="Matches against the JD's explicit 'not for you if' section")
    fixable_before_applying: List[Dict[str, Any]] = Field(default_factory=list,
        description="Gaps that are true but not shown on the CV: [{gap, fix, effort_minutes}]")
    fixable_in_two_weeks: List[Dict[str, Any]] = Field(default_factory=list,
        description="Gaps closeable with a weekend project or write-up: [{gap, fix, effort_minutes}]")
    structural_gaps: List[str] = Field(default_factory=list, description="Gaps that cannot be fixed before this application closes")
    competitive_context: CompetitiveContext = Field(...,
        description="Applicant pool and the calibrated interview odds — required, never omit")
    company_stage: str = Field("", description="Inferred company stage/headcount, e.g. 'startup <50', 'mid-size', 'large enterprise'")
    working_language_of_product: str = Field("", description="Language the product's users/data/internal docs are actually in, distinct from JD language")
    remote_scope: str = Field("", description="Countries/timezone remote work is actually scoped to, if remote")
    regulatory_context: str = Field("", description="MDR/GDPR/HIPAA/SOC2/medical-device or other regulatory context, if any")
    sponsorship_signal: str = Field("unclear", description="explicit / implied / absent / explicitly_excluded")
    salary_band: str = Field("", description="Stated salary band, or '' if not stated")
    posting_age_days: Optional[int] = Field(None, description="If stated or inferable")
    is_agency_or_staffing_firm: bool = Field(False, description="True if this posting is from a recruiting/staffing agency, not the hiring company")
    application_effort_hours: float = Field(1.0, description="Estimated hours to complete the application")
    application_effort_estimate: str = Field("", description="e.g. 'CV + cover letter upload' vs '3 essay questions'")
    expected_value: float = Field(0.0, description="p_first_round_interview.after_fixes / application_effort_hours")
    calibration_check: Dict[str, Any] = Field(default_factory=dict,
        description="must_haves_total, must_haves_met_tier_1_or_2, hard_gates_failed, cap_applied, score_before_cap, final_score, would_a_skeptical_recruiter_agree, confidence, confidence_reason")
    one_line_verdict: str = Field(..., description="One sentence: the honest bottom line")

class Config:
    extra = 'allow'