"""Automations: build rules without code, try them on a past meeting, approve what they produce."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..automations import Action, ActionType, Condition, Engine, Mode, Recipients, Rule, Store, Trigger
from ..automations.context import PLACEHOLDERS, events_from_report, unknown_placeholders
from ..automations.models import (
    ACTION_LABELS,
    FIELDS,
    MODE_LABELS,
    OP_LABELS,
    RECIPIENT_LABELS,
    TRIGGER_LABELS,
    fields_for,
)
from ..automations.templates import builtin_templates
from ..models import MeetingReport


def heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 18px; font-weight: 600;")
    return label


def section(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; margin-top: 8px;")
    return label


def quiet(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def card() -> QFrame:
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    return frame


def combo(items: dict, current=None) -> QComboBox:
    box = QComboBox()
    for value, label in items.items():
        box.addItem(label, value)
    if current is not None:
        index = box.findData(current)
        box.setCurrentIndex(max(index, 0))
    return box


def insert_field_menu(button: QPushButton, insert) -> None:
    menu = QMenu(button)
    for key, label in PLACEHOLDERS.items():
        menu.addAction(label, lambda k=key: insert("{{" + k + "}}"))
    button.setMenu(menu)


# rule editor

class ConditionRow(QWidget):
    removed = Signal(QWidget)

    def __init__(self, trigger: Trigger, condition: Condition | None = None):
        super().__init__()
        self.field = QComboBox()
        self.op = QComboBox()
        self.value_box = QHBoxLayout()
        self.value_widget: QWidget | None = None
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self.removed.emit(self))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.field, 2)
        row.addWidget(self.op, 1)
        row.addLayout(self.value_box, 2)
        row.addWidget(remove)
        self.set_trigger(trigger, condition)
        self.field.currentIndexChanged.connect(lambda: self._field_changed(None))

    def set_trigger(self, trigger: Trigger, condition: Condition | None = None) -> None:
        current = condition.field if condition else self.field.currentData()
        self.field.blockSignals(True)
        self.field.clear()
        for key, spec in fields_for(trigger).items():
            self.field.addItem(spec.label, key)
        self.field.setCurrentIndex(max(self.field.findData(current), 0))
        self.field.blockSignals(False)
        self._field_changed(condition)

    def _field_changed(self, condition: Condition | None) -> None:
        spec = FIELDS[self.field.currentData()]
        self.op.clear()
        for op in spec.ops:
            self.op.addItem(OP_LABELS[op], op)
        if condition:
            self.op.setCurrentIndex(max(self.op.findData(condition.op), 0))
        if self.value_widget:
            self.value_widget.deleteLater()
        value = condition.value if condition else ""
        if spec.kind == "bool":
            self.value_widget = combo({"true": "yes", "false": "no"}, value or "true")
        elif spec.kind == "choice":
            self.value_widget = combo({c: c for c in spec.choices}, value or None)
        else:
            self.value_widget = QLineEdit(value)
            self.value_widget.setPlaceholderText("a number" if spec.kind == "number" else "text")
        self.value_box.addWidget(self.value_widget)

    def condition(self) -> Condition:
        w = self.value_widget
        value = w.currentData() if isinstance(w, QComboBox) else w.text()
        return Condition(field=self.field.currentData(), op=self.op.currentData(), value=value)


class ActionCard(QFrame):
    removed = Signal(QFrame)

    def __init__(self, action: Action):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.type = action.type
        p = action.params
        title = QLabel(ACTION_LABELS[action.type])
        title.setStyleSheet("font-weight: 600;")
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self.removed.emit(self))
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        form = QFormLayout()
        layout.addLayout(form)

        if action.type == ActionType.DRAFT_EMAIL:
            self.to = combo({r.value: label for r, label in RECIPIENT_LABELS.items()}, p.get("to", "external"))
            self.custom_to = QLineEdit(p.get("custom_to", ""))
            self.custom_to.setPlaceholderText("name@company.com, other@company.com")
            self.subject = QLineEdit(p.get("subject", ""))
            self.body = QPlainTextEdit(p.get("body", ""))
            self.body.setMinimumHeight(150)
            insert = QPushButton("Insert meeting detail")
            insert_field_menu(insert, self.body.insertPlainText)
            self.to.currentIndexChanged.connect(self._sync_custom)
            form.addRow("To", self.to)
            form.addRow("", self.custom_to)
            form.addRow("Subject", self.subject)
            form.addRow("Message", self.body)
            form.addRow("", insert)
            form.addRow("", quiet("Your internal: notes and the conflicts list are never included "
                                  "when an email goes to anyone outside your company."))
            self._sync_custom()
        elif action.type == ActionType.CREATE_TASKS:
            form.addRow(quiet("Creates a task for each action item, with its owner and due date. "
                              "With the “action item is found” trigger, only that item."))
        elif action.type == ActionType.NOTION_PAGE:
            form.addRow(quiet("Adds the meeting to your Notion Meetings database, with the full report as "
                              "the page. Running it twice for the same meeting doesn't create a duplicate."))
        elif action.type == ActionType.NOTION_TASKS:
            form.addRow(quiet("Adds each action item to your Notion Tasks database, linked to the meeting "
                              "page, with status “To do”. Tasks already in Notion are skipped."))
        elif action.type == ActionType.NOTIFY:
            self.message = QLineEdit(p.get("message", ""))
            insert = QPushButton("Insert meeting detail")
            insert_field_menu(insert, self.message.insert)
            form.addRow("Message", self.message)
            form.addRow("", insert)
        elif action.type == ActionType.SAVE_SUMMARY:
            self.folder = QLineEdit(p.get("folder", ""))
            browse = QPushButton("Choose…")
            browse.clicked.connect(self._pick_folder)
            row = QHBoxLayout()
            row.addWidget(self.folder)
            row.addWidget(browse)
            form.addRow("Folder", row)

    def _sync_custom(self) -> None:
        self.custom_to.setVisible(self.to.currentData() == Recipients.CUSTOM.value)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def action(self) -> Action:
        if self.type == ActionType.DRAFT_EMAIL:
            params = {"to": self.to.currentData(), "subject": self.subject.text(), "body": self.body.toPlainText()}
            if self.to.currentData() == Recipients.CUSTOM.value:
                params["custom_to"] = self.custom_to.text()
            return Action(type=self.type, params=params)
        if self.type == ActionType.NOTIFY:
            return Action(type=self.type, params={"message": self.message.text()})
        if self.type == ActionType.SAVE_SUMMARY:
            return Action(type=self.type, params={"folder": self.folder.text()})
        return Action(type=self.type)

    def templates_text(self) -> str:
        if self.type == ActionType.DRAFT_EMAIL:
            return self.subject.text() + self.body.toPlainText()
        if self.type == ActionType.NOTIFY:
            return self.message.text()
        return ""


class RuleEditor(QDialog):
    def __init__(self, rule: Rule, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Automation")
        self.resize(760, 760)
        self.rule_id = rule.id
        self.enabled = rule.enabled

        self.name = QLineEdit(rule.name)
        self.name.setPlaceholderText("e.g. Client recap email")
        self.trigger = combo({t: label for t, label in TRIGGER_LABELS.items()}, rule.trigger)
        self.trigger.currentIndexChanged.connect(self._trigger_changed)
        self.mode = combo({m: label for m, label in MODE_LABELS.items()}, rule.mode)

        self.conditions = QVBoxLayout()
        add_condition = QPushButton("+ Add condition")
        add_condition.clicked.connect(lambda: self._add_condition(None))
        self.actions = QVBoxLayout()
        add_action = QPushButton("+ Add action")
        menu = QMenu(add_action)
        for action_type, label in ACTION_LABELS.items():
            menu.addAction(label, lambda t=action_type: self._add_action(Action(type=t)))
        add_action.setMenu(menu)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(section("Name"))
        layout.addWidget(self.name)
        layout.addWidget(section("When"))
        layout.addWidget(self.trigger)
        layout.addWidget(section("Only if (optional)"))
        layout.addLayout(self.conditions)
        layout.addWidget(add_condition, 0, Qt.AlignLeft)
        layout.addWidget(section("Then"))
        layout.addLayout(self.actions)
        layout.addWidget(add_action, 0, Qt.AlignLeft)
        layout.addWidget(section("How"))
        layout.addWidget(self.mode)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setStyleSheet("color: #e57373;")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll)
        outer.addWidget(self.errors)
        outer.addWidget(buttons)

        for c in rule.conditions:
            self._add_condition(c)
        for a in rule.actions:
            self._add_action(a)
        self.result_rule: Rule | None = None

    def _current_trigger(self) -> Trigger:
        return Trigger(self.trigger.currentData())

    def _rows(self, layout) -> list[QWidget]:
        return [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget()]

    def _add_condition(self, condition: Condition | None) -> None:
        row = ConditionRow(self._current_trigger(), condition)
        row.removed.connect(lambda w: w.deleteLater())
        self.conditions.addWidget(row)

    def _add_action(self, action: Action) -> None:
        action_card = ActionCard(action)
        action_card.removed.connect(lambda w: w.deleteLater())
        self.actions.addWidget(action_card)

    def _trigger_changed(self) -> None:
        trigger = self._current_trigger()
        for row in self._rows(self.conditions):
            if row.field.currentData() in fields_for(trigger):
                row.set_trigger(trigger, row.condition())
            else:
                row.deleteLater()

    def build(self) -> Rule:
        return Rule(
            id=self.rule_id,
            name=self.name.text().strip(),
            enabled=self.enabled,
            trigger=self._current_trigger(),
            conditions=[row.condition() for row in self._rows(self.conditions)],
            actions=[c.action() for c in self._rows(self.actions)],
            mode=Mode(self.mode.currentData()),
        )

    def _save(self) -> None:
        rule = self.build()
        problems = rule.validate_rule()
        for c in self._rows(self.actions):
            for name in unknown_placeholders(c.templates_text()):
                problems.append(f"Unknown detail {{{{{name}}}}}. Use “Insert meeting detail”.")
        if problems:
            self.errors.setText("\n".join(problems))
            return
        sends_out = any(
            a.type == ActionType.DRAFT_EMAIL and a.params.get("to") in ("external", "all", "custom")
            for a in rule.actions
        )
        if rule.mode == Mode.AUTO and sends_out:
            answer = QMessageBox.question(
                self, "Run without review?",
                "This automation will create emails for people outside your company without asking you first. "
                "Continue?",
            )
            if answer != QMessageBox.Yes:
                return
        self.result_rule = rule
        self.accept()


class TemplatePicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start from a template")
        self.resize(560, 420)
        self.templates = builtin_templates()
        self.list = QListWidget()
        for description, rule in self.templates:
            item = QListWidgetItem(f"{rule.name}\n{description}")
            self.list.addItem(item)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use this template")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(quiet("Pick one, then adjust it before saving."))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def chosen(self) -> Rule:
        return self.templates[self.list.currentRow()][1]


class TestRunDialog(QDialog):
    """Run one rule against a past meeting and show exactly what it would do. Nothing is changed."""

    def __init__(self, engine: Engine, rule: Rule, meetings_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Test run: {rule.name}")
        self.resize(760, 640)
        self.engine, self.rule = engine, rule
        self.meeting = QComboBox()
        for folder in sorted(meetings_dir.glob("*/report.json"), reverse=True):
            self.meeting.addItem(folder.parent.name, str(folder.parent))
        browse = QPushButton("Other report…")
        browse.clicked.connect(self._browse)
        self.attendees = QLineEdit()
        self.attendees.setPlaceholderText("client@company.com, colleague@yourcompany.com")
        run = QPushButton("Run test")
        run.clicked.connect(self.run)
        self.meeting.currentIndexChanged.connect(self._load_attendees)
        self.output = QTextBrowser()

        pick = QHBoxLayout()
        pick.addWidget(self.meeting, 1)
        pick.addWidget(browse)
        form = QFormLayout()
        form.addRow("Meeting", pick)
        form.addRow("Attendees", self.attendees)
        layout = QVBoxLayout(self)
        layout.addWidget(quiet("Shows what this automation would do for a past meeting. Nothing is sent, saved or created."))
        layout.addLayout(form)
        layout.addWidget(run, 0, Qt.AlignLeft)
        layout.addWidget(self.output, 1)
        self._load_attendees()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a report", "", "Report (*.json)")
        if path:
            self.meeting.addItem(Path(path).name, path)
            self.meeting.setCurrentIndex(self.meeting.count() - 1)

    def _paths(self) -> tuple[Path, Path | None]:
        chosen = Path(self.meeting.currentData())
        if chosen.is_dir():
            return chosen / "report.json", chosen / "meeting.json"
        return chosen, None

    def _load_attendees(self) -> None:
        if self.meeting.currentData() is None:
            return
        _, info = self._paths()
        if info and info.exists():
            self.attendees.setText(", ".join(json.loads(info.read_text(encoding="utf-8")).get("attendees", [])))

    def run(self) -> None:
        if self.meeting.currentData() is None:
            self.output.setPlainText("No processed meetings yet. Choose a report file with “Other report…”.")
            return
        report_path, info_path = self._paths()
        report = MeetingReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        date = json.loads(info_path.read_text(encoding="utf-8")).get("started_at", "") if info_path and info_path.exists() else ""
        attendees = [a.strip() for a in self.attendees.text().replace(";", ",").split(",") if a.strip()]
        events = events_from_report(report, date, attendees, report_path.parent)
        outcomes = self.engine.test_run(self.rule, events)
        if not outcomes:
            self.output.setPlainText("Nothing would happen: this meeting doesn't match the trigger and conditions.")
            return
        parts = []
        for o in outcomes:
            for e in o.effects:
                parts.append(f"<h3>{escape(e['title'])}</h3>"
                             f"<p style='white-space: pre-wrap'>{escape(e['preview'])}</p>")
            parts += [f"<p><i>Skipped: {escape(s)}</i></p>" for s in o.skipped]
        mode = MODE_LABELS[self.rule.mode]
        parts.append(f"<p>When live, this automation would: <b>{escape(mode.lower())}</b>.</p>")
        self.output.setHtml("".join(parts))


# page

class AutomationsPage(QWidget):
    approvals_changed = Signal(int)

    def __init__(self, store: Store, engine: Engine, meetings_dir: Path):
        super().__init__()
        self.store, self.engine, self.meetings_dir = store, engine, meetings_dir

        self.tabs = QTabWidget()
        self.tabs.addTab(self._rules_tab(), "Rules")
        self.tabs.addTab(self._approvals_tab(), "Approvals")
        self.tabs.addTab(self._activity_tab(), "Activity")
        self.tabs.addTab(self._tasks_tab(), "Tasks")

        layout = QVBoxLayout(self)
        layout.addWidget(heading("Automations"))
        layout.addWidget(quiet("Rules run after each meeting is processed. Build them from the "
                               "dropdowns below; no code needed."))
        layout.addWidget(self.tabs, 1)
        self.refresh()

    # rules tab
    def _rules_tab(self) -> QWidget:
        self.rules_table = QTableWidget(0, 5)
        self.rules_table.setHorizontalHeaderLabels(["On", "Name", "What it does", "Last run", "Runs"])
        header = self.rules_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for col in (0, 1, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rules_table.setSelectionMode(QTableWidget.SingleSelection)
        self.rules_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rules_table.setWordWrap(True)
        self.rules_table.doubleClicked.connect(lambda _: self.edit_rule())

        new = QPushButton("New automation")
        new.clicked.connect(lambda: self.open_editor(Rule(name="", trigger=Trigger.MEETING_PROCESSED, actions=[])))
        template = QPushButton("From a template…")
        template.clicked.connect(self.from_template)
        edit = QPushButton("Edit")
        edit.clicked.connect(self.edit_rule)
        test = QPushButton("Test run…")
        test.clicked.connect(self.test_rule)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_rule)
        buttons = QHBoxLayout()
        for b in (new, template, edit, test):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(delete)

        self.rules_empty = quiet("No automations yet. Start from a template to see how they work.")
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addLayout(buttons)
        layout.addWidget(self.rules_empty)
        layout.addWidget(self.rules_table, 1)
        return tab

    def _selected_rule(self) -> Rule | None:
        row = self.rules_table.currentRow()
        rules = self.store.rules()
        return rules[row] if 0 <= row < len(rules) else None

    def open_editor(self, rule: Rule) -> None:
        editor = RuleEditor(rule, self)
        if editor.exec() == QDialog.Accepted and editor.result_rule:
            self.store.save_rule(editor.result_rule)
            self.refresh()

    def from_template(self) -> None:
        picker = TemplatePicker(self)
        if picker.exec() == QDialog.Accepted:
            self.open_editor(picker.chosen())

    def edit_rule(self) -> None:
        rule = self._selected_rule()
        if rule:
            self.open_editor(rule)

    def test_rule(self) -> None:
        rule = self._selected_rule()
        if rule:
            TestRunDialog(self.engine, rule, self.meetings_dir, self).exec()

    def delete_rule(self) -> None:
        rule = self._selected_rule()
        if rule and QMessageBox.question(self, "Delete automation", f"Delete “{rule.name}”?") == QMessageBox.Yes:
            self.store.delete_rule(rule.id)
            self.refresh()

    def _toggle(self, rule: Rule, on: bool) -> None:
        self.store.save_rule(rule.model_copy(update={"enabled": on}))

    # approvals tab
    def _approvals_tab(self) -> QWidget:
        self.pending_list = QListWidget()
        self.pending_list.currentRowChanged.connect(self._show_pending)
        self.pending_preview = QTextBrowser()
        approve = QPushButton("Approve")
        approve.clicked.connect(lambda: self._decide(True))
        reject = QPushButton("Reject")
        reject.clicked.connect(lambda: self._decide(False))
        self.approval_status = quiet("")
        right = QWidget()
        r = QVBoxLayout(right)
        r.addWidget(self.pending_preview, 1)
        row = QHBoxLayout()
        row.addWidget(approve)
        row.addWidget(reject)
        row.addStretch()
        r.addLayout(row)
        r.addWidget(self.approval_status)
        splitter = QSplitter()
        splitter.addWidget(self.pending_list)
        splitter.addWidget(right)
        splitter.setSizes([300, 600])
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(quiet("Items from automations set to “Wait for my approval”. Approved emails are saved "
                               "as drafts you can open in Outlook or Mail."))
        layout.addWidget(splitter, 1)
        return tab

    def _show_pending(self, row: int) -> None:
        pending = self.store.pending()
        if 0 <= row < len(pending):
            effect = json.loads(pending[row]["effect"])
            self.pending_preview.setPlainText(effect["preview"])
        else:
            self.pending_preview.clear()

    def _decide(self, approve: bool) -> None:
        pending = self.store.pending()
        row = self.pending_list.currentRow()
        if not 0 <= row < len(pending):
            return
        if approve:
            try:
                result = self.engine.approve(pending[row]["id"])
            except Exception as e:  # e.g. Notion not connected: keep it in the inbox
                self.approval_status.setText(f"Couldn't complete it, so it's still waiting: {e}")
                return
            self.approval_status.setText(f"Approved. {result}")
        else:
            self.engine.reject(pending[row]["id"])
            self.approval_status.setText("Rejected.")
        self.refresh()

    # activity and tasks tabs
    def _activity_tab(self) -> QWidget:
        self.activity = self._table(["When", "Automation", "Meeting", "Result", "Details"], stretch=4)
        return self._wrap(self.activity)

    def _tasks_tab(self) -> QWidget:
        self.tasks = self._table(["Task", "Owner", "Due", "Meeting", "Added"], stretch=0)
        return self._wrap(self.tasks, "Tasks created by automations. Notion and Jira sync come in a later milestone.")

    @staticmethod
    def _table(headers: list[str], stretch: int) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = table.horizontalHeader()
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == stretch else QHeaderView.ResizeToContents)
        return table

    @staticmethod
    def _wrap(table: QTableWidget, note: str = "") -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        if note:
            layout.addWidget(quiet(note))
        layout.addWidget(table, 1)
        return tab

    @staticmethod
    def _fill(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                table.setItem(r, c, QTableWidgetItem(value or ""))

    def refresh(self) -> None:
        rules = self.store.rules()
        self.rules_empty.setVisible(not rules)
        self.rules_table.setRowCount(len(rules))
        for r, rule in enumerate(rules):
            box = QCheckBox()
            box.setChecked(rule.enabled)
            box.toggled.connect(lambda on, rule=rule: self._toggle(rule, on))
            holder = QWidget()
            h = QHBoxLayout(holder)
            h.setContentsMargins(8, 0, 8, 0)
            h.addWidget(box)
            self.rules_table.setCellWidget(r, 0, holder)
            last = self.store.last_run(rule.id)
            for c, value in enumerate([rule.name, rule.describe(), (last or "never").replace("T", " "),
                                       str(self.store.run_count(rule.id))], start=1):
                self.rules_table.setItem(r, c, QTableWidgetItem(value))
        self.rules_table.resizeRowsToContents()

        pending = self.store.pending()
        self.pending_list.clear()
        for p in pending:
            effect = json.loads(p["effect"])
            self.pending_list.addItem(f"{p['rule_name']}\n{effect['title']}")
        self.tabs.setTabText(1, f"Approvals ({len(pending)})" if pending else "Approvals")
        self.approvals_changed.emit(len(pending))

        self._fill(self.activity, [
            [r["at"].replace("T", " "), r["rule_name"], r["meeting"], r["status"], r["detail"]]
            for r in self.store.runs()
        ])
        self._fill(self.tasks, [
            [t["text"], t["owner"], t["due"], t["meeting"], t["created_at"].replace("T", " ")]
            for t in self.store.tasks()
        ])
