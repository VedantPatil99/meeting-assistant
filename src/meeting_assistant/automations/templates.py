"""Ready-made automations people can switch on with one click and then adjust."""

from __future__ import annotations

from .models import Action, ActionType, Condition, Mode, Recipients, Rule, Trigger

CLIENT_RECAP_BODY = """Hi all,

Thank you for your time today. Here is what we agreed:

{{decisions}}

Next steps:
{{action_items}}

Still open:
{{open_questions}}

Please reply if I have missed anything.
"""

INTERNAL_SUMMARY_BODY = """Summary of {{meeting.title}} ({{meeting.date}}):

Decisions:
{{decisions}}

Key points:
{{key_points}}

Action items:
{{action_items}}

Notes that disagreed with what was said:
{{conflicts}}
"""


def builtin_templates() -> list[tuple[str, Rule]]:
    """(description, rule) pairs. Each call returns fresh rules with new ids."""
    return [
        (
            "After a client meeting, draft a recap for the client to confirm what was agreed. "
            "Your internal notes are never included.",
            Rule(
                name="Client recap email",
                trigger=Trigger.MEETING_PROCESSED,
                conditions=[
                    Condition(field="meeting_type", op="is", value="client feedback"),
                    Condition(field="has_external", op="is", value="true"),
                ],
                actions=[Action(type=ActionType.DRAFT_EMAIL, params={
                    "to": Recipients.EXTERNAL.value,
                    "subject": "Recap: {{meeting.title}}",
                    "body": CLIENT_RECAP_BODY,
                })],
                mode=Mode.REVIEW,
            ),
        ),
        (
            "Turn every action item into a task with its owner and due date.",
            Rule(
                name="Tasks from every meeting",
                trigger=Trigger.MEETING_PROCESSED,
                actions=[Action(type=ActionType.CREATE_TASKS)],
                mode=Mode.AUTO,
            ),
        ),
        (
            "Tell me straight away when one of my notes disagrees with what the meeting decided.",
            Rule(
                name="Alert me about conflicts",
                trigger=Trigger.CONFLICT_FOUND,
                actions=[Action(type=ActionType.NOTIFY, params={
                    "message": "Your note \"{{conflict.note}}\" doesn't match the meeting: {{conflict.explanation}}",
                })],
                mode=Mode.AUTO,
            ),
        ),
        (
            "Send my team an internal summary, including conflicts, after each meeting.",
            Rule(
                name="Internal summary to my team",
                trigger=Trigger.MEETING_PROCESSED,
                actions=[Action(type=ActionType.DRAFT_EMAIL, params={
                    "to": Recipients.INTERNAL.value,
                    "subject": "Summary: {{meeting.title}}",
                    "body": INTERNAL_SUMMARY_BODY,
                })],
                mode=Mode.REVIEW,
            ),
        ),
        (
            "Keep a copy of every meeting report in a folder, such as a shared drive.",
            Rule(
                name="Archive reports to a folder",
                trigger=Trigger.MEETING_PROCESSED,
                actions=[Action(type=ActionType.SAVE_SUMMARY, params={"folder": ""})],
                mode=Mode.AUTO,
            ),
        ),
    ]
