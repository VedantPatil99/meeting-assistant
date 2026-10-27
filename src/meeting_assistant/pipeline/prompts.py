"""Prompt templates. Kept short and single-purpose so a 7B local model stays on task."""

from __future__ import annotations

from ..models import Anchor, AnchorKind

SYSTEM = (
    "You are a precise meeting analyst. Use only facts stated in the transcript or the user's "
    "notes. Never invent names, dates or numbers. Answer in the requested JSON format only."
)


def anchor_context(anchors: list[Anchor]) -> str:
    ctx = [a.text for a in anchors if a.kind == AnchorKind.CONTEXT]
    flagged = [a.text for a in anchors if a.must_include]
    parts = []
    if ctx:
        parts.append(
            "Background from the user (use it to interpret the meeting, do not quote it):\n"
            + "\n".join(f"- {t}" for t in ctx)
        )
    if flagged:
        parts.append(
            "Points the user flagged as important (include each one if it is discussed):\n"
            + "\n".join(f"- {t}" for t in flagged)
        )
    return "\n\n".join(parts)


def extract_chunk(meeting_type: str, anchors: list[Anchor], chunk_text: str, part: int, total: int) -> str:
    return f"""Meeting type: {meeting_type}
{anchor_context(anchors)}

Transcript part {part} of {total}:
{chunk_text}

Extract from THIS part only:
- decisions: things agreed or decided
- action_items: tasks with owner and due date if stated
- open_questions: unresolved questions
- key_points: other important discussion points
Copy the [HH:MM:SS] timestamp of the line each item comes from into "timestamp"."""


def merge_summaries(meeting_type: str, anchors: list[Anchor], partials_json: str) -> str:
    return f"""Meeting type: {meeting_type}
{anchor_context(anchors)}

Below are notes extracted from consecutive parts of one meeting, as JSON:
{partials_json}

Merge them into one summary. Remove duplicates, keep the latest version when an item changed
later in the meeting, and keep timestamps. Do not drop any decision or action item."""


def coverage_check(anchor_text: str, numbered_lines: str) -> str:
    return f"""User's note:
"{anchor_text}"

Summary lines:
{numbered_lines}

Does any summary line express the same information as the user's note (same meaning, not
necessarily the same words)? If yes, set covered=true and give that line's number.
If the note's key fact (names, dates, numbers, decision) is missing, set covered=false."""


def repair_point(anchor_text: str, evidence: str) -> str:
    return f"""The user flagged this point as important, but the meeting summary does not include it:
"{anchor_text}"

Relevant transcript lines:
{evidence or "(no matching lines found)"}

Write one summary item that captures the user's point, grounded in the transcript where possible.
Choose the section that fits best: decisions, action_items, open_questions or key_points."""


def conflict_check(anchor_text: str, evidence: str) -> str:
    return f"""User's note:
"{anchor_text}"

Relevant transcript lines:
{evidence or "(no matching lines found)"}

Fill in the fields in order:
1. note_says: the key facts in the note (names, dates, numbers, decisions).
2. transcript_final: what the transcript FINALLY settles on for the SAME topic as the note. If something
   was proposed and later changed, use the later, agreed version. Ignore lines about other topics;
   if none are about the note's topic, write "not discussed".
3. transcript_quote and timestamp: the line that states that final version.
4. explanation: one sentence comparing note_says with transcript_final.
5. verdict:
   - consistent: the note matches the final version
   - contradicts: the note states a different date, owner, amount or outcome than the final version
     (a note that matches only an earlier proposal, later changed, is a contradiction)
   - not_mentioned: the transcript does not discuss the note's topic
   A note that leaves out details, or talks about a different topic, is not a contradiction."""
