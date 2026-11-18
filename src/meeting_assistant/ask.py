"""Ask a question across all your meetings: "what did Rahul say about pricing last month?"

1. Index: every meeting's transcript is cut into short overlapping passages, and its summary
   points are added as passages too. Each passage is embedded (turned into a vector that
   represents its meaning) by a small local model and stored in SQLite next to the meetings.
   Only meetings that changed since the last run are embedded again.
2. Search: the question is embedded the same way. Passages are ranked by meaning (cosine
   similarity) plus a bonus for sharing the question's exact words, because names and terms
   like "CSV" matter and meaning-only search blurs them. "Last month", "in October" and similar
   phrases narrow the search to those dates, worked out in code like due dates are.
3. Answer: the local model gets the top passages and must quote the ones it uses before it
   writes the answer. Each quote is checked against the passage it cites; an answer with no
   quote that checks out is replaced by "not found", so it can't answer from thin air.
"""

from __future__ import annotations

import json
import re
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from pydantic import BaseModel, Field

from .llm import LLMProvider
from .models import MeetingReport, format_ts
from .scheduling import MONTHS, NUMBERS
from .session import MeetingSession
from .textmatch import content_words, similarity
from .transcript import parse_transcript

PASSAGE_CHARS = 600
TOP_K = 8
WORD_BONUS = 0.04       # added per question word found in the passage...
MAX_WORD_BONUS = 0.12   # ...up to this much
QUOTE_MATCH = 0.6       # share of a quote's words that must appear in the cited passage
DOC, QUERY = "search_document: ", "search_query: "  # the prefixes nomic-embed-text was trained with


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """One row per text."""
        ...


@dataclass
class Passage:
    folder: str
    title: str
    date: str               # YYYY-MM-DD
    start: float | None     # seconds into the meeting
    kind: str               # "transcript" or "summary"
    text: str

    def where(self) -> str:
        at = f" at {format_ts(self.start)}" if self.start is not None else ""
        return f"{self.title} ({self.date}){at}"


def meeting_passages(session: MeetingSession) -> list[Passage]:
    info = session.load_info()
    day = info.started_at[:10]
    out: list[Passage] = []
    if session.transcript_file.exists():
        segments = parse_transcript(session.transcript_file.read_text(encoding="utf-8")).segments
        i = 0
        while i < len(segments):
            lines, size, j = [], 0, i
            while j < len(segments) and (not lines or size + len(segments[j].render()) <= PASSAGE_CHARS):
                lines.append(segments[j].render())
                size += len(lines[-1]) + 1
                j += 1
            out.append(Passage(str(session.folder), info.title, day, segments[i].start, "transcript",
                               "\n".join(lines)))
            i = j - 1 if j - i > 1 else j  # overlap one line, so an answer split across two passages survives
            if j >= len(segments):
                break
    report_json = session.report_file.with_suffix(".json")
    if report_json.exists():
        report = MeetingReport.model_validate_json(report_json.read_text(encoding="utf-8"))
        s = report.summary
        points = [("Decision", p.text, p.timestamp) for p in s.decisions]
        points += [("Action item", a.task + (f" (owner: {a.owner})" if a.owner else "")
                    + (f" (due: {a.due})" if a.due else ""), a.timestamp) for a in s.action_items]
        points += [("Open question", p.text, p.timestamp) for p in s.open_questions]
        points += [("Point", p.text, p.timestamp) for p in s.key_points]
        points += [("Point", p.text, p.timestamp) for ps in s.sections.values() for p in ps]
        points += [("Feature request", f.title + (f": {f.detail}" if f.detail else ""), f.timestamp)
                   for f in report.feature_requests]
        for label, text, ts in points:
            out.append(Passage(str(session.folder), info.title, day, _ts_seconds(ts), "summary", f"{label}: {text}"))
    return out


def _ts_seconds(ts: str | None) -> float | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", (ts or "").strip())
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def _stamp(session: MeetingSession) -> str:
    files = [session.transcript_file, session.report_file.with_suffix(".json"), session.folder / "meeting.json"]
    return json.dumps([f.stat().st_mtime_ns if f.exists() else 0 for f in files])


class MeetingIndex:
    """The passages of every meeting and their vectors, in one SQLite file."""

    def __init__(self, path: Path, embedder: Embedder):
        self.embedder = embedder
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meetings (folder TEXT PRIMARY KEY, stamp TEXT, model TEXT);
            CREATE TABLE IF NOT EXISTS passages (folder TEXT, title TEXT, date TEXT, start REAL, kind TEXT,
                                                 text TEXT, vector BLOB);
            CREATE INDEX IF NOT EXISTS passages_folder ON passages (folder);
        """)

    def update(self, sessions: list[MeetingSession],
               report: Callable[[int, int], None] = lambda done, total: None) -> int:
        """Embed meetings that are new or changed and drop deleted ones. Returns how many were embedded."""
        known = dict(self.db.execute("SELECT folder, stamp || '|' || model FROM meetings"))
        current = {str(s.folder): s for s in sessions}
        for gone in set(known) - set(current):
            self._forget(gone)
        todo = [s for f, s in current.items() if known.get(f) != f"{_stamp(s)}|{self.embedder.model}"]
        for n, session in enumerate(todo, start=1):
            folder = str(session.folder)
            passages = meeting_passages(session)
            vectors = self._embed([DOC + p.text for p in passages]) if passages else []
            self._forget(folder)
            self.db.executemany(
                "INSERT INTO passages VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(p.folder, p.title, p.date, p.start, p.kind, p.text, v.astype(np.float32).tobytes())
                 for p, v in zip(passages, vectors)])
            self.db.execute("INSERT INTO meetings VALUES (?, ?, ?)", (folder, _stamp(session), self.embedder.model))
            self.db.commit()
            report(n, len(todo))
        return len(todo)

    def _forget(self, folder: str) -> None:
        self.db.execute("DELETE FROM passages WHERE folder = ?", (folder,))
        self.db.execute("DELETE FROM meetings WHERE folder = ?", (folder,))
        self.db.commit()

    def _embed(self, texts: list[str]) -> np.ndarray:
        rows = np.vstack([self.embedder.embed(texts[i:i + 32]) for i in range(0, len(texts), 32)])
        return rows / np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-9)

    def size(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]

    def search(self, question: str, k: int = TOP_K, since: date | None = None,
               until: date | None = None) -> list[tuple[float, Passage]]:
        sql, args = "SELECT folder, title, date, start, kind, text, vector FROM passages", []
        if since and until:
            sql, args = sql + " WHERE date BETWEEN ? AND ?", [since.isoformat(), until.isoformat()]
        rows = self.db.execute(sql, args).fetchall()
        if not rows:
            return []
        query = self._embed([QUERY + question])[0]
        vectors = np.vstack([np.frombuffer(r[6], dtype=np.float32) for r in rows])
        scores = vectors @ query
        words = set(content_words(question))
        ranked = []
        for row, score in zip(rows, scores):
            shared = len(words & set(content_words(row[5])))
            ranked.append((float(score) + min(shared * WORD_BONUS, MAX_WORD_BONUS), Passage(*row[:6])))
        ranked.sort(key=lambda x: -x[0])
        return ranked[:k]


def past_range(question: str, today: date) -> tuple[date, date] | None:
    """The dates a question is about, from phrases like "last month" or "in October"."""
    q = question.lower()
    if re.search(r"\byesterday\b", q):
        return today - timedelta(days=1), today - timedelta(days=1)
    if re.search(r"\btoday\b", q):
        return today, today
    if re.search(r"\bthis week\b", q):
        return today - timedelta(days=today.weekday()), today
    if re.search(r"\blast week\b", q):
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if re.search(r"\bthis month\b", q):
        return today.replace(day=1), today
    if re.search(r"\blast month\b", q):
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if m := re.search(r"\b(?:last|past) (\d+|[a-z]+) (day|week|month)s?\b", q):
        n = int(m[1]) if m[1].isdigit() else NUMBERS.get(m[1])
        if n:
            days = {"day": 1, "week": 7, "month": 30}[m[2]] * n
            return today - timedelta(days=days), today
    if m := re.search(r"\b(?:in|during|from) (" + "|".join(MONTHS) + r")\b", q):
        month = MONTHS.index(m[1]) + 1
        year = today.year if month <= today.month else today.year - 1  # "in October" means the last one
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])
    return None


class Citation(BaseModel):
    passage: int = Field(description="Number of the passage the quote comes from")
    quote: str = Field(description="The sentence from that passage that supports the answer, copied exactly")


class Answer(BaseModel):
    # quotes first: the model finds its evidence before it writes the answer
    citations: list[Citation] = Field(description="Every passage used, with a supporting quote; empty if none answer it")
    answer: str = Field(description="1 to 3 sentences answering the question, naming the meeting and date")


@dataclass
class AskResult:
    answer: str
    found: bool
    sources: list[Passage]


NOT_FOUND = "I couldn't find that in your meetings."


def answer_prompt(question: str, hits: list[Passage]) -> str:
    listed = "\n\n".join(f"[{i}] {p.where()} ({'summary' if p.kind == 'summary' else 'transcript'}):\n{p.text}"
                         for i, p in enumerate(hits, start=1))
    return f"""Question: {question}

Passages from the user's past meetings:
{listed}

Answer the question using only these passages.
1. citations: for each passage that helps answer it, give its number and copy the exact sentence
   that supports the answer. Leave it empty if no passage answers the question.
2. answer: 1 to 3 sentences, naming the meeting and its date. If the citations are empty, say
   the meetings don't cover it. Do not guess."""


def _line_start(passage: Passage, quote: str) -> float | None:
    """The time of the transcript line a quote comes from, so the source opens at that moment."""
    if passage.kind != "transcript":
        return passage.start
    best, best_score = passage.start, 0.0
    for line in passage.text.splitlines():
        m = re.match(r"\[(\d{2}:\d{2}:\d{2})\]", line)
        score = similarity(quote, line)
        if m and score > best_score:
            best, best_score = _ts_seconds(m[1]), score
    return best


def ask(provider: LLMProvider, index: MeetingIndex, question: str, today: date | None = None) -> AskResult:
    window = past_range(question, today or date.today())
    hits = [p for _, p in index.search(question, since=window[0] if window else None,
                                       until=window[1] if window else None)]
    if not hits:
        when = f" between {window[0]:%d %b} and {window[1]:%d %b %Y}" if window else ""
        return AskResult(f"No meetings to search{when}.", False, [])
    result = provider.complete_json(
        "You answer questions about the user's meetings using only the passages given. Never invent "
        "names, dates or numbers. Answer in the requested JSON format only.",
        answer_prompt(question, hits), Answer)
    sources: list[Passage] = []
    for c in result.citations:
        if not 1 <= c.passage <= len(hits):
            continue
        passage = hits[c.passage - 1]
        if similarity(c.quote, passage.text) < QUOTE_MATCH:
            continue  # the quote isn't in the passage it cites
        source = Passage(passage.folder, passage.title, passage.date, _line_start(passage, c.quote),
                         passage.kind, c.quote)
        if all((s.folder, s.start) != (source.folder, source.start) for s in sources):
            sources.append(source)
    if not sources:
        return AskResult(NOT_FOUND, False, [])
    return AskResult(result.answer, True, sources)
