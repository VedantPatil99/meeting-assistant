"""Suggest who should get a follow-up email after a meeting, and draft each one.

Two steps, so a small model does one job at a time:
1. Pick recipients from the meeting: who was asked for something, promised something, or
   needs the outcome. Each gets a reason the user sees in the app.
2. Draft one email per recipient. An email to someone outside the company is written from a
   summary with every line that came from an internal note removed, and the model never sees
   the internal notes. A final check flags any draft that still repeats one.

Names are matched to email addresses from the attendee list and the app's contacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..automations.context import Prefs, internal_lines
from ..llm import LLMProvider
from ..models import AnchorKind, MeetingReport
from ..scheduling import resolve_due
from .prompts import SYSTEM

MAX_SUGGESTIONS = 4
STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "have", "will", "they", "their", "about"}


class Recipient(BaseModel):
    name: str = Field(description="The person's name as said in the meeting, or their role if no name")
    audience: Literal["internal", "external"] = Field(
        description="internal = a colleague at the user's company, external = a client, vendor or partner")
    reason: str = Field(description="One sentence: why this person needs a follow-up")


class RecipientList(BaseModel):
    recipients: list[Recipient]


class EmailDraft(BaseModel):
    subject: str
    body: str


@dataclass
class SuggestedEmail:
    recipient: str
    to: list[str]
    audience: str
    reason: str
    subject: str
    body: str
    warning: str = ""


def summary_text(report: MeetingReport, hide: set[str], meeting_date: str = "") -> str:
    """The summary as plain text, without the lines in `hide`. Due dates are given as the
    calendar date too, so the model copies a date instead of working one out."""
    s = report.summary

    def action(a) -> str:
        line = a.task + (f" (owner: {a.owner})" if a.owner else "")
        if a.due:
            when = resolve_due(a.due, meeting_date or None) if meeting_date else None
            line += f" (due: {a.due}" + (f", which is {when:%A %d %B %Y}" if when else "") + ")"
        return line

    sections = [("Decisions", [p.text for p in s.decisions if p.text not in hide]),
                ("Action items", [action(a) for a in s.action_items if a.task not in hide]),
                ("Open questions", [p.text for p in s.open_questions if p.text not in hide]),
                ("Key points", [p.text for p in s.key_points if p.text not in hide])]
    return "\n\n".join(title + ":\n" + "\n".join(f"- {line}" for line in lines)
                        for title, lines in sections if lines)


def name_tokens(address: str) -> set[str]:
    local = address.split("@")[0].lower()
    return {t for t in re.split(r"[._\-+0-9]+", local) if len(t) >= 2}


def match_address(name: str, attendees: list[str], contacts: dict[str, str]) -> str | None:
    """An email address for a spoken name: a saved contact first, then an attendee whose
    address contains the first name (rahul.mehta@client.com for "Rahul")."""
    wanted = name.strip().lower()
    for contact, email in contacts.items():
        if contact.lower() == wanted:
            return email
    words = [w for w in re.split(r"\W+", wanted) if len(w) >= 2]
    if not words:
        return None
    for contact, email in contacts.items():
        if contact.lower().split()[0] == words[0]:
            return email
    hits = [a for a in attendees if words[0] in name_tokens(a)]
    return hits[0] if len(hits) == 1 else None


def content_words(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 3 and w not in STOPWORDS]
    return [w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in words]


def leaked_note(body: str, notes: list[str]) -> str | None:
    """An internal note whose wording shows up in the draft: three of its distinctive words
    in a row, or at least three quarters of them anywhere."""
    body_words = content_words(body)
    body_text, body_set = " ".join(body_words), set(body_words)
    for note in notes:
        words = content_words(note)
        if any(" ".join(words[i:i + 3]) in body_text for i in range(len(words) - 2)):
            return note
        if len(words) >= 3 and sum(w in body_set for w in words) / len(words) >= 0.75:
            return note
    return None


def recipients_prompt(report: MeetingReport, attendees: list[str], prefs: Prefs) -> str:
    notes = [a.text for a in report.anchors if a.kind != AnchorKind.INTERNAL]
    outside = [a for a in attendees if prefs.company_domain and not prefs.is_internal(a)]
    return f"""Meeting: {report.title} ({report.meeting_type})
Attendee emails: {', '.join(attendees) or 'not given'}
Attendees from outside the user's company (clients, partners): {', '.join(outside) or 'unknown'}
The user writing the follow-ups: {prefs.my_email or 'not given'}

Summary:
{summary_text(report, set())}

User's notes:
{chr(10).join(f'- {n}' for n in notes) or '- none'}

Who should the user send a follow-up email to after this meeting? Pick people who were
promised something, were asked for something, own an action item, or need to know a decision.
After a meeting with a client or partner, the main person on their side should get a recap of
what was agreed and the next steps. Do not include the user. Pick at most {MAX_SUGGESTIONS}.
One entry per person. Return an empty list if no follow-up is needed."""


PLACEHOLDER_RE = re.compile(r"[ \t]*\[(?:insert|your|add|date|name|recipient)[^\]]*\][ \t]*", re.IGNORECASE)


def remove_placeholders(text: str) -> str:
    """Drops "[Your Name]"-style blanks a small model sometimes leaves, and lines left empty by it."""
    lines = []
    for line in text.splitlines():
        cleaned = PLACEHOLDER_RE.sub(" ", line).rstrip()
        cleaned = re.sub(r"\s+([,.])", r"\1", cleaned)
        if cleaned.strip() or not line.strip():
            lines.append(re.sub(r"  +", " ", cleaned))
    return "\n".join(lines).strip()


def draft_prompt(report: MeetingReport, recipient: Recipient, summary: str, meeting_date: str = "") -> str:
    tone = ("a client or partner outside the company: polite, clear, no internal opinions"
            if recipient.audience == "external" else "a colleague: short and direct")
    held = f" held on {meeting_date[:10]}" if meeting_date else ""
    return f"""Write a follow-up email after the meeting "{report.title}"{held}.

To: {recipient.name}, {tone}.
Why they get this email: {recipient.reason}

What the meeting covered:
{summary}

Write a subject line and a plain-text body. Recap only what concerns this person: decisions,
their action items and dates, and anything they asked for. Use only facts from the summary, and
copy dates exactly as they appear there; never work out a date yourself. Address the reader as
"you". Leave no blanks or placeholders in square brackets. Do not add a signature with a name;
end with "Best regards,"."""


def suggest_followups(provider: LLMProvider, report: MeetingReport, attendees: list[str],
                      prefs: Prefs, contacts: dict[str, str] | None = None,
                      meeting_date: str = "") -> list[SuggestedEmail]:
    contacts = contacts or {}
    picked = provider.complete_json(SYSTEM, recipients_prompt(report, attendees, prefs), RecipientList)
    internal_notes = [a.text for a in report.anchors if a.kind == AnchorKind.INTERNAL]
    full_summary = summary_text(report, set(), meeting_date)
    client_summary = summary_text(report, internal_lines(report), meeting_date)

    suggestions: list[SuggestedEmail] = []
    seen: set[str] = set()
    for r in picked.recipients:
        if r.name.strip().lower() in seen or len(suggestions) >= MAX_SUGGESTIONS:
            continue
        seen.add(r.name.strip().lower())
        address = match_address(r.name, attendees, contacts)
        if address and prefs.my_email and address.lower() == prefs.my_email.lower():
            continue
        audience = r.audience
        if address and prefs.company_domain:
            audience = "internal" if prefs.is_internal(address) else "external"
        r = r.model_copy(update={"audience": audience})
        external = audience == "external"
        draft = provider.complete_json(SYSTEM, draft_prompt(report, r, client_summary if external else full_summary, meeting_date),
                                       EmailDraft)
        warning = ""
        if external and (note := leaked_note(draft.subject + " " + draft.body, internal_notes)):
            warning = f"This email may repeat your internal note “{note}”. Check it before sending."
        suggestions.append(SuggestedEmail(
            recipient=r.name, to=[address] if address else [], audience=audience, reason=r.reason,
            subject=remove_placeholders(draft.subject), body=remove_placeholders(draft.body), warning=warning,
        ))
    return suggestions
