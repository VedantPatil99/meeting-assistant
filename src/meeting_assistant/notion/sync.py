"""Write meetings and tasks into two Notion databases, creating them on first setup.

Meetings: one page per meeting, with the full report as the page body.
Tasks: one row per action item, linked back to its meeting page.
Writes are idempotent: a meeting or task that already exists is found and reused, so a
re-run or a second approval does not create duplicates.
"""

from __future__ import annotations

from ..models import ConflictVerdict, CoverageStatus, MeetingReport, Point
from ..automations.models import MEETING_TYPES
from .client import NotionClient

TEXT_LIMIT = 2000  # characters per rich-text object

COVERAGE_WORDS = {
    CoverageStatus.COVERED: "covered",
    CoverageStatus.REPAIRED: "added by repair pass",
    CoverageStatus.FORCED: "inserted verbatim",
    CoverageStatus.CONFLICT: "left out: contradicts the meeting",
}


def rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[i:i + TEXT_LIMIT]}}
            for i in range(0, max(len(text), 1), TEXT_LIMIT)] if text else []


def block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text), **extra}}


def _point(p: Point) -> str:
    return f"{p.text} [{p.timestamp}]" if p.timestamp else p.text


def meetings_schema() -> dict:
    return {
        "Name": {"title": {}},
        "Date": {"date": {}},
        "Type": {"select": {"options": [{"name": t} for t in MEETING_TYPES]}},
        "Attendees": {"rich_text": {}},
        "Decisions": {"number": {}},
        "Action items": {"number": {}},
        "Conflicts": {"number": {}},
        "Covered without repair": {"number": {"format": "percent"}},
    }


def tasks_schema(meetings_db: str) -> dict:
    return {
        "Task": {"title": {}},
        "Owner": {"rich_text": {}},
        "Due": {"rich_text": {}},
        "Status": {"select": {"options": [{"name": "To do"}, {"name": "In progress"}, {"name": "Done"}]}},
        "Meeting": {"relation": {"database_id": meetings_db, "single_property": {}}},
    }


def report_blocks(report: MeetingReport) -> list[dict]:
    s = report.summary
    anchors = {a.id: a for a in report.anchors}
    blocks: list[dict] = []

    def section(title: str, lines: list[str], kind: str = "bulleted_list_item") -> None:
        blocks.append(block("heading_2", title))
        if lines:
            blocks.extend(block(kind, line, **({"checked": False} if kind == "to_do" else {})) for line in lines)
        else:
            blocks.append(block("paragraph", "None"))

    section("Decisions", [_point(p) for p in s.decisions])
    section("Action items", [
        a.task + (f" ({', '.join(x for x in [a.owner, a.due and f'due {a.due}'] if x)})" if a.owner or a.due else "")
        for a in s.action_items
    ], kind="to_do")
    section("Open questions", [_point(p) for p in s.open_questions])
    section("Key points", [_point(p) for p in s.key_points])
    section("Your flagged notes", [
        f"{anchors[c.anchor_id].text}: {COVERAGE_WORDS[c.status]}" for c in report.coverage if c.anchor_id in anchors
    ])
    blocks.append(block("heading_2", "Conflicts with your notes"))
    conflicts = [c for c in report.conflicts if c.verdict == ConflictVerdict.CONTRADICTS and c.anchor_id in anchors]
    for c in conflicts:
        quote = f" Transcript at {c.timestamp}: \"{c.transcript_quote}\"" if c.transcript_quote else ""
        blocks.append(block("callout", f"\"{anchors[c.anchor_id].text}\": {c.explanation}{quote}",
                            icon={"type": "emoji", "emoji": "⚠️"}))
    if not conflicts:
        blocks.append(block("paragraph", "None found"))
    return blocks


class NotionSync:
    def __init__(self, client: NotionClient, meetings_db: str, tasks_db: str):
        self.client = client
        self.meetings_db = meetings_db
        self.tasks_db = tasks_db

    @staticmethod
    def setup(client: NotionClient, parent_page_id: str) -> tuple[str, str]:
        """Create the Meetings and Tasks databases under a page shared with the integration."""
        meetings = client.create_database(parent_page_id, "Meetings", meetings_schema())
        tasks = client.create_database(parent_page_id, "Tasks", tasks_schema(meetings))
        return meetings, tasks

    def check(self) -> None:
        self.client.get_database(self.meetings_db)
        self.client.get_database(self.tasks_db)

    def find_meeting(self, title: str, date: str) -> dict | None:
        filters = [{"property": "Name", "title": {"equals": title}}]
        if date:
            filters.append({"property": "Date", "date": {"equals": date[:10]}})
        results = self.client.query(self.meetings_db, {"and": filters})
        return results[0] if results else None

    def save_meeting(self, report: MeetingReport, date: str, attendees: list[str]) -> str:
        existing = self.find_meeting(report.title, date)
        if existing:
            return existing["url"]
        coverage = [c for c in report.coverage if c.status != CoverageStatus.CONFLICT]
        props = {
            "Name": {"title": rich(report.title)},
            "Attendees": {"rich_text": rich(", ".join(attendees))},
            "Decisions": {"number": len(report.summary.decisions)},
            "Action items": {"number": len(report.summary.action_items)},
            "Conflicts": {"number": sum(c.verdict == ConflictVerdict.CONTRADICTS for c in report.conflicts)},
            "Covered without repair": {"number": round(report.coverage_rate_before_repair, 2) if coverage else None},
        }
        if report.meeting_type:
            props["Type"] = {"select": {"name": report.meeting_type}}
        if date:
            props["Date"] = {"date": {"start": date[:10]}}
        return self.client.create_page(self.meetings_db, props, report_blocks(report))["url"]

    def save_task(self, text: str, owner: str, due: str, meeting_title: str, meeting_date: str) -> str | None:
        """Create the task unless it already exists for this meeting. Returns its URL, or None for a duplicate."""
        meeting = self.find_meeting(meeting_title, meeting_date)
        filters = [{"property": "Task", "title": {"equals": text}}]
        if meeting:
            filters.append({"property": "Meeting", "relation": {"contains": meeting["id"]}})
        if self.client.query(self.tasks_db, {"and": filters}):
            return None
        props = {
            "Task": {"title": rich(text)},
            "Owner": {"rich_text": rich(owner)},
            "Due": {"rich_text": rich(due)},
            "Status": {"select": {"name": "To do"}},
        }
        if meeting:
            props["Meeting"] = {"relation": [{"id": meeting["id"]}]}
        return self.client.create_page(self.tasks_db, props)["url"]
