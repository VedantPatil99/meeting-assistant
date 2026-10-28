"""What an automation sees when it runs: the meeting, its report, and the item that fired it.

Also renders email templates. Text going to external recipients never includes summary
lines that came from `internal:` notes, or the conflicts section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import (
    ActionItem,
    AnchorKind,
    ConflictResult,
    ConflictVerdict,
    MeetingReport,
    Point,
)
from .models import FIELDS, Condition, Trigger

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_.]+)\s*\}\}")

PLACEHOLDERS = {
    "meeting.title": "Meeting title",
    "meeting.type": "Meeting type",
    "meeting.date": "Meeting date",
    "decisions": "Decisions",
    "action_items": "Action items",
    "open_questions": "Open questions",
    "key_points": "Key points",
    "conflicts": "Conflicts (internal only)",
    "item.task": "Action item",
    "item.owner": "Action item owner",
    "item.due": "Action item due date",
    "conflict.note": "Conflicting note",
    "conflict.explanation": "What the meeting said",
}


@dataclass
class Prefs:
    my_email: str = ""
    company_domain: str = ""

    def is_internal(self, address: str) -> bool:
        domain = self.company_domain.strip().lstrip("@").lower()
        return bool(domain) and address.lower().endswith("@" + domain)


@dataclass
class EventContext:
    trigger: Trigger
    report: MeetingReport
    date: str = ""
    attendees: list[str] = field(default_factory=list)
    folder: Path | None = None
    item: ActionItem | None = None
    conflict: ConflictResult | None = None


def events_from_report(
    report: MeetingReport, date: str = "", attendees: list[str] | None = None, folder: Path | None = None
) -> list[EventContext]:
    """Every event one processed meeting produces, in the order rules should see them."""
    base = dict(report=report, date=date, attendees=attendees or [], folder=folder)
    events = [EventContext(Trigger.MEETING_PROCESSED, **base)]
    events += [EventContext(Trigger.ACTION_ITEM, item=item, **base) for item in report.summary.action_items]
    events += [
        EventContext(Trigger.CONFLICT_FOUND, conflict=c, **base)
        for c in report.conflicts
        if c.verdict == ConflictVerdict.CONTRADICTS
    ]
    return events


# conditions

def field_value(key: str, ctx: EventContext, prefs: Prefs):
    report = ctx.report
    if key == "meeting_type":
        return report.meeting_type
    if key == "title":
        return report.title
    if key == "has_external":
        return any(not prefs.is_internal(a) for a in ctx.attendees)
    if key == "has_conflicts":
        return any(c.verdict == ConflictVerdict.CONTRADICTS for c in report.conflicts)
    if key == "has_feature_requests":
        return any(a.kind == AnchorKind.FEATURE for a in report.anchors)
    if key == "action_item_count":
        return len(report.summary.action_items)
    if key == "item_owner":
        return (ctx.item.owner or "") if ctx.item else ""
    if key == "item_text":
        return ctx.item.task if ctx.item else ""
    raise KeyError(key)


def condition_matches(cond: Condition, ctx: EventContext, prefs: Prefs) -> bool:
    spec = FIELDS[cond.field]
    actual = field_value(cond.field, ctx, prefs)
    want = cond.value.strip()
    if spec.kind == "bool":
        return actual == (want.lower() == "true")
    if spec.kind == "number":
        return actual >= int(want or 0)
    actual, want = str(actual).lower(), want.lower()
    return {
        "is": actual == want,
        "is_not": actual != want,
        "contains": want in actual,
        "not_contains": want not in actual,
    }[cond.op]


# templates

def internal_lines(report: MeetingReport) -> set[str]:
    """Summary lines that exist because of an internal: note, so they never reach clients."""
    internal_ids = {a.id for a in report.anchors if a.kind == AnchorKind.INTERNAL}
    return {c.evidence for c in report.coverage if c.anchor_id in internal_ids and c.evidence}


def _bullets(points: list[Point], hidden: set[str]) -> str:
    lines = [f"- {p.text}" for p in points if p.text not in hidden]
    return "\n".join(lines) or "- None"


def _action_items(items: list[ActionItem], hidden: set[str]) -> str:
    lines = []
    for a in items:
        if a.task in hidden:
            continue
        meta = ", ".join(x for x in [a.owner, a.due and f"due {a.due}"] if x)
        lines.append(f"- {a.task}" + (f" ({meta})" if meta else ""))
    return "\n".join(lines) or "- None"


def template_values(ctx: EventContext, external: bool) -> dict[str, str]:
    report, s = ctx.report, ctx.report.summary
    hidden = internal_lines(report) if external else set()
    anchors = {a.id: a for a in report.anchors}
    conflicts = [
        f"- \"{anchors[c.anchor_id].text}\": {c.explanation}"
        for c in report.conflicts
        if c.verdict == ConflictVerdict.CONTRADICTS and c.anchor_id in anchors
    ]
    values = {
        "meeting.title": report.title,
        "meeting.type": report.meeting_type,
        "meeting.date": ctx.date,
        "decisions": _bullets(s.decisions, hidden),
        "action_items": _action_items(s.action_items, hidden),
        "open_questions": _bullets(s.open_questions, hidden),
        "key_points": _bullets(s.key_points, hidden),
        "conflicts": "" if external else ("\n".join(conflicts) or "- None"),
        "item.task": ctx.item.task if ctx.item else "",
        "item.owner": (ctx.item.owner or "") if ctx.item else "",
        "item.due": (ctx.item.due or "") if ctx.item else "",
        "conflict.note": anchors[ctx.conflict.anchor_id].text if ctx.conflict and ctx.conflict.anchor_id in anchors else "",
        "conflict.explanation": ctx.conflict.explanation if ctx.conflict else "",
    }
    return values


def render(template: str, ctx: EventContext, external: bool = False) -> str:
    values = template_values(ctx, external)
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def unknown_placeholders(template: str) -> list[str]:
    return [name for name in PLACEHOLDER_RE.findall(template) if name not in PLACEHOLDERS]
