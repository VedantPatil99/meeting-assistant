"""Meeting-type templates add their own sections to the summary."""

from meeting_assistant.models import Point, Summary
from meeting_assistant.pipeline.summarize import summarize
from meeting_assistant.pipeline.templates import summary_schema, template_for
from meeting_assistant.report import render_markdown
from meeting_assistant.transcript import parse_transcript

from .conftest import FakeProvider
from .test_pipeline import TRANSCRIPT


def test_template_picked_by_keywords():
    assert template_for("Daily standup").name == "standup"
    assert template_for("sales discovery").name == "sales"
    assert template_for("Sprint 18 planning").name == "planning"
    assert template_for("incident review").name == "incident"
    assert template_for("general") is None


def test_general_meetings_use_the_plain_summary():
    assert summary_schema(None) is Summary


def test_template_sections_are_required_fields_the_model_must_fill():
    schema = summary_schema(template_for("standup")).model_json_schema()
    assert {"updates", "blockers"} <= set(schema["required"])
    assert "sections" not in schema["properties"]


def standup_answer(prompt):
    return summary_schema(template_for("standup"))(
        decisions=[Point(text="Release 4.2 cut on Thursday")],
        updates=[Point(text="Arjun: finished the amendment screen, writing tests next")],
        blockers=[Point(text="Deepa blocked: Barclays test environment down")],
    )


def test_sections_reach_the_summary_and_report():
    provider = FakeProvider({summary_schema(template_for("standup")): standup_answer})
    summary = summarize(provider, TRANSCRIPT, [], "standup")
    assert summary.sections["blockers"][0].text.startswith("Deepa blocked")
    assert "Deepa blocked: Barclays test environment down" in summary.lines()  # coverage checks see it
    assert "blockers: Anything stopping someone" in provider.calls[0][1]
    from meeting_assistant.models import MeetingReport
    md = render_markdown(MeetingReport(title="Standup", meeting_type="standup", anchors=[], summary=summary,
                                       coverage=[], conflicts=[]))
    assert md.index("## Updates") < md.index("## Blockers") < md.index("## Decisions")


def test_long_meetings_merge_template_sections():
    long = parse_transcript("\n".join(f"[00:{i // 60:02d}:{i % 60:02d}] Meera: update number {i} " + "x" * 80
                                      for i in range(120)))
    provider = FakeProvider({summary_schema(template_for("standup")): standup_answer})
    summary = summarize(provider, long, [], "standup", max_chunk_chars=6000)
    merge_prompt = provider.calls[-1][1]
    assert len(provider.calls) > 2 and "Merge them" in merge_prompt
    assert '"blockers"' in merge_prompt and '"sections"' not in merge_prompt
    assert summary.sections["updates"]
