"""Run the full pipeline for one meeting."""

from __future__ import annotations

from typing import Callable

from ..llm import LLMProvider
from ..models import Anchor, ConflictVerdict, MeetingReport, Transcript
from .conflicts import detect_conflicts
from .coverage import correct_contradicted, ensure_coverage
from .features import find_feature_requests
from .owners import fill_owners
from .summarize import summarize


def process_meeting(
    provider: LLMProvider,
    transcript: Transcript,
    anchors: list[Anchor],
    title: str = "Meeting",
    meeting_type: str = "general",
    on_progress: Callable[[str], None] = lambda msg: None,
    extract_provider: LLMProvider | None = None,
) -> MeetingReport:
    """`extract_provider`, if given, does the bulk per-chunk reading (summary extraction and
    feature-request finding). `provider` does the merge and every check that guards quality:
    conflicts, coverage and repair."""
    reader = extract_provider or provider
    on_progress("Summarizing transcript")
    summary = summarize(provider, transcript, anchors, meeting_type, extract_provider=reader)

    # conflicts first, so a note the meeting contradicts is never forced into the summary
    on_progress("Checking your notes against the transcript")
    conflicts = detect_conflicts(provider, anchors, transcript)
    contradicted = frozenset(
        c.anchor_id for c in conflicts if c.verdict == ConflictVerdict.CONTRADICTS
    )
    # the summary may already repeat a contradicted note as fact; the meeting's version replaces it
    correct_contradicted(provider, summary, anchors, conflicts)

    on_progress("Checking your flagged points are covered")
    coverage = ensure_coverage(provider, summary, anchors, transcript, contradicted)
    fill_owners(summary, transcript)  # after repairs, which can add action items too

    on_progress("Looking for feature requests")
    feature_requests = find_feature_requests(reader, transcript, anchors)

    on_progress("Done")
    return MeetingReport(
        title=title,
        meeting_type=meeting_type,
        anchors=anchors,
        summary=summary,
        coverage=coverage,
        conflicts=conflicts,
        feature_requests=feature_requests,
    )
