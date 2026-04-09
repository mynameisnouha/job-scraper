from pydantic import BaseModel, Field
from typing import List, Optional


# --- Core Resume Component Models ---

class Education(BaseModel):
    degree: str = ""
    institution: str = ""
    dates: str = ""
    location: str = ""


class Experience(BaseModel):
    job_title: str = ""
    company: str = ""
    start_date: str = ""
    end_date: str = ""
    dates: str = ""
    location: str = ""
    description: str = ""


class Project(BaseModel):
    name: str = ""
    description: str = ""
    technologies: List[str] = Field(default_factory=list)
    link: str = ""


class Certification(BaseModel):
    name: str = ""
    issuer: str = ""
    date: str = ""


class Links(BaseModel):
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    other: str = ""


# --- Full Resume Model ---

class Resume(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""

    skills: List[str] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    links: Optional[Links] = None

    class Config:
        extra = "allow"


# --- LLM Structured Output Models ---
# Used as response_format in generate_content() calls.
# Each wraps a single field so the grammar schema stays simple.

class SummaryOutput(BaseModel):
    """LLM output schema for the summary section."""
    summary: str


class SkillsOutput(BaseModel):
    """LLM output schema for the skills section."""
    skills: List[str]


class SingleExperienceOutput(BaseModel):
    """LLM output schema for a single experience item."""
    experience: Experience


class ExperienceListOutput(BaseModel):
    """LLM output schema for the full experience list."""
    experience: List[Experience]


class SingleProjectOutput(BaseModel):
    """LLM output schema for a single project item."""
    project: Project


class ProjectListOutput(BaseModel):
    """LLM output schema for the full projects list."""
    projects: List[Project]


class ValidationResponse(BaseModel):
    """LLM output schema for the validation step."""
    is_valid: bool
    reason: str


# --- Parsing Output (used by resume_parser.py) ---

class ResumeOutput(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    summary: str
    skills: List[str]
    experience: List[Experience]
    education: List[Education]
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    links: Optional[Links] = None