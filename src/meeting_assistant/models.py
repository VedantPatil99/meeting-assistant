"""Core data structures shared by the pipeline, CLI and desktop app."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


def format_ts(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class Segment(BaseModel):
    start: float
    end: float | None = None
    speaker: str | None = None
    text: str

    def render(self) -> str:
        who = f"{self.speaker}: " if self.speaker else ""
        return f"[{format_ts(self.start)}] {who}{self.text}"


class Transcript(BaseModel):
    segments: list[Segment]

    def render(self) -> str:
        return "\n".join(seg.render() for seg in self.segments)


class AnchorKind(str, Enum):
    MUST = "must"            # must appear in the summary
    CONTEXT = "context"      # background that shapes interpretation, never quoted
    INTERNAL = "internal"    # must appear internally, never in client-facing output
    FEATURE = "feature"      # flagged feature request


class Anchor(BaseModel):
    id: int
    kind: AnchorKind
    text: str
    timestamp: float | None = None

    @property
    def must_include(self) -> bool:
        return self.kind in (AnchorKind.MUST, AnchorKind.INTERNAL, AnchorKind.FEATURE)


class Point(BaseModel):
    text: str
    timestamp: str | None = Field(None, description="HH:MM:SS from the transcript, if known")


class ActionItem(BaseModel):
    task: str
    owner: str | None = None
    due: str | None = None
    timestamp: str | None = None


class Summary(BaseModel):
    decisions: list[Point] = []
    action_items: list[ActionItem] = []
    open_questions: list[Point] = []
    key_points: list[Point] = []

    def lines(self) -> list[str]:
        out = [p.text for p in self.decisions + self.open_questions + self.key_points]
        out += [a.task for a in self.action_items]
        return out


class CoverageStatus(str, Enum):
    COVERED = "covered"          # model included it on its own
    REPAIRED = "repaired"        # missing at first, added by the repair pass
    FORCED = "forced"            # still missing, inserted verbatim as a fallback
    CONFLICT = "conflict"        # contradicts the transcript, left out for the user to resolve


class CoverageResult(BaseModel):
    anchor_id: int
    status: CoverageStatus
    evidence: str | None = None


class ConflictVerdict(str, Enum):
    CONSISTENT = "consistent"
    CONTRADICTS = "contradicts"
    NOT_MENTIONED = "not_mentioned"


class ConflictResult(BaseModel):
    anchor_id: int
    verdict: ConflictVerdict
    explanation: str = ""
    transcript_quote: str | None = None
    timestamp: str | None = None


class MeetingReport(BaseModel):
    title: str
    meeting_type: str
    anchors: list[Anchor]
    summary: Summary
    coverage: list[CoverageResult]
    conflicts: list[ConflictResult]

    @property
    def coverage_rate_before_repair(self) -> float:
        checked = [c for c in self.coverage if c.status != CoverageStatus.CONFLICT]
        if not checked:
            return 1.0
        return sum(c.status == CoverageStatus.COVERED for c in checked) / len(checked)
