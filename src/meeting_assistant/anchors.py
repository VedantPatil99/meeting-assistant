"""Parse the notes a user types during a meeting into typed anchors.

One anchor per line, with an optional timestamp and a prefix:

    [00:12:30] ! Client agreed to move launch to 15 Dec   -> must include
    ctx: Rahul is the decision-maker, Priya advises        -> background only
    internal: client seems unhappy with pricing            -> internal only
    fr: they want bulk upload for trades                   -> feature request
    Plain lines without a prefix are treated as must-include.
"""

from __future__ import annotations

import re

from .models import Anchor, AnchorKind

TIMESTAMP_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*")

PREFIXES: list[tuple[str, AnchorKind]] = [
    ("internal:", AnchorKind.INTERNAL),
    ("ctx:", AnchorKind.CONTEXT),
    ("fr:", AnchorKind.FEATURE),
    ("!", AnchorKind.MUST),
]


def parse_timestamp(match: re.Match[str]) -> float:
    a, b, c = match.groups()
    if c is None:  # [MM:SS]
        return int(a) * 60 + int(b)
    return int(a) * 3600 + int(b) * 60 + int(c)


def parse_anchor_line(line: str, anchor_id: int) -> Anchor | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    timestamp = None
    if m := TIMESTAMP_RE.match(line):
        timestamp = parse_timestamp(m)
        line = line[m.end():]

    kind = AnchorKind.MUST
    lowered = line.lower()
    for prefix, prefix_kind in PREFIXES:
        if lowered.startswith(prefix):
            kind = prefix_kind
            line = line[len(prefix):]
            break

    text = line.strip()
    if not text:
        return None
    return Anchor(id=anchor_id, kind=kind, text=text, timestamp=timestamp)


def parse_anchors(text: str) -> list[Anchor]:
    anchors: list[Anchor] = []
    for line in text.splitlines():
        anchor = parse_anchor_line(line, len(anchors) + 1)
        if anchor:
            anchors.append(anchor)
    return anchors
