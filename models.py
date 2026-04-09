from pydantic import BaseModel, Field
from typing import List

# --- Flattened nested models to reduce optional parameters ---
class Education(BaseModel):
    degree: str = ""
    institution: str = ""

class Experience(BaseModel):
    job_title: str = ""
    company: str = ""
    start_date: str = ""
    end_date: str = ""

class Resume(BaseModel):
    # Essential personal info
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""

    # Essential structured fields (limit the number of optional params)
    skills: List[str] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)

    # Optional fields removed or moved out of AI parsing
    # projects, certifications, languages, links -> handle outside LLM

# --- Example output schema for LiteLLM / Anthropic ---
class ResumeOutput(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    summary: str
    skills: List[str]
    experience: List[Experience]
    education: List[Education]

class Config:
    extra = "allow"