"""Run the full pipeline for one meeting."""

from __future__ import annotations

from typing import Callable

from ..llm import LLMProvider
from ..models import Anchor, ConflictVerdict, MeetingReport, Transcript
from .conflicts import detect_conflicts
from .coverage import ensure_coverage
from .summarize import summarize


def process_meeting(
    provider: LLMProvider,
    transcript: Transcript,
    anchors: list[Anchor],
    title: str = "Meeting",
    meeting_type: str = "general",
    on_progress: Callable[[str], None] = lambda msg: None,
) -> MeetingReport:
    on_progress("Summarizing transcript")
    summary = summarize(provider, transcript, anchors, meeting_type)

    # conflicts first, so a note the meeting contradicts is never forced into the summary
    on_progress("Checking your notes against the transcript")
    conflicts = detect_conflicts(provider, anchors, transcript)
    contradicted = frozenset(
        c.anchor_id for c in conflicts if c.verdict == ConflictVerdict.CONTRADICTS
    )

    on_progress("Checking your flagged points are covered")
    coverage = ensure_coverage(provider, summary, anchors, transcript, contradicted)

    on_progress("Done")
    return MeetingReport(
        title=title,
        meeting_type=meeting_type,
        anchors=anchors,
        summary=summary,
        coverage=coverage,
        conflicts=conflicts,
    )
