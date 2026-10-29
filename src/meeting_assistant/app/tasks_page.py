"""Tasks: action items from meetings, scheduled with real dates, plus tasks you add yourself.

Meetings suggest tasks; nothing is scheduled until you press Schedule. Open tasks are grouped
by when they're due, and the app reminds you from the tray on the day. Notion is optional:
when it's connected, a task can also be added there.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..automations.store import Store
from ..scheduling import describe_due
from ..session import MeetingSession
from .background import run_in_background

GROUPS = ["Suggested from meetings", "Overdue", "Today", "Next 7 days", "Later", "No date", "Done"]


def group_for(row, today: date) -> str:
    if row["status"] == "suggested":
        return "Suggested from meetings"
    if row["status"] == "done":
        return "Done"
    if not row["due_date"]:
        return "No date"
    due = date.fromisoformat(row["due_date"])
    if due < today:
        return "Overdue"
    if due == today:
        return "Today"
    return "Next 7 days" if due <= today + timedelta(days=7) else "Later"


class TasksPage(QWidget):
    counts_changed = Signal(int)  # suggested + overdue + due today, for the sidebar badge

    def __init__(self, store: Store, notion):
        super().__init__()
        self.store = store
        self.notion = notion
        self.current: int | None = None

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Task", "Owner", "Due", "Meeting"])
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 110)
        self.tree.setRootIsDecorated(True)
        self.tree.currentItemChanged.connect(lambda item, _: self.select(item))
        new = QPushButton("New task")
        new.clicked.connect(self.new_task)
        self.schedule_all = QPushButton("Schedule all suggestions")
        self.schedule_all.clicked.connect(self.accept_all)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Tasks</b>"))
        top.addStretch()
        top.addWidget(self.schedule_all)
        top.addWidget(new)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addLayout(top)
        left.addWidget(self.tree)
        left_widget = QWidget()
        left_widget.setLayout(left)

        # editor
        self.text = QLineEdit()
        self.owner = QLineEdit()
        self.owner.setPlaceholderText("Who does it")
        self.has_date = QCheckBox("Due")
        self.has_date.toggled.connect(lambda on: self.due.setEnabled(on))
        self.due = QDateEdit()
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("ddd d MMM yyyy")
        due_row = QHBoxLayout()
        due_row.addWidget(self.has_date)
        due_row.addWidget(self.due, 1)
        self.said = QLabel()
        self.said.setStyleSheet("color: palette(placeholder-text);")
        self.meeting = QLabel()
        self.meeting.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Task", self.text)
        form.addRow("Owner", self.owner)
        form.addRow("", due_row)
        form.addRow("Said in meeting", self.said)
        form.addRow("Meeting", self.meeting)

        self.schedule_button = QPushButton("Schedule")
        self.schedule_button.clicked.connect(lambda: self.save(status="open"))
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(lambda: self.save())
        self.done_button = QPushButton("Mark done")
        self.done_button.clicked.connect(self.toggle_done)
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.clicked.connect(lambda: self.save(status="dismissed"))
        self.notion_button = QPushButton("Add to Notion")
        self.notion_button.clicked.connect(self.add_to_notion)
        self.folder_button = QPushButton("Open meeting folder")
        self.folder_button.clicked.connect(self.open_folder)
        buttons = QHBoxLayout()
        for b in (self.schedule_button, self.save_button, self.done_button, self.dismiss_button):
            buttons.addWidget(b)
        more = QHBoxLayout()
        more.addWidget(self.notion_button)
        more.addWidget(self.folder_button)
        more.addStretch()
        self.status = QLabel()
        self.status.setWordWrap(True)

        right = QVBoxLayout()
        right.addLayout(form)
        right.addLayout(buttons)
        right.addLayout(more)
        right.addWidget(self.status)
        right.addStretch()
        self.editor = QWidget()
        self.editor.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(self.editor)
        splitter.setSizes([640, 380])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.refresh()

    def refresh(self, keep: int | None = None) -> None:
        keep = keep if keep is not None else self.current
        today = date.today()
        rows = [r for r in self.store.tasks() if r["status"] != "dismissed"]
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for name in GROUPS:
            members = [r for r in rows if group_for(r, today) == name]
            if not members:
                continue
            group = QTreeWidgetItem([f"{name} ({len(members)})"])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(group)
            groups[name] = group
            members.sort(key=lambda r: (r["due_date"] or "9999", r["id"]))
            for r in members:
                due = date.fromisoformat(r["due_date"]) if r["due_date"] else None
                item = QTreeWidgetItem([r["text"], r["owner"] or "", describe_due(due, today), r["meeting"] or ""])
                item.setData(0, Qt.UserRole, r["id"])
                if name == "Overdue":
                    item.setForeground(2, QColor("#c62828"))
                if name == "Done":
                    for col in range(4):
                        item.setForeground(col, QColor("#9e9e9e"))
                group.addChild(item)
                if r["id"] == keep:
                    self.tree.setCurrentItem(item)
            group.setExpanded(name != "Done")
        self.schedule_all.setVisible("Suggested from meetings" in groups)
        if self.tree.currentItem() is None:
            self.select(None)
        busy = sum(group_for(r, today) in ("Suggested from meetings", "Overdue", "Today") for r in rows)
        self.counts_changed.emit(busy)

    def select(self, item: QTreeWidgetItem | None) -> None:
        task_id = item.data(0, Qt.UserRole) if item else None
        row = self.store.task(task_id) if task_id else None
        self.current = task_id if row else None
        self.editor.setEnabled(row is not None)
        if row is None:
            for w in (self.text, self.owner):
                w.clear()
            self.said.clear()
            self.meeting.clear()
            self.has_date.setChecked(False)
            self.due.setEnabled(False)
            return
        self.text.setText(row["text"])
        self.owner.setText(row["owner"] or "")
        self.has_date.setChecked(bool(row["due_date"]))
        self.due.setEnabled(bool(row["due_date"]))
        self.due.setDate(QDate.fromString(row["due_date"], "yyyy-MM-dd") if row["due_date"] else QDate.currentDate())
        self.said.setText(f"“{row['due']}”" if row["due"] else "no date mentioned")
        self.meeting.setText(row["meeting"] or "Added by you")
        suggested, done = row["status"] == "suggested", row["status"] == "done"
        self.schedule_button.setVisible(suggested)
        self.done_button.setVisible(not suggested)
        self.done_button.setText("Reopen" if done else "Mark done")
        self.dismiss_button.setText("Dismiss" if suggested else "Delete")
        self.folder_button.setEnabled(bool(row["folder"]) and Path(row["folder"]).exists())
        self.notion_button.setVisible(self.notion.is_ready)
        self.notion_button.setEnabled(not row["notion_url"])
        self.notion_button.setText("In Notion" if row["notion_url"] else "Add to Notion")
        self.status.clear()

    def save(self, status: str | None = None) -> None:
        if self.current is None:
            return
        fields = {
            "text": self.text.text().strip() or "Untitled task",
            "owner": self.owner.text().strip(),
            "due_date": self.due.date().toString("yyyy-MM-dd") if self.has_date.isChecked() else None,
        }
        if status:
            fields["status"] = status
        self.store.update_task(self.current, **fields)
        self.refresh(keep=self.current)
        self.status.setText({"open": "Scheduled.", "dismissed": "Removed."}.get(status, "Saved."))

    def toggle_done(self) -> None:
        row = self.store.task(self.current)
        self.save(status="open" if row["status"] == "done" else "done")

    def accept_all(self) -> None:
        for row in self.store.tasks("suggested"):
            self.store.update_task(row["id"], status="open")
        self.refresh()

    def new_task(self) -> None:
        task_id = self.store.add_task("New task", "", "", "", status="open")
        self.refresh(keep=task_id)
        self.text.setFocus()
        self.text.selectAll()

    def open_folder(self) -> None:
        row = self.store.task(self.current)
        if row and row["folder"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(row["folder"]))

    def add_to_notion(self) -> None:
        sync = self.notion.sync()
        if sync is None:
            self.status.setText("Notion isn't connected. Connect it in Settings.")
            return
        self.save()
        row = self.store.task(self.current)
        task_id = row["id"]
        self.notion_button.setEnabled(False)
        self.status.setText("Adding to Notion…")

        def done(url):
            self.store.update_task(task_id, notion_url=url or "in notion")
            self.refresh(keep=task_id)
            self.status.setText("Added to Notion." if url else "Already in Notion.")

        def failed(message):
            self.notion_button.setEnabled(True)
            self.status.setText(f"Couldn't add to Notion: {message}")

        # the meeting's start time links the task to its Notion meeting page
        info = Path(row["folder"] or ".") / "meeting.json"
        started = MeetingSession(info.parent).load_info().started_at if row["folder"] and info.exists() else ""
        due = row["due_date"] or row["due"] or ""
        run_in_background(self, lambda: sync.save_task(row["text"], row["owner"] or "", due,
                                                       row["meeting"] or "", started),
                          done, failed)
