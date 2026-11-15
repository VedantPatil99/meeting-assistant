"""Cheap word-overlap comparison for short texts: no model, no embeddings, same answer every time.

Used to find duplicate feature requests and to spot internal notes repeated in an email.
"""

from __future__ import annotations

import re

STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "have", "will", "they", "their", "about",
             "when", "would", "could", "should", "like", "want", "need", "able", "into", "some", "more"}


def content_words(text: str) -> list[str]:
    """Lower-case words longer than 3 letters, minus common words, with a plural "s" removed."""
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 3 and w not in STOPWORDS]
    return [w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in words]


def similarity(a: str, b: str) -> float:
    """Share of the shorter text's content words that also appear in the other, 0 to 1.
    "Export reports to Excel" vs "export the report straight to Excel" -> 1.0."""
    wa, wb = set(content_words(a)), set(content_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def same_words(a: str, b: str) -> float:
    """Like `similarity`, but against the longer text, so one text being contained in the other
    isn't enough: "Export reports as PDF" vs "Export reports to Excel" -> 0.67, not 1.0."""
    wa, wb = set(content_words(a)), set(content_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))
