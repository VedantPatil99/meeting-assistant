"""Fixes for the two weak spots the evaluation found: a contradicted note repeated in the summary
as fact, and task owners left blank."""

from meeting_assistant.anchors import parse_anchors
from meeting_assistant.models import ActionItem, ConflictResult, ConflictVerdict, Point, Summary
from meeting_assistant.pipeline.coverage import CoverageCheck, correct_contradicted
from meeting_assistant.pipeline.owners import fill_owners, owner_from_line
from meeting_assistant.models import Segment
from meeting_assistant.transcript import parse_transcript

from .conftest import FakeProvider

NOTE = parse_anchors("! Outage lasted 40 minutes")


def conflict(final="The outage lasted 55 minutes, restored at 10:00"):
    return [ConflictResult(anchor_id=1, verdict=ConflictVerdict.CONTRADICTS, timestamp="00:00:27",
                           transcript_final=final)]


def summary():
    return Summary(key_points=[Point(text="Prices froze at 9:05"),
                               Point(text="The outage lasted 40 minutes, not 55 as initially stated")])


def test_a_line_repeating_the_contradicted_note_is_replaced_by_the_meetings_version():
    s = summary()
    provider = FakeProvider({CoverageCheck: lambda p: CoverageCheck(covered=True, line_number=2)})
    correct_contradicted(provider, s, NOTE, conflict())
    assert [p.text for p in s.key_points] == ["Prices froze at 9:05", "The outage lasted 55 minutes, restored at 10:00"]
    assert s.key_points[1].timestamp == "00:00:27"


def test_the_meetings_version_is_added_when_missing():
    s = Summary(key_points=[Point(text="Prices froze at 9:05")])
    provider = FakeProvider({CoverageCheck: lambda p: CoverageCheck(covered=False)})
    correct_contradicted(provider, s, NOTE, conflict())
    assert s.key_points[-1].text == "The outage lasted 55 minutes, restored at 10:00"
    assert len(provider.calls) == 2  # the note, then the meeting's version


def test_nothing_changes_for_consistent_notes_or_without_a_final_version():
    s = summary()
    provider = FakeProvider({})
    consistent = [ConflictResult(anchor_id=1, verdict=ConflictVerdict.CONSISTENT, transcript_final="x")]
    correct_contradicted(provider, s, NOTE, consistent + conflict(final=None))
    assert s == summary() and provider.calls == []


def test_a_wrong_owner_is_dropped_when_an_action_item_is_corrected():
    s = Summary(action_items=[ActionItem(task="Arjun takes the settlement-date bug", owner="Arjun")])
    provider = FakeProvider({CoverageCheck: lambda p: CoverageCheck(covered=True, line_number=1)})
    correct_contradicted(provider, s, parse_anchors("! Arjun takes the settlement-date bug"),
                         conflict(final="Meera will take the settlement-date bug herself"))
    assert s.action_items[0].task == "Meera will take the settlement-date bug herself"
    assert s.action_items[0].owner is None  # fill_owners finds Meera from the transcript later


TRANSCRIPT = parse_transcript(
    "[00:03:02] Vedant: Understood. I'll send over the licence breakdown by Friday.\n"
    "[00:03:20] Vedant: Yes, Ankit from our engineering team will investigate and report back next Wednesday.\n"
    "[00:03:35] Others: We'll circulate the minutes.\n"
    "[00:03:48] Priya: Monday is busy for everyone.")


def test_owners_come_from_the_source_line():
    s = Summary(action_items=[
        ActionItem(task="Send the licence breakdown", timestamp="00:03:02"),
        ActionItem(task="Investigate the Monday performance issue and report back"),
        ActionItem(task="Circulate the minutes"),
        ActionItem(task="Review pricing", owner="Rahul"),
    ])
    assert fill_owners(s, TRANSCRIPT) == 2
    assert [a.owner for a in s.action_items] == ["Vedant", "Ankit", None, "Rahul"]


def test_names_are_not_confused_with_ordinary_words():
    assert owner_from_line(Segment(start=0, speaker="Priya", text="Monday is busy, so nobody will join.")) is None
    assert owner_from_line(Segment(start=0, speaker="Meera", text="Fine, I'll take the bug myself.")) == "Meera"
    assert owner_from_line(Segment(start=0, speaker="Kiran", text="Leila will update the runbook.")) == "Leila"
