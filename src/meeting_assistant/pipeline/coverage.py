"""Guarantee that every must-include anchor ends up in the summary.

1. Check each anchor against the summary lines.
2. Missing ones get a repair pass grounded in nearby transcript lines.
3. Anything still missing is inserted verbatim and marked as a user note.

Anchors the transcript contradicts skip all three steps and are reported as conflicts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..llm import LLMProvider
from ..models import (
    ActionItem,
    Anchor,
    CoverageResult,
    CoverageStatus,
    Point,
    Summary,
    Transcript,
)
from ..transcript import nearby_segments
from . import prompts


class CoverageCheck(BaseModel):
    covered: bool
    line_number: int | None = None


class RepairedPoint(BaseModel):
    section: Literal["decisions", "action_items", "open_questions", "key_points"]
    text: str
    owner: str | None = None
    due: str | None = None
    timestamp: str | None = None


def check_anchor(provider: LLMProvider, anchor: Anchor, summary: Summary) -> str | None:
    """Return the summary line that covers the anchor, or None."""
    lines = summary.lines()
    if not lines:
        return None
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))
    result = provider.complete_json(
        prompts.SYSTEM, prompts.coverage_check(anchor.text, numbered), CoverageCheck
    )
    if not result.covered:
        return None
    if result.line_number and 1 <= result.line_number <= len(lines):
        return lines[result.line_number - 1]
    return "(covered; line not identified)"


def add_point(summary: Summary, point: RepairedPoint) -> None:
    if point.section == "action_items":
        summary.action_items.append(
            ActionItem(task=point.text, owner=point.owner, due=point.due, timestamp=point.timestamp)
        )
    else:
        getattr(summary, point.section).append(Point(text=point.text, timestamp=point.timestamp))


def ensure_coverage(
    provider: LLMProvider,
    summary: Summary,
    anchors: list[Anchor],
    transcript: Transcript,
    contradicted: frozenset[int] = frozenset(),
) -> list[CoverageResult]:
    results: list[CoverageResult] = []
    for anchor in anchors:
        if not anchor.must_include:
            continue
        if anchor.id in contradicted:
            # never force a note into the summary that the meeting itself contradicts
            results.append(CoverageResult(anchor_id=anchor.id, status=CoverageStatus.CONFLICT))
            continue

        evidence = check_anchor(provider, anchor, summary)
        if evidence:
            results.append(CoverageResult(anchor_id=anchor.id, status=CoverageStatus.COVERED, evidence=evidence))
            continue

        segments = nearby_segments(transcript, anchor.text, anchor.timestamp)
        context = "\n".join(seg.render() for seg in segments)
        repaired = provider.complete_json(
            prompts.SYSTEM, prompts.repair_point(anchor.text, context), RepairedPoint
        )
        add_point(summary, repaired)

        evidence = check_anchor(provider, anchor, summary)
        if evidence:
            results.append(CoverageResult(anchor_id=anchor.id, status=CoverageStatus.REPAIRED, evidence=evidence))
            continue

        forced = f"[User note] {anchor.text}"
        summary.key_points.append(Point(text=forced))
        results.append(CoverageResult(anchor_id=anchor.id, status=CoverageStatus.FORCED, evidence=forced))
    return results
