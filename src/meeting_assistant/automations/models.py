"""No-code automation rules: a trigger, optional conditions, one or more actions, and a run mode.

Rules are data, not code. The editor in the app is a form over these models, and the
same JSON can be exported and shared.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field


class Trigger(str, Enum):
    MEETING_PROCESSED = "meeting_processed"
    ACTION_ITEM = "action_item_created"
    CONFLICT_FOUND = "conflict_found"


TRIGGER_LABELS = {
    Trigger.MEETING_PROCESSED: "A meeting is processed",
    Trigger.ACTION_ITEM: "An action item is found",
    Trigger.CONFLICT_FOUND: "A note conflicts with the meeting",
}


class ActionType(str, Enum):
    DRAFT_EMAIL = "draft_email"
    CREATE_TASKS = "create_tasks"
    NOTIFY = "notify"
    SAVE_SUMMARY = "save_summary"
    NOTION_PAGE = "notion_page"
    NOTION_TASKS = "notion_tasks"


ACTION_LABELS = {
    ActionType.DRAFT_EMAIL: "Draft an email",
    ActionType.CREATE_TASKS: "Create tasks from action items",
    ActionType.NOTIFY: "Show me a notification",
    ActionType.SAVE_SUMMARY: "Save the summary to a folder",
    ActionType.NOTION_PAGE: "Save the meeting to Notion",
    ActionType.NOTION_TASKS: "Create tasks in Notion",
}


class Mode(str, Enum):
    AUTO = "auto"
    REVIEW = "review"
    NOTIFY_ONLY = "notify_only"


MODE_LABELS = {
    Mode.AUTO: "Run automatically",
    Mode.REVIEW: "Wait for my approval",
    Mode.NOTIFY_ONLY: "Only tell me what it would do",
}


class Recipients(str, Enum):
    EXTERNAL = "external"
    INTERNAL = "internal"
    ALL = "all"
    ME = "me"
    CUSTOM = "custom"


RECIPIENT_LABELS = {
    Recipients.EXTERNAL: "External attendees (clients)",
    Recipients.INTERNAL: "Internal attendees (my company)",
    Recipients.ALL: "All attendees",
    Recipients.ME: "Only me",
    Recipients.CUSTOM: "These addresses",
}


class FieldSpec(BaseModel):
    label: str
    kind: str  # choice | text | bool | number
    ops: list[str]
    triggers: list[Trigger] = list(Trigger)
    choices: list[str] = []


OP_LABELS = {
    "is": "is",
    "is_not": "is not",
    "contains": "contains",
    "not_contains": "does not contain",
    "at_least": "is at least",
}

MEETING_TYPES = ["client feedback", "general", "standup", "sprint planning", "1:1"]

FIELDS: dict[str, FieldSpec] = {
    "meeting_type": FieldSpec(label="Meeting type", kind="choice", ops=["is", "is_not"], choices=MEETING_TYPES),
    "title": FieldSpec(label="Meeting title", kind="text", ops=["contains", "not_contains"]),
    "has_external": FieldSpec(label="Has external attendees", kind="bool", ops=["is"]),
    "has_conflicts": FieldSpec(label="Has conflicts with my notes", kind="bool", ops=["is"]),
    "has_feature_requests": FieldSpec(label="Has feature requests", kind="bool", ops=["is"]),
    "action_item_count": FieldSpec(label="Number of action items", kind="number", ops=["at_least"]),
    "item_owner": FieldSpec(
        label="Action item owner", kind="text", ops=["is", "contains"], triggers=[Trigger.ACTION_ITEM]
    ),
    "item_text": FieldSpec(
        label="Action item text", kind="text", ops=["contains", "not_contains"], triggers=[Trigger.ACTION_ITEM]
    ),
}


def fields_for(trigger: Trigger) -> dict[str, FieldSpec]:
    return {key: spec for key, spec in FIELDS.items() if trigger in spec.triggers}


class Condition(BaseModel):
    field: str
    op: str
    value: str


class Action(BaseModel):
    type: ActionType
    params: dict[str, str] = {}


class Rule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    enabled: bool = True
    trigger: Trigger
    conditions: list[Condition] = []
    actions: list[Action]
    mode: Mode = Mode.REVIEW

    def describe(self) -> str:
        """One plain sentence, as shown in the rules list."""
        parts = [f"When {TRIGGER_LABELS[self.trigger].lower()}"]
        for c in self.conditions:
            spec = FIELDS.get(c.field)
            if spec:
                value = {"true": "yes", "false": "no"}.get(c.value, c.value)
                parts.append(f"if {spec.label.lower()} {OP_LABELS.get(c.op, c.op)} {value}")
        actions = ", ".join(ACTION_LABELS[a.type].lower() for a in self.actions)
        return f"{', '.join(parts)}: {actions}. {MODE_LABELS[self.mode]}."

    def validate_rule(self) -> list[str]:
        """Problems that stop the rule from being saved, in plain words."""
        problems = []
        if not self.name.strip():
            problems.append("Give the automation a name.")
        if not self.actions:
            problems.append("Add at least one action.")
        allowed = fields_for(self.trigger)
        for c in self.conditions:
            spec = allowed.get(c.field)
            if spec is None:
                problems.append(f"The condition '{c.field}' doesn't apply to this trigger.")
            elif c.op not in spec.ops:
                problems.append(f"'{spec.label}' can't use '{c.op}'.")
            elif spec.kind == "number" and not c.value.strip().isdigit():
                problems.append(f"'{spec.label}' needs a whole number.")
            elif spec.kind in ("text", "choice") and not c.value.strip():
                problems.append(f"'{spec.label}' needs a value.")
        for a in self.actions:
            if a.type == ActionType.DRAFT_EMAIL:
                if not a.params.get("subject", "").strip():
                    problems.append("The email needs a subject.")
                if a.params.get("to") == Recipients.CUSTOM and not a.params.get("custom_to", "").strip():
                    problems.append("Enter at least one email address.")
            if a.type == ActionType.NOTIFY and not a.params.get("message", "").strip():
                problems.append("The notification needs a message.")
            if a.type == ActionType.SAVE_SUMMARY and not a.params.get("folder", "").strip():
                problems.append("Choose a folder for the summary.")
        return problems
