"""Map-reduce summarization: extract per chunk, then merge into one structured summary."""

from __future__ import annotations

import json

from ..llm import LLMProvider
from ..models import Anchor, Summary, Transcript
from ..transcript import chunk_segments
from . import prompts
from .templates import section_instructions, summary_schema, template_for, to_summary


def summarize(
    provider: LLMProvider,
    transcript: Transcript,
    anchors: list[Anchor],
    meeting_type: str,
    max_chunk_chars: int = 6000,
    extract_provider: LLMProvider | None = None,
) -> Summary:
    """`extract_provider` (default: `provider`) reads each chunk; `provider` merges the parts.
    The meeting type's template, if any, adds its own sections to what the model fills in."""
    template = template_for(meeting_type)
    schema, extra = summary_schema(template), section_instructions(template)
    chunks = chunk_segments(transcript, max_chunk_chars)
    partials: list[Summary] = []
    for i, chunk in enumerate(chunks, start=1):
        chunk_text = "\n".join(seg.render() for seg in chunk)
        prompt = prompts.extract_chunk(meeting_type, anchors, chunk_text, i, len(chunks)) + extra
        partials.append((extract_provider or provider).complete_json(prompts.SYSTEM, prompt, schema))

    if len(partials) == 1:
        return to_summary(partials[0], template)

    partials_json = json.dumps([p.model_dump(exclude_none=True, exclude={"sections"}) for p in partials],
                               indent=1)
    prompt = prompts.merge_summaries(meeting_type, anchors, partials_json) + extra
    return to_summary(provider.complete_json(prompts.SYSTEM, prompt, schema), template)
