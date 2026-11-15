"""Find feature requests in a meeting, and match them to requests already in the tracker.

Finding: each transcript chunk is searched separately (the same chunks the summary uses), with
the model quoting the line before it names the request, so every request is tied to something
actually said. Requests found twice are merged in code, and every `fr:` note is guaranteed a
place: a flagged request the model missed is added from the note.

Matching: word overlap decides the clear cases for free. Only a possible match goes to the
model, which compares the new request with at most three tracker entries.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from ..llm import LLMProvider
from ..models import AnchorKind, FeatureRequest, Segment, Transcript, Anchor, format_ts
from ..textmatch import same_words, similarity
from ..transcript import chunk_segments
from .prompts import SYSTEM

SAME_IN_MEETING = 0.6   # two requests from one meeting this similar are one request
NOTE_FOUND = 0.5        # an fr: note this similar to a found request is that request
SURE_MATCH = 0.8        # this many words shared both ways with a tracker entry: same request, no model needed
MAYBE_MATCH = 0.25      # below this, not worth asking the model
MAX_CANDIDATES = 3


class FoundRequest(BaseModel):
    # quote first: the model points at the evidence before it names the request
    quote: str = Field(description="The transcript line where the request is made, copied exactly")
    timestamp: str | None = Field(None, description="HH:MM:SS of that line")
    requested_by: str | None = Field(None, description="Who asked, as named in the transcript")
    title: str = Field(description="3 to 8 words naming the capability, e.g. 'Export reports to Excel'")
    detail: str = Field("", description="One sentence: what they want it for")


class FoundRequests(BaseModel):
    requests: list[FoundRequest]


class MatchChoice(BaseModel):
    reasoning: str = Field(description="Compare what the new request asks for with each candidate")
    match: int = Field(description="Number of the candidate that is the same request, or 0 for none")


def find_prompt(chunk_text: str, flagged: list[str], part: int, total: int) -> str:
    notes = "\n".join(f"- {t}" for t in flagged) or "- none"
    return f"""Transcript part {part} of {total}:
{chunk_text}

The user flagged these as feature requests during the meeting:
{notes}

List the product feature requests made in THIS part: something a client or user asks the
product to do that it does not do yet, such as a new report, export, integration, alert or
setting. Do not include bug reports, slowness or performance complaints, pricing, dates,
or tasks people agreed to do. Return an empty list if there are none."""


def match_prompt(request: FeatureRequest, candidates: list[tuple[int, str, str]]) -> str:
    listed = "\n".join(f"{i}. {title}" + (f": {detail}" if detail else "")
                       for i, (_, title, detail) in enumerate(candidates, start=1))
    return f"""A client asked for this in a meeting:
"{request.title}" {f'({request.detail})' if request.detail else ''}

Feature requests already being tracked:
{listed}

Is the new request the same need as one of the tracked requests? Count it as the same when the
core capability is the same, even if the wording differs or one of them asks for extra options
or more detail (for example "trade failure alerts by email" and "alerts when a trade fails, by
email or Teams" are the same). Count it as different when the core capability differs (for
example exporting to PDF versus exporting to Excel, or alerts about a different event).
Answer with its number, or 0 if none is the same."""


def _merge_into(kept: FeatureRequest, other: FeatureRequest) -> FeatureRequest:
    return kept.model_copy(update={
        "detail": kept.detail or other.detail,
        "requested_by": kept.requested_by or other.requested_by,
        "quote": kept.quote or other.quote,
        "timestamp": kept.timestamp or other.timestamp,
        "flagged": kept.flagged or other.flagged,
    })


def dedupe(requests: list[FeatureRequest]) -> list[FeatureRequest]:
    out: list[FeatureRequest] = []
    for r in requests:
        for i, kept in enumerate(out):
            if similarity(r.title, kept.title) >= SAME_IN_MEETING:
                out[i] = _merge_into(kept, r)
                break
        else:
            out.append(r)
    return out


def _source_line(request: FeatureRequest, segments: list[Segment]) -> Segment | None:
    """The transcript line the request came from. The model often shortens its quote, so the
    line is found by word overlap rather than exact text."""
    best = max(segments, key=lambda s: similarity(request.quote or request.title, s.text), default=None)
    return best if best and similarity(request.quote or request.title, best.text) >= NOTE_FOUND else None


def _fill_from_transcript(request: FeatureRequest, segments: list[Segment]) -> FeatureRequest:
    if request.requested_by and request.timestamp:
        return request
    line = _source_line(request, segments)
    if line is None:
        return request
    return request.model_copy(update={"requested_by": request.requested_by or line.speaker,
                                      "timestamp": request.timestamp or format_ts(line.start)})


def find_feature_requests(provider: LLMProvider, transcript: Transcript, anchors: list[Anchor],
                          max_chunk_chars: int = 6000) -> list[FeatureRequest]:
    flagged_notes = [a for a in anchors if a.kind == AnchorKind.FEATURE]
    flagged = [a.text for a in flagged_notes]
    chunks = chunk_segments(transcript, max_chunk_chars)
    found: list[FeatureRequest] = []
    for i, chunk in enumerate(chunks, start=1):
        text = "\n".join(seg.render() for seg in chunk)
        answer = provider.complete_json(SYSTEM, find_prompt(text, flagged, i, len(chunks)), FoundRequests)
        found += [FeatureRequest(**r.model_dump()) for r in answer.requests if r.title.strip()]
    requests = dedupe(found)

    for note in flagged_notes:
        best = max(requests, key=lambda r: similarity(note.text, f"{r.title} {r.detail}"), default=None)
        if best and similarity(note.text, f"{best.title} {best.detail}") >= NOTE_FOUND:
            requests[requests.index(best)] = best.model_copy(update={"flagged": True})
        else:
            requests.append(FeatureRequest(title=note.text[:1].upper() + note.text[1:], flagged=True,
                                           timestamp=format_ts(note.timestamp) if note.timestamp is not None else None))
    return [_fill_from_transcript(r, transcript.segments) for r in requests]


def link_to_tracker(provider: LLMProvider, requests: list[FeatureRequest],
                    tracked: list[tuple[int, str, str]],
                    on_progress: Callable[[str], None] = lambda msg: None) -> list[int | None]:
    """For each request, the id of the tracked feature it repeats, or None if it's new.
    `tracked` is (id, title, detail) for every feature in the tracker."""
    links: list[int | None] = []
    for request in requests:
        text = f"{request.title} {request.detail}"
        scored = sorted(((max(similarity(request.title, title), similarity(text, f"{title} {detail}")), fid,
                          title, detail) for fid, title, detail in tracked), reverse=True)
        sure = [fid for _, fid, title, _ in scored if same_words(request.title, title) >= SURE_MATCH]
        if sure:
            links.append(sure[0])
            continue
        candidates = [(fid, title, detail) for score, fid, title, detail in scored[:MAX_CANDIDATES]
                      if score >= MAYBE_MATCH]
        if not candidates:
            links.append(None)
            continue
        on_progress(f"Comparing “{request.title}” with the tracker")
        choice = provider.complete_json(SYSTEM, match_prompt(request, candidates), MatchChoice)
        links.append(candidates[choice.match - 1][0] if 1 <= choice.match <= len(candidates) else None)
    return links
