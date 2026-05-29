from pydantic import BaseModel, Field
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

class ScoreBreakdown(BaseModel):
    overall_score: int = Field(..., ge=0, le=100, description="Overall suitability score 0-100")
    skills_match_score: int = Field(..., ge=0, le=100, description="How well the candidate's skills match the job requirements")
    experience_score: int = Field(..., ge=0, le=100, description="Relevance of the candidate's experience level and domain")
    education_score: int = Field(..., ge=0, le=100, description="How well the candidate's education matches")
    language_fit: str = Field(..., description="Language assessment: e.g. 'Full match', 'Partial - B1 German may suffice', 'Mismatch - job requires fluent German'")
    key_matching_skills: List[str] = Field(default_factory=list, description="Top skills from resume that match the job")
    key_gaps: List[str] = Field(default_factory=list, description="Important skills/requirements the candidate lacks")
    recommendation: str = Field(..., description="One of: 'strong_apply', 'apply', 'consider', 'skip'")
    reasoning: str = Field(..., description="2-3 sentence explanation of the score")

class Config:
    extra = 'allow'