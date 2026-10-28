"""Runs rules against meeting events.

Each matching rule first *plans* its effects (emails, tasks, notifications, files) without
touching anything. The run mode then decides what happens to the plan: run it, queue it for
approval, or only describe it. A test run is the plan alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

from ..report import render_markdown
from .context import EventContext, Prefs, condition_matches, render
from .models import Action, ActionType, Mode, Recipients, Rule, Trigger
from .store import Store

MAX_AUTO_RUNS_PER_HOUR = 20


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"


@dataclass
class Outcome:
    rule: Rule
    status: str  # done | waiting for approval | notified | would run | skipped | rate limited
    effects: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def resolve_recipients(action: Action, ctx: EventContext, prefs: Prefs) -> list[str]:
    choice = action.params.get("to", Recipients.EXTERNAL)
    if choice == Recipients.CUSTOM:
        return [a.strip() for a in re.split(r"[,;\s]+", action.params.get("custom_to", "")) if a.strip()]
    if choice == Recipients.ME:
        return [prefs.my_email] if prefs.my_email else []
    if choice == Recipients.EXTERNAL:
        return [a for a in ctx.attendees if not prefs.is_internal(a)]
    if choice == Recipients.INTERNAL:
        return [a for a in ctx.attendees if prefs.is_internal(a)]
    return list(ctx.attendees)


def plan_action(action: Action, ctx: EventContext, prefs: Prefs, out_dir: Path) -> tuple[list[dict], list[str]]:
    report = ctx.report
    base = {"out_dir": str(out_dir), "meeting": report.title}

    if action.type == ActionType.DRAFT_EMAIL:
        to = resolve_recipients(action, ctx, prefs)
        if not to:
            return [], ["Email skipped: no matching recipients for this meeting."]
        external = any(not prefs.is_internal(a) for a in to)
        subject = render(action.params.get("subject", ""), ctx, external)
        body = render(action.params.get("body", ""), ctx, external)
        audience = "external" if external else "internal"
        return [{
            **base, "kind": "email", "to": to, "subject": subject, "body": body,
            "title": f"Email to {', '.join(to)} ({audience})",
            "preview": f"To: {', '.join(to)}\nSubject: {subject}\n\n{body}",
        }], []

    if action.type == ActionType.CREATE_TASKS:
        items = [ctx.item] if ctx.trigger == Trigger.ACTION_ITEM and ctx.item else report.summary.action_items
        if not items:
            return [], ["Tasks skipped: the meeting has no action items."]
        return [{
            **base, "kind": "task", "text": i.task, "owner": i.owner or "", "due": i.due or "",
            "title": f"Task: {i.task}",
            "preview": f"{i.task}\nOwner: {i.owner or 'unassigned'}   Due: {i.due or 'not set'}",
        } for i in items], []

    if action.type == ActionType.NOTIFY:
        message = render(action.params.get("message", ""), ctx)
        return [{**base, "kind": "notify", "message": message, "title": "Notification", "preview": message}], []

    if action.type == ActionType.SAVE_SUMMARY:
        folder = action.params.get("folder", "")
        day = ctx.date[:10] if ctx.date else f"{datetime.now():%Y-%m-%d}"
        name = f"{day}_{slug(report.title)}.md"
        return [{
            **base, "kind": "file", "path": str(Path(folder) / name), "content": render_markdown(report),
            "title": f"Save summary to {Path(folder) / name}",
            "preview": f"Saves the full report as {Path(folder) / name}",
        }], []

    return [], [f"Unknown action {action.type}"]


class Engine:
    def __init__(self, store: Store, default_out_dir: Path, notifier: Callable[[str, str], None] = lambda t, m: None):
        self.store = store
        self.default_out_dir = default_out_dir
        self.notifier = notifier

    def matches(self, rule: Rule, ctx: EventContext, prefs: Prefs) -> bool:
        return rule.trigger == ctx.trigger and all(condition_matches(c, ctx, prefs) for c in rule.conditions)

    def plan(self, rule: Rule, ctx: EventContext, prefs: Prefs) -> tuple[list[dict], list[str]]:
        out_dir = ctx.folder or self.default_out_dir
        effects, skipped = [], []
        for action in rule.actions:
            e, s = plan_action(action, ctx, prefs, out_dir)
            effects += e
            skipped += s
        return effects, skipped

    def test_run(self, rule: Rule, events: list[EventContext]) -> list[Outcome]:
        """What the rule would do for these events. Changes nothing."""
        prefs = self.store.prefs()
        outcomes = []
        for ctx in events:
            if self.matches(rule, ctx, prefs):
                effects, skipped = self.plan(rule, ctx, prefs)
                outcomes.append(Outcome(rule, "would run", effects, skipped))
        return outcomes

    def handle(self, events: list[EventContext]) -> list[Outcome]:
        prefs = self.store.prefs()
        outcomes = []
        for rule in self.store.rules():
            if not rule.enabled:
                continue
            for ctx in events:
                if self.matches(rule, ctx, prefs):
                    outcomes.append(self._run(rule, ctx, prefs))
        waiting = sum(len(o.effects) for o in outcomes if o.status == "waiting for approval")
        if waiting:
            self.notifier("Waiting for your approval", f"{waiting} item(s) in Automations > Approvals")
        return outcomes

    def _run(self, rule: Rule, ctx: EventContext, prefs: Prefs) -> Outcome:
        effects, skipped = self.plan(rule, ctx, prefs)
        meeting = ctx.report.title
        if not effects:
            self.store.log_run(rule, meeting, "skipped", "; ".join(skipped))
            return Outcome(rule, "skipped", effects, skipped)

        if rule.mode == Mode.AUTO:
            if self.store.runs_in_last_hour(rule.id) >= MAX_AUTO_RUNS_PER_HOUR:
                self.store.log_run(rule, meeting, "rate limited", "Paused: ran 20 times in the last hour.")
                return Outcome(rule, "rate limited", effects, skipped)
            for effect in effects:
                self.execute(effect)
            status = "done"
        elif rule.mode == Mode.REVIEW:
            for effect in effects:
                self.store.queue(rule, meeting, effect)
            status = "waiting for approval"
        else:
            self.notifier(rule.name, "Would do: " + "; ".join(e["title"] for e in effects))
            status = "notified"
        self.store.log_run(rule, meeting, status, "; ".join(e["title"] for e in effects + [{"title": s} for s in skipped]))
        return Outcome(rule, status, effects, skipped)

    def approve(self, outbox_id: int) -> str:
        row = next(r for r in self.store.pending() if r["id"] == outbox_id)
        result = self.execute(json.loads(row["effect"]))
        self.store.decide(outbox_id, "approved")
        return result

    def reject(self, outbox_id: int) -> None:
        self.store.decide(outbox_id, "rejected")

    def execute(self, effect: dict) -> str:
        out_dir = Path(effect["out_dir"])
        kind = effect["kind"]
        if kind == "email":
            # saved as an unsent draft: Outlook and Windows Mail open .eml files with X-Unsent as a new message
            msg = EmailMessage()
            msg["To"] = ", ".join(effect["to"])
            msg["Subject"] = effect["subject"]
            msg["X-Unsent"] = "1"
            msg.set_content(effect["body"])
            folder = out_dir / "emails"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{datetime.now():%Y%m%d_%H%M%S}_{slug(effect['subject'])}.eml"
            path.write_bytes(bytes(msg))
            return str(path)
        if kind == "task":
            self.store.add_task(effect["text"], effect["owner"], effect["due"], effect["meeting"])
            out_dir.mkdir(parents=True, exist_ok=True)
            meta = ", ".join(x for x in [effect["owner"], effect["due"] and f"due {effect['due']}"] if x)
            with open(out_dir / "tasks.md", "a", encoding="utf-8") as f:
                f.write(f"- [ ] {effect['text']}" + (f" ({meta})" if meta else "") + "\n")
            return "task added"
        if kind == "notify":
            self.notifier(effect["meeting"], effect["message"])
            return "notified"
        if kind == "file":
            path = Path(effect["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(effect["content"], encoding="utf-8")
            return str(path)
        raise ValueError(f"Unknown effect kind {kind}")
