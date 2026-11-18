"""Asking questions across meetings: indexing, date windows, search and checked answers."""

import os
import zlib
from datetime import date, datetime

import numpy as np

from meeting_assistant.ask import NOT_FOUND, Answer, Citation, MeetingIndex, ask, meeting_passages, past_range
from meeting_assistant.models import MeetingReport, Point, Summary
from meeting_assistant.session import MeetingSession
from meeting_assistant.textmatch import content_words
from meeting_assistant.transcript import parse_transcript

from .conftest import FakeProvider


class WordEmbedder:
    """Stands in for the embedding model: one dimension per word, so shared words mean similar."""

    model = "fake-embed"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        rows = np.zeros((len(texts), 512), dtype=np.float32)
        for i, text in enumerate(texts):
            for w in content_words(text.split(": ", 1)[-1]):
                rows[i, zlib.crc32(w.encode()) % 512] += 1
        return rows


PRICING = ("[00:00:10] Rahul: The licence cost is too high for the full rollout.\n"
           "[00:00:30] Vedant: I'll send a pricing breakdown by Friday.\n"
           "[00:01:00] Rahul: Go-live moves to 20 December.")
STANDUP = "[00:00:05] Deepa: Blocked on the Barclays FIX certification.\n[00:00:20] Kiran: Fixed the blotter freeze."


def meeting(base, title, when, transcript, report=None):
    s = MeetingSession.create(base, title, "client feedback", now=when)
    s.save_transcript(parse_transcript(transcript))
    if report:
        s.save_report(report)
    return s


def sessions(base):
    return [MeetingSession(p) for p in sorted(base.iterdir())]


def test_passages_cover_transcript_and_summary(tmp_path):
    report = MeetingReport(title="Pricing", meeting_type="client feedback", anchors=[], coverage=[], conflicts=[],
                           summary=Summary(decisions=[Point(text="Go-live on 20 December", timestamp="00:01:00")]))
    s = meeting(tmp_path, "Pricing", datetime(2026, 10, 14, 10), PRICING, report)
    passages = meeting_passages(s)
    assert passages[0].kind == "transcript" and passages[0].start == 10 and "licence cost" in passages[0].text
    decision = [p for p in passages if p.kind == "summary"][0]
    assert decision.text == "Decision: Go-live on 20 December" and decision.start == 60
    assert all(p.date == "2026-10-14" for p in passages)


def test_long_transcripts_split_into_overlapping_passages(tmp_path):
    lines = "\n".join(f"[00:{i // 60:02d}:{i % 60:02d}] Meera: point number {i} " + "y" * 60 for i in range(40))
    passages = meeting_passages(meeting(tmp_path, "Long", datetime(2026, 10, 1), lines))
    assert len(passages) > 3
    assert passages[0].text.splitlines()[-1] == passages[1].text.splitlines()[0]  # one line of overlap
    assert all(len(p.text) <= 700 for p in passages)


def test_only_new_or_changed_meetings_are_embedded(tmp_path):
    base = tmp_path / "meetings"
    a = meeting(base, "Pricing", datetime(2026, 10, 14), PRICING)
    meeting(base, "Standup", datetime(2026, 11, 3), STANDUP)
    index = MeetingIndex(tmp_path / "index.sqlite", WordEmbedder())
    assert index.update(sessions(base)) == 2
    assert index.update(sessions(base)) == 0
    a.transcript_file.write_text(PRICING + "\n[00:02:00] Rahul: One more thing.", encoding="utf-8")
    os.utime(a.transcript_file, ns=(1, 10**18))
    assert index.update(sessions(base)) == 1
    assert index.update(sessions(base)[1:]) == 0 and index.size() == 1  # a deleted meeting is dropped


def test_search_ranks_by_meaning_and_respects_dates(tmp_path):
    base = tmp_path / "meetings"
    meeting(base, "Pricing", datetime(2026, 10, 14), PRICING)
    meeting(base, "Standup", datetime(2026, 11, 3), STANDUP)
    index = MeetingIndex(tmp_path / "index.sqlite", WordEmbedder())
    index.update(sessions(base))
    assert index.search("Who is blocked on Barclays?")[0][1].title == "Standup"
    october = index.search("Who is blocked on Barclays?", since=date(2026, 10, 1), until=date(2026, 10, 31))
    assert {p.title for _, p in october} == {"Pricing"}


def test_date_phrases():
    today = date(2026, 11, 15)  # a Sunday
    assert past_range("what did Rahul say about pricing last month?", today) == (date(2026, 10, 1), date(2026, 10, 31))
    assert past_range("decisions last week", today) == (date(2026, 11, 2), date(2026, 11, 8))
    assert past_range("anything in October", today) == (date(2026, 10, 1), date(2026, 10, 31))
    assert past_range("anything in December", today) == (date(2025, 12, 1), date(2025, 12, 31))
    assert past_range("in the past two weeks", today) == (date(2026, 11, 1), today)
    assert past_range("who owns the pricing breakdown?", today) is None


def indexed(tmp_path):
    base = tmp_path / "meetings"
    meeting(base, "Pricing", datetime(2026, 10, 14), PRICING)
    index = MeetingIndex(tmp_path / "index.sqlite", WordEmbedder())
    index.update(sessions(base))
    return index


def test_answer_cites_a_checked_quote_with_its_moment(tmp_path):
    provider = FakeProvider({Answer: lambda p: Answer(
        citations=[Citation(passage=1, quote="The licence cost is too high for the full rollout.")],
        answer="In Pricing (2026-10-14) Rahul said the licence cost is too high.")})
    result = ask(provider, indexed(tmp_path), "What did Rahul say about the licence cost last month?",
                 today=date(2026, 11, 15))
    assert result.found and result.answer.startswith("In Pricing")
    assert result.sources[0].start == 10 and result.sources[0].title == "Pricing"
    assert "[1] Pricing (2026-10-14) at 00:00:10" in provider.calls[0][1]


def test_answers_without_a_real_quote_are_refused(tmp_path):
    made_up = FakeProvider({Answer: lambda p: Answer(
        citations=[Citation(passage=1, quote="Rahul agreed to a 30% discount on the licence."),
                   Citation(passage=9, quote="The licence cost is too high for the full rollout.")],
        answer="Rahul agreed to a 30% discount.")})
    result = ask(made_up, indexed(tmp_path), "Did Rahul agree a discount?")
    assert not result.found and result.answer == NOT_FOUND and result.sources == []


def test_no_meetings_in_the_window(tmp_path):
    provider = FakeProvider({})
    result = ask(provider, indexed(tmp_path), "What happened yesterday?", today=date(2026, 11, 15))
    assert not result.found and "No meetings to search" in result.answer and provider.calls == []
