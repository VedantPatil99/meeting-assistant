"""Flag anchors that disagree with what was actually said in the meeting."""

from __future__ import annotations

from pydantic import BaseModel

from ..llm import LLMProvider
from ..models import Anchor, ConflictResult, ConflictVerdict, Transcript
from ..transcript import nearby_segments
from . import prompts


class ConflictCheck(BaseModel):
    # Field order matters: the model writes fields in order, so it states the facts
    # before committing to a verdict. With verdict first, a 7B model answered
    # "consistent" and then described the contradiction in its own explanation.
    note_says: str
    transcript_final: str
    transcript_quote: str | None = None
    timestamp: str | None = None
    explanation: str = ""
    verdict: ConflictVerdict


def detect_conflicts(
    provider: LLMProvider, anchors: list[Anchor], transcript: Transcript
) -> list[ConflictResult]:
    results: list[ConflictResult] = []
    for anchor in anchors:
        # ctx: notes are background the meeting rarely states outright; checking them
        # against loosely related lines produced false alarms on the sample meeting
        if not anchor.must_include:
            continue
        segments = nearby_segments(transcript, anchor.text, anchor.timestamp)
        evidence = "\n".join(seg.render() for seg in segments)
        check = provider.complete_json(
            prompts.SYSTEM, prompts.conflict_check(anchor.text, evidence), ConflictCheck
        )
        results.append(
            ConflictResult(
                anchor_id=anchor.id,
                verdict=check.verdict,
                explanation=check.explanation,
                transcript_quote=check.transcript_quote,
                timestamp=check.timestamp,
            )
        )
    return results
