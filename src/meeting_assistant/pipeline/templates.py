"""Summary templates per meeting type.

Every summary has decisions, action items, open questions and key points. A template adds the
sections that kind of meeting needs: blockers for a standup, budget and decision process for a
sales call. The extra sections become required fields in the JSON schema the model must fill,
so a standup summary always has a place for blockers, and an empty list means none were said.

The meeting type is free text, so a template is picked by keywords ("sales discovery" -> sales).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from pydantic import Field, create_model

from ..models import Point, Summary


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    description: str


@dataclass(frozen=True)
class Template:
    name: str
    keywords: tuple[str, ...]
    sections: tuple[Section, ...]


TEMPLATES = (
    Template("standup", ("standup", "stand-up", "daily"), (
        Section("updates", "Updates", "One item per person who gave an update: their name, what they "
                "finished and what they are doing next"),
        Section("blockers", "Blockers", "Anything stopping someone: who is blocked and by what"),
    )),
    Template("planning", ("planning", "sprint"), (
        Section("sprint_goal", "Sprint goal", "The goal agreed for the sprint or period"),
        Section("committed_work", "Committed work", "Each story or task taken on, with who owns it"),
        Section("risks", "Risks and dependencies", "Things that could stop the plan, and what it depends on"),
    )),
    Template("sales", ("sales", "discovery", "prospect", "demo"), (
        Section("pain_points", "Pain points", "Problems the prospect wants solved, as they described them"),
        Section("requirements", "Requirements", "Must-haves the prospect stated"),
        Section("budget_and_timeline", "Budget and timeline", "What was said about budget, contract dates "
                "and when they want to decide or go live"),
        Section("decision_process", "Decision process", "Who decides, who else is involved and how"),
    )),
    Template("interview", ("interview", "candidate", "hiring"), (
        Section("strengths", "Strengths", "Evidence of the candidate's strengths, from what they said"),
        Section("concerns", "Concerns", "Gaps or concerns raised"),
        Section("recommendation", "Recommendation", "The interviewers' overall view if one was stated; "
                "leave empty rather than guess"),
    )),
    Template("incident", ("incident", "postmortem", "post-mortem", "outage"), (
        Section("timeline", "Timeline", "What happened when, in order"),
        Section("root_cause", "Root cause", "The cause the team identified"),
        Section("impact", "Impact", "Who or what was affected, and for how long"),
    )),
    Template("client", ("client", "customer", "feedback", "review", "qbr"), (
        Section("working_well", "What's working", "What the client said is going well"),
        Section("issues", "Issues raised", "Complaints, problems and frustrations the client raised"),
        Section("commercial", "Commercial", "Contract, pricing or renewal points"),
    )),
)


def template_for(meeting_type: str) -> Template | None:
    text = meeting_type.lower()
    return next((t for t in TEMPLATES if any(k in text for k in t.keywords)), None)


@cache
def summary_schema(template: Template | None) -> type[Summary]:
    """The schema the model fills: Summary plus one list per template section."""
    if template is None:
        return Summary
    fields = {s.key: (list[Point], Field(description=s.description)) for s in template.sections}
    return create_model(f"{template.name.title()}Summary", __base__=Summary, **fields)


def to_summary(result: Summary, template: Template | None) -> Summary:
    """Move the template's fields into Summary.sections, so the rest of the app sees one type."""
    if template is None:
        return result
    core = {k: getattr(result, k) for k in Summary.model_fields if k != "sections"}
    return Summary(**core, sections={s.key: getattr(result, s.key) for s in template.sections})


def section_title(meeting_type: str, key: str) -> str:
    template = template_for(meeting_type)
    for s in template.sections if template else ():
        if s.key == key:
            return s.title
    return key.replace("_", " ").capitalize()


def section_instructions(template: Template | None) -> str:
    if template is None:
        return ""
    lines = "\n".join(f"- {s.key}: {s.description}" for s in template.sections)
    return f"\nThis is a {template.name} meeting, so also fill these sections (empty list if not discussed):\n{lines}"
