"""Cheap word-overlap comparison for short texts: no model, no embeddings, same answer every time.

Used to find duplicate feature requests and to spot internal notes repeated in an email.
"""

from __future__ import annotations

import re

STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "have", "will", "they", "their", "about",
             "when", "would", "could", "should", "like", "want", "need", "able", "into", "some", "more"}


def _word(token: str) -> str | None:
    """The comparable form of one token, or None to skip it. Short words are mostly filler
    ("to", "as", "via"), but short all-caps terms carry the meaning: "Export to CSV" and
    "Export to PDF" are different requests."""
    acronym = token[:-1] if token.endswith("s") and token[:-1].isupper() else token  # "APIs" -> "API"
    if len(acronym) >= 2 and acronym.isupper():
        return acronym.lower()
    word = token.lower()
    if len(word) <= 3 or word in STOPWORDS:
        return None
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def content_words(text: str) -> list[str]:
    """Lower-case words longer than 3 letters plus short all-caps terms (CSV, SSO, Q4), minus
    common words, with a plural "s" removed."""
    return [w for w in map(_word, re.findall(r"[A-Za-z0-9']+", text)) if w]


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
