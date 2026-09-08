"""The shapes a tailored application comes in, and how they render to text.

Every line that makes a claim carries the fact ids it rests on. That is the
structural choice the whole package depends on: a writer that must cite cannot
quietly invent, and the citation is checkable mechanically rather than by asking
another model to be vigilant.
"""

from typing import List

from pydantic import BaseModel, Field


class Line(BaseModel):
    """One claim-bearing line, and the facts behind it."""

    text: str = Field(..., description="The line as it appears on the page.")
    fact_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Ids of every fact this line rests on. Never empty. Use the literal id "
            "'personal' for lines that state availability, notice period, location "
            "or contact details, which come from the personal details block rather "
            "than the fact base."
        ),
    )


class Block(BaseModel):
    """A role or project, with its bullets."""

    title: str = Field(..., description="Job title or project name.")
    organisation: str = Field("", description="Employer, or empty for a personal project.")
    dates: str = Field("", description="As written on the CV, e.g. 'Mar 2024 - Feb 2025'.")
    bullets: List[Line] = Field(default_factory=list)


class TailoredCV(BaseModel):
    headline: str = Field(
        ..., description="The role being applied for, as a title line. No adjectives."
    )
    summary: Line = Field(..., description="2-3 sentences. Must cite facts.")
    skills: List[str] = Field(
        default_factory=list,
        description="Skills to list, ordered by relevance to this posting. Every one "
                    "must be evidenced by some fact in the base.",
    )
    experience: List[Block] = Field(default_factory=list)
    projects: List[Block] = Field(default_factory=list)
    education: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)


class CoverLetter(BaseModel):
    subject: str = Field(..., description="Betreff line, naming the role.")
    greeting: str = Field(..., description="e.g. 'Sehr geehrte Damen und Herren,'")
    paragraphs: List[Line] = Field(
        default_factory=list, description="3-4 paragraphs. Each cites facts."
    )
    closing: str = Field(..., description="Sign-off line.")


class Application(BaseModel):
    cv: TailoredCV
    cover_letter: CoverLetter


def render_cv(cv: TailoredCV) -> str:
    """Plain text, the form the scorer and a human reviewer both read."""
    out = [cv.headline, "", cv.summary.text, ""]
    if cv.skills:
        out += ["SKILLS", ", ".join(cv.skills), ""]
    if cv.experience:
        out.append("EXPERIENCE")
        for block in cv.experience:
            head = " | ".join(p for p in (block.title, block.organisation, block.dates) if p)
            out.append(head)
            out += [f"  - {b.text}" for b in block.bullets]
            out.append("")
    if cv.projects:
        out.append("PROJECTS")
        for block in cv.projects:
            head = " | ".join(p for p in (block.title, block.dates) if p)
            out.append(head)
            out += [f"  - {b.text}" for b in block.bullets]
            out.append("")
    if cv.education:
        out += ["EDUCATION"] + [f"  {e}" for e in cv.education] + [""]
    if cv.languages:
        out += ["LANGUAGES", ", ".join(cv.languages), ""]
    return "\n".join(out).strip()


def render_cover_letter(letter: CoverLetter) -> str:
    parts = [letter.subject, "", letter.greeting, ""]
    parts += [p.text for p in letter.paragraphs]
    parts += ["", letter.closing]
    return "\n\n".join(p for p in parts if p is not None).strip()


def all_lines(app: Application) -> List[Line]:
    """Every claim-bearing line, for the verifier to walk."""
    lines = [app.cv.summary]
    for block in list(app.cv.experience) + list(app.cv.projects):
        lines.extend(block.bullets)
    lines.extend(app.cover_letter.paragraphs)
    return lines
