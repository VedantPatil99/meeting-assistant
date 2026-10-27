from meeting_assistant.anchors import parse_anchors
from meeting_assistant.models import ConflictVerdict, CoverageStatus, Point, Summary
from meeting_assistant.pipeline import process_meeting
from meeting_assistant.pipeline.conflicts import ConflictCheck
from meeting_assistant.pipeline.coverage import CoverageCheck, RepairedPoint
from meeting_assistant.report import render_markdown
from meeting_assistant.transcript import parse_transcript

from .conftest import FakeProvider

TRANSCRIPT = parse_transcript(
    "[00:00:10] Rahul: Monday loads are slow.\n"
    "[00:01:00] Rahul: Let's go live on 20 December."
)


def base_answers(coverage):
    return {
        Summary: lambda p: Summary(decisions=[Point(text="Go-live on 20 December", timestamp="00:01:00")]),
        CoverageCheck: coverage,
        RepairedPoint: lambda p: RepairedPoint(section="key_points", text="Monday loads are slow"),
        ConflictCheck: lambda p: ConflictCheck(
            note_says="-", transcript_final="-", verdict=ConflictVerdict.CONSISTENT
        ),
    }


def contradiction(p):
    return ConflictCheck(
        note_says="Go-live on 15 December",
        transcript_final="Go-live on 20 December",
        explanation="Transcript says 20 December",
        transcript_quote="Let's go live on 20 December.",
        timestamp="00:01:00",
        verdict=ConflictVerdict.CONTRADICTS,
    )


def test_covered_anchor_is_not_repaired():
    provider = FakeProvider(base_answers(lambda p: CoverageCheck(covered=True, line_number=1)))
    report = process_meeting(provider, TRANSCRIPT, parse_anchors("! go live 20 Dec"))
    assert report.coverage[0].status == CoverageStatus.COVERED
    assert report.coverage[0].evidence == "Go-live on 20 December"
    assert not any(name == "RepairedPoint" for name, _ in provider.calls)


def test_missing_anchor_is_repaired_then_rechecked():
    # first check says missing, recheck after repair says covered on the new line
    verdicts = iter([CoverageCheck(covered=False), CoverageCheck(covered=True, line_number=2)])
    provider = FakeProvider(base_answers(lambda p: next(verdicts)))
    report = process_meeting(provider, TRANSCRIPT, parse_anchors("! Monday slowness"))
    assert report.coverage[0].status == CoverageStatus.REPAIRED
    assert "Monday loads are slow" in report.summary.lines()


def test_anchor_still_missing_is_forced_in_verbatim():
    provider = FakeProvider(base_answers(lambda p: CoverageCheck(covered=False)))
    report = process_meeting(provider, TRANSCRIPT, parse_anchors("! Monday slowness"))
    assert report.coverage[0].status == CoverageStatus.FORCED
    assert "[User note] Monday slowness" in report.summary.lines()


def test_context_anchors_are_background_only():
    provider = FakeProvider(base_answers(lambda p: CoverageCheck(covered=True, line_number=1)))
    report = process_meeting(provider, TRANSCRIPT, parse_anchors("ctx: Rahul decides"))
    assert report.coverage == []
    assert report.conflicts == []
    summary_prompt = next(prompt for name, prompt in provider.calls if name == "Summary")
    assert "Rahul decides" in summary_prompt


def test_contradicted_anchor_is_never_forced_into_summary():
    answers = base_answers(lambda p: CoverageCheck(covered=False))
    answers[ConflictCheck] = contradiction
    provider = FakeProvider(answers)
    report = process_meeting(provider, TRANSCRIPT, parse_anchors("! Go-live 15 December"))
    assert report.coverage[0].status == CoverageStatus.CONFLICT
    assert not any("15 December" in line for line in report.summary.lines())
    assert not any(name in ("CoverageCheck", "RepairedPoint") for name, _ in provider.calls)


def test_report_shows_conflicts_and_excludes_them_from_coverage_rate():
    answers = base_answers(lambda p: CoverageCheck(covered=True, line_number=1))
    consistent = answers[ConflictCheck]
    answers[ConflictCheck] = lambda p: contradiction(p) if "15 December" in p else consistent(p)
    anchors = parse_anchors("! Go-live 15 December\n! Monday loads are slow")
    report = process_meeting(FakeProvider(answers), TRANSCRIPT, anchors)
    md = render_markdown(report)
    assert "Transcript says 20 December" in md
    assert "contradicts the transcript" in md
    assert "Covered without repair: 100%" in md
