"""What the app keeps after a meeting is processed: suggested tasks and follow-up drafts,
and the steps that move a draft into Gmail or send it.

Nothing leaves the app on its own. Tasks start as suggestions to schedule or dismiss, and
drafts stay local until the user creates them in Gmail or presses Send.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .automations.context import Prefs
from .automations.store import Store
from .models import MeetingReport
from .pipeline.followups import SuggestedEmail
from .scheduling import resolve_due


@dataclass
class Outputs:
    """What processing produces besides the report."""
    emails: list[SuggestedEmail] = field(default_factory=list)
    feature_links: list[int | None] = field(default_factory=list)  # tracked feature per request, or None


def save_suggestions(store: Store, report: MeetingReport, date: str, folder: Path | None,
                     emails: list[SuggestedEmail]) -> tuple[int, int]:
    """Stores the meeting's action items as suggested tasks and its follow-ups as drafts.
    Returns how many of each were added."""
    tasks = 0
    for item in report.summary.action_items:
        due_date = resolve_due(item.due, date or None)
        added = store.add_task(item.task, item.owner or "", item.due or "", report.title,
                               due_date=due_date.isoformat() if due_date else None,
                               status="suggested", folder=str(folder or ""))
        tasks += added is not None
    for email in emails:
        store.add_draft(report.title, str(folder or ""), email.recipient, email.to, email.reason,
                        email.subject, email.body, email.audience, email.warning)
    return tasks, len(emails)


def client_names(attendees: list[str], prefs: Prefs) -> str:
    """The outside companies in the meeting, by email domain: "finedge.com, other.io".
    Your own email's domain never counts, even before a company domain is set."""
    mine = prefs.my_email.split("@")[-1].lower() if "@" in prefs.my_email else None
    domains = sorted({a.split("@")[-1].lower() for a in attendees
                      if "@" in a and not prefs.is_internal(a)} - {mine})
    return ", ".join(domains)


def save_features(store: Store, report: MeetingReport, date: str, folder: Path | None,
                  attendees: list[str], prefs: Prefs, links: list[int | None]) -> tuple[int, int]:
    """Adds the meeting's feature requests to the tracker. `links[i]` is the tracked feature that
    request i repeats, or None for a new one. Returns (new features, repeats of known ones)."""
    new = repeats = 0
    client = client_names(attendees, prefs)
    for request, link in zip(report.feature_requests, links):
        feature_id = link if link is not None and store.feature(link) else None
        if feature_id is None:
            feature_id = store.add_feature(request.title, request.detail)
            new += 1
        else:
            repeats += 1
        store.add_mention(feature_id, report.title, str(folder or ""), date, request.requested_by or "",
                          request.quote or "", request.timestamp or "", client, request.flagged)
    return new, repeats


def recipients(row) -> list[str]:
    return json.loads(row["to_addrs"] or "[]")


def push_to_gmail(store: Store, gmail, draft_id: int) -> str:
    """Creates (or updates) the draft in the user's Gmail Drafts folder. Returns the Gmail draft id."""
    row = store.draft(draft_id)
    to = recipients(row)
    if row["gmail_id"]:
        gmail_id = gmail.update_draft(row["gmail_id"], to, row["subject"], row["body"])
    else:
        gmail_id = gmail.create_draft(to, row["subject"], row["body"])
    store.update_draft(draft_id, gmail_id=gmail_id, status="in gmail")
    if row["recipient"] and len(to) == 1:
        store.remember_contact(row["recipient"], to[0])
    return gmail_id


def send(store: Store, gmail, draft_id: int) -> None:
    """Sends the draft through Gmail. Uses the Gmail draft if there is one, so it leaves Drafts."""
    row = store.draft(draft_id)
    to = recipients(row)
    if not to:
        raise ValueError("Add at least one recipient before sending.")
    if row["gmail_id"]:
        gmail.update_draft(row["gmail_id"], to, row["subject"], row["body"])
        gmail.send_draft(row["gmail_id"])
    else:
        gmail.send(to, row["subject"], row["body"])
    store.update_draft(draft_id, status="sent")
    if row["recipient"] and len(to) == 1:
        store.remember_contact(row["recipient"], to[0])
