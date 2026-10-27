"""Map-reduce summarization: extract per chunk, then merge into one structured summary."""

from __future__ import annotations

import json

from ..llm import LLMProvider
from ..models import Anchor, Summary, Transcript
from ..transcript import chunk_segments
from . import prompts


def summarize(
    provider: LLMProvider,
    transcript: Transcript,
    anchors: list[Anchor],
    meeting_type: str,
    max_chunk_chars: int = 6000,
) -> Summary:
    chunks = chunk_segments(transcript, max_chunk_chars)
    partials: list[Summary] = []
    for i, chunk in enumerate(chunks, start=1):
        chunk_text = "\n".join(seg.render() for seg in chunk)
        prompt = prompts.extract_chunk(meeting_type, anchors, chunk_text, i, len(chunks))
        partials.append(provider.complete_json(prompts.SYSTEM, prompt, Summary))

    if len(partials) == 1:
        return partials[0]

    partials_json = json.dumps([p.model_dump(exclude_none=True) for p in partials], indent=1)
    prompt = prompts.merge_summaries(meeting_type, anchors, partials_json)
    return provider.complete_json(prompts.SYSTEM, prompt, Summary)
