"""Fill in task owners the model left blank, from the transcript line the task came from.

The evaluation showed the 7B model often leaves `owner` empty even when the line says who will
do it. Two cheap rules cover most of those cases, with no extra model call:
1. The speaker commits themselves ("I'll send the breakdown by Friday") -> the speaker.
2. The line names who will do it ("Ankit from our engineering team will investigate") -> that name.
An owner the model did give is never changed.
"""

from __future__ import annotations

import re

from ..models import Segment, Summary, Transcript
from ..scheduling import MONTHS, WEEKDAYS
from ..textmatch import similarity

SYSTEM_SPEAKER = "Others"  # session.SYSTEM_SPEAKER: everyone on the call track, so not one person
SOURCE_MATCH = 0.5  # share of the task's words that must appear in a line for it to be the source
SELF = re.compile(r"\b(I'll|I will|I can|I'm going to|I am going to|let me)\b", re.IGNORECASE)
NAMED = re.compile(r"\b([A-Z][a-z]+)\b[^.,;!?]{0,40}?\b(?:will|'ll|is going to|to take|takes|owns)\b")
NOT_NAMES = {"I", "We", "You", "They", "He", "She", "It", "The", "Our", "Your", "Their", "This", "That",
             "Then", "So", "And", "But", "Yes", "No", "Okay", "Ok", "Sure", "Great", "Good", "Thanks",
             "Someone", "Everyone", "Nobody", "Anyone", "Can", "Could", "Would", "Should", "Please", "If",
             "When", "Also", "Actually", "Agreed", "Perfect", "Fine", "Understood", "Noted", "Next",
             *(w.capitalize() for w in WEEKDAYS + MONTHS)}


def _seconds(ts: str | None) -> float | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", (ts or "").strip())
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def source_line(task: str, timestamp: str | None, segments: list[Segment]) -> Segment | None:
    """The line a task came from: the one at its timestamp if that line is about the task,
    otherwise the line sharing the most of its words."""
    at = _seconds(timestamp)
    candidates = [s for s in segments if at is not None and abs(s.start - at) < 1] or segments
    best = max(candidates, key=lambda s: similarity(task, s.text), default=None)
    if best is not None and similarity(task, best.text) >= SOURCE_MATCH:
        return best
    if candidates is not segments:  # the timestamp line wasn't about the task; search everything
        return source_line(task, None, segments)
    return None


def owner_from_line(line: Segment) -> str | None:
    if SELF.search(line.text) and line.speaker and line.speaker != SYSTEM_SPEAKER:
        return line.speaker
    for m in NAMED.finditer(line.text):
        if m[1] not in NOT_NAMES:
            return m[1]
    return None


def fill_owners(summary: Summary, transcript: Transcript) -> int:
    """Sets the owner of action items that have none. Returns how many were filled."""
    filled = 0
    for i, item in enumerate(summary.action_items):
        if item.owner:
            continue
        line = source_line(item.task, item.timestamp, transcript.segments)
        owner = owner_from_line(line) if line else None
        if owner:
            summary.action_items[i] = item.model_copy(update={"owner": owner})
            filled += 1
    return filled
