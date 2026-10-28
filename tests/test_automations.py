import email
from pathlib import Path

import pytest

from meeting_assistant.anchors import parse_anchors
from meeting_assistant.automations import (
    Action,
    ActionType,
    Condition,
    Engine,
    Mode,
    Prefs,
    Recipients,
    Rule,
    Store,
    Trigger,
    builtin_templates,
    events_from_report,
)
from meeting_assistant.models import (
    ActionItem,
    ConflictResult,
    ConflictVerdict,
    CoverageResult,
    CoverageStatus,
    MeetingReport,
    Point,
    Summary,
)

ATTENDEES = ["me@ourco.com", "rahul@client.com", "priya@client.com"]


@pytest.fixture
def report():
    anchors = parse_anchors(
        "! go-live 15 December\n"
        "internal: client is hesitant about licence cost\n"
        "fr: export to Excel"
    )
    return MeetingReport(
        title="Dashboard pilot feedback",
        meeting_type="client feedback",
        anchors=anchors,
        summary=Summary(
            decisions=[Point(text="Go-live on 20 December")],
            key_points=[Point(text="Client hesitant about licence cost"), Point(text="Wants Excel export")],
            action_items=[
                ActionItem(task="Send licence breakdown", owner="Vedant", due="Friday"),
                ActionItem(task="Investigate Monday slowness", owner="Ankit"),
            ],
        ),
        coverage=[
            CoverageResult(anchor_id=1, status=CoverageStatus.CONFLICT),
            CoverageResult(anchor_id=2, status=CoverageStatus.REPAIRED, evidence="Client hesitant about licence cost"),
            CoverageResult(anchor_id=3, status=CoverageStatus.COVERED, evidence="Wants Excel export"),
        ],
        conflicts=[
            ConflictResult(anchor_id=1, verdict=ConflictVerdict.CONTRADICTS, explanation="Agreed on 20 December"),
        ],
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    s.save_prefs(Prefs(my_email="me@ourco.com", company_domain="ourco.com"))
    return s


def engine(store, tmp_path, notes=None):
    return Engine(store, tmp_path, notifier=lambda title, msg: notes.append((title, msg)) if notes is not None else None)


def events(report, tmp_path):
    return events_from_report(report, date="2026-10-28T15:00:00", attendees=ATTENDEES, folder=tmp_path)


def recap_rule(**changes):
    _, rule = builtin_templates()[0]
    return rule.model_copy(update=changes)


def test_events_cover_meeting_items_and_conflicts(report, tmp_path):
    triggers = [e.trigger for e in events(report, tmp_path)]
    assert triggers == [Trigger.MEETING_PROCESSED, Trigger.ACTION_ITEM, Trigger.ACTION_ITEM, Trigger.CONFLICT_FOUND]


def test_client_email_goes_to_external_attendees_without_internal_notes(report, store, tmp_path):
    outcomes = engine(store, tmp_path).test_run(recap_rule(), events(report, tmp_path))
    assert len(outcomes) == 1
    effect = outcomes[0].effects[0]
    assert effect["to"] == ["rahul@client.com", "priya@client.com"]
    assert "Go-live on 20 December" in effect["body"]
    assert "Wants Excel export" not in effect["body"]  # recap template has no key points
    assert "licence cost" not in effect["body"]
    assert "Send licence breakdown (Vedant, due Friday)" in effect["body"]


def test_internal_email_keeps_internal_notes_and_conflicts(report, store, tmp_path):
    _, rule = builtin_templates()[3]
    effect = engine(store, tmp_path).test_run(rule, events(report, tmp_path))[0].effects[0]
    assert effect["to"] == ["me@ourco.com"]
    assert "Client hesitant about licence cost" in effect["body"]
    assert "Agreed on 20 December" in effect["body"]


def test_external_render_hides_internal_lines_even_in_key_points(report, store, tmp_path):
    rule = recap_rule(actions=[Action(type=ActionType.DRAFT_EMAIL, params={
        "to": Recipients.EXTERNAL.value, "subject": "x", "body": "{{key_points}}|{{conflicts}}",
    })])
    body = engine(store, tmp_path).test_run(rule, events(report, tmp_path))[0].effects[0]["body"]
    assert body == "- Wants Excel export|"


def test_conditions_filter_meetings(report, store, tmp_path):
    rule = recap_rule(conditions=[Condition(field="meeting_type", op="is", value="standup")])
    assert engine(store, tmp_path).test_run(rule, events(report, tmp_path)) == []
    rule = recap_rule(conditions=[Condition(field="action_item_count", op="at_least", value="2")])
    assert len(engine(store, tmp_path).test_run(rule, events(report, tmp_path))) == 1


def test_action_item_trigger_with_owner_condition(report, store, tmp_path):
    rule = Rule(
        name="My tasks", trigger=Trigger.ACTION_ITEM,
        conditions=[Condition(field="item_owner", op="is", value="vedant")],
        actions=[Action(type=ActionType.CREATE_TASKS)], mode=Mode.AUTO,
    )
    outcomes = engine(store, tmp_path).test_run(rule, events(report, tmp_path))
    assert [e["text"] for o in outcomes for e in o.effects] == ["Send licence breakdown"]


def test_test_run_changes_nothing(report, store, tmp_path):
    store.save_rule(recap_rule())
    engine(store, tmp_path).test_run(recap_rule(), events(report, tmp_path))
    assert store.pending() == [] and store.runs() == [] and not (tmp_path / "emails").exists()


def test_review_mode_queues_then_approval_writes_draft(report, store, tmp_path):
    notes = []
    eng = engine(store, tmp_path, notes)
    store.save_rule(recap_rule())
    outcomes = eng.handle(events(report, tmp_path))
    assert [o.status for o in outcomes] == ["waiting for approval"]
    assert notes and "approval" in notes[0][0]
    pending = store.pending()
    assert len(pending) == 1

    eng.approve(pending[0]["id"])
    assert store.pending() == []
    drafts = list((tmp_path / "emails").glob("*.eml"))
    assert len(drafts) == 1
    msg = email.message_from_bytes(drafts[0].read_bytes())
    assert msg["X-Unsent"] == "1" and msg["Subject"] == "Recap: Dashboard pilot feedback"


def test_auto_mode_creates_tasks_and_logs(report, store, tmp_path):
    _, rule = builtin_templates()[1]
    store.save_rule(rule)
    engine(store, tmp_path).handle(events(report, tmp_path))
    assert [t["text"] for t in store.tasks()] == ["Investigate Monday slowness", "Send licence breakdown"]
    assert "Send licence breakdown (Vedant, due Friday)" in (tmp_path / "tasks.md").read_text(encoding="utf-8")
    assert store.runs()[0]["status"] == "done"


def test_disabled_rules_do_not_run(report, store, tmp_path):
    store.save_rule(recap_rule(enabled=False))
    assert engine(store, tmp_path).handle(events(report, tmp_path)) == []


def test_email_without_recipients_is_skipped(report, store, tmp_path):
    store.save_rule(recap_rule(conditions=[]))
    report_events = events_from_report(report, attendees=["me@ourco.com"], folder=tmp_path)
    outcomes = engine(store, tmp_path).handle(report_events)
    assert outcomes[0].status == "skipped"
    assert "no matching recipients" in outcomes[0].skipped[0]


def test_auto_rules_pause_after_rate_limit(report, store, tmp_path, monkeypatch):
    monkeypatch.setattr("meeting_assistant.automations.engine.MAX_AUTO_RUNS_PER_HOUR", 2)
    _, rule = builtin_templates()[1]
    store.save_rule(rule)
    eng = engine(store, tmp_path)
    statuses = [eng.handle(events(report, tmp_path))[0].status for _ in range(3)]
    assert statuses == ["done", "done", "rate limited"]


def test_validation_messages():
    rule = Rule(name=" ", trigger=Trigger.MEETING_PROCESSED, actions=[
        Action(type=ActionType.DRAFT_EMAIL, params={"to": "custom", "subject": ""}),
    ], conditions=[Condition(field="item_owner", op="is", value="x")])
    problems = rule.validate_rule()
    assert "Give the automation a name." in problems
    assert "The email needs a subject." in problems
    assert "Enter at least one email address." in problems
    assert any("doesn't apply" in p for p in problems)
    for _, template in builtin_templates()[:4]:
        assert template.validate_rule() == []


def test_placeholder_due_dates_are_dropped():
    item = ActionItem(task="Log the request", owner=" Vedant ", due="Not specified")
    assert (item.owner, item.due) == ("Vedant", None)


def test_rules_round_trip_in_store(store):
    rule = recap_rule()
    store.save_rule(rule)
    store.save_rule(rule.model_copy(update={"enabled": False}))
    [saved] = store.rules()
    assert saved.enabled is False and saved.describe().startswith("When a meeting is processed")
