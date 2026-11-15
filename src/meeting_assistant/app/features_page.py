"""The feature-request tracker: every request clients made across meetings, most asked first.

Each meeting adds its requests here. A request asked for again is counted on the existing
entry instead of being added twice, so the list shows what clients keep coming back to.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..automations.store import FEATURE_STATUSES, Store
from . import theme
from .background import run_in_background

COLUMNS = ["Request", "Times asked", "Clients", "Last asked", "Status"]


class FeaturesPage(QWidget):
    new_count_changed = Signal(int)  # requests nobody has looked at yet

    def __init__(self, store: Store, notion):
        super().__init__()
        self.store = store
        self.notion = notion
        self.current: int | None = None

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        for col in (1, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.select)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(theme.title("Feature requests"))
        left.addWidget(theme.muted("Most asked first. A request asked again is counted, not added twice."))
        left.addWidget(self.table)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.title = QLineEdit()
        self.detail = QPlainTextEdit()
        self.detail.setMaximumHeight(70)
        self.status_box = QComboBox()
        self.status_box.addItems(FEATURE_STATUSES)
        form = QFormLayout()
        form.addRow("Request", self.title)
        form.addRow("Detail", self.detail)
        form.addRow("Status", self.status_box)

        self.mentions = QLabel()
        self.mentions.setWordWrap(True)
        self.mentions.setTextFormat(Qt.RichText)
        self.mentions.setAlignment(Qt.AlignTop)
        self.mentions.setMinimumWidth(240)

        save = QPushButton("Save")
        theme.set_role(save, "primary")
        save.clicked.connect(self.save)
        self.notion_button = QPushButton("Add to Notion")
        self.notion_button.clicked.connect(self.to_notion)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete)
        buttons = QHBoxLayout()
        buttons.addWidget(save)
        buttons.addWidget(self.notion_button)
        buttons.addStretch()
        buttons.addWidget(delete)

        self.merge_target = QComboBox()
        merge = QPushButton("Merge into")
        merge.clicked.connect(self.merge)
        self.merge_target.setToolTip("If this is the same request as another entry, merge it into that one")
        merge_row = QHBoxLayout()
        merge_row.addWidget(merge)
        merge_row.addWidget(self.merge_target, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setOpenExternalLinks(True)

        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(theme.section("Asked in"))
        right.addWidget(self.mentions, 1)
        right.addWidget(theme.muted("Same request as another entry? Merge them so it's counted once."))
        right.addLayout(merge_row)
        right.addLayout(buttons)
        right.addWidget(self.status)
        self.editor = QWidget()
        self.editor.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(self.editor)
        splitter.setSizes([580, 420])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.refresh()

    def refresh(self, keep: int | None = None) -> None:
        keep = keep if keep is not None else self.current
        rows = self.store.features()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            values = [r["title"], str(r["mentions"]), (r["clients"] or "").replace(",", ", "),
                      (r["last_asked"] or "")[:10], r["status"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, r["id"])
                if col == 1:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, col, item)
            if r["id"] == keep:
                self.table.selectRow(i)
        self.table.setColumnWidth(2, 150)  # long client lists wrap instead of hiding Status
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        if not self.table.selectedItems() and rows:
            self.table.selectRow(0)
        self.select()
        self.new_count_changed.emit(sum(r["status"] == "new" for r in rows))

    def _selected_id(self) -> int | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def select(self) -> None:
        feature_id = self._selected_id()
        row = self.store.feature(feature_id) if feature_id is not None else None
        self.current = feature_id if row else None
        self.editor.setEnabled(row is not None)
        self.status.clear()
        if row is None:
            self.title.clear()
            self.detail.clear()
            self.mentions.setText("Requests appear here after a meeting where a client asks for something "
                                  "the product doesn't do yet. Type <code>fr:</code> in your notes to flag one.")
            self.merge_target.clear()
            return
        self.title.setText(row["title"])
        self.detail.setPlainText(row["detail"])
        self.status_box.setCurrentText(row["status"])
        self.mentions.setText(self._mentions_html(row["id"]))

        self.merge_target.clear()
        for other in self.store.features():
            if other["id"] != row["id"]:
                self.merge_target.addItem(other["title"], other["id"])

        self.notion_button.setVisible(self.notion.is_ready)
        self.notion_button.setText("Update in Notion" if row["notion_url"] else "Add to Notion")
        if row["notion_url"]:
            self.status.setText(f'<a href="{row["notion_url"]}">Open in Notion</a>')

    def _mentions_html(self, feature_id: int) -> str:
        lines = []
        for m in self.store.mentions(feature_id):
            who = m["requested_by"] or "someone"
            if m["client"]:
                who += f" ({m['client']})"
            quote = f"<br><i>“{_escape(m['quote'])}”</i>" if m["quote"] else ""
            at = f" at {m['timestamp']}" if m["timestamp"] else ""
            flag = "  ·  you flagged this" if m["flagged"] else ""
            lines.append(f"<b>{_escape(m['date'][:10])}</b>  {_escape(m['meeting'])}{at}<br>"
                         f"asked by {_escape(who)}{flag}{quote}")
        return "<br><br>".join(lines)

    def save(self) -> None:
        if self.current is None:
            return
        title = self.title.text().strip()
        if not title:
            self.status.setText("The request needs a name.")
            return
        self.store.update_feature(self.current, title=title, detail=self.detail.toPlainText().strip(),
                                  status=self.status_box.currentText())
        self.refresh(keep=self.current)
        self.status.setText("Saved.")

    def merge(self) -> None:
        target = self.merge_target.currentData()
        if self.current is None or target is None:
            return
        answer = QMessageBox.question(
            self, "Merge requests",
            f"Treat “{self.title.text()}” as the same request as “{self.merge_target.currentText()}”? "
            "Its meetings move to that entry and this one is removed.")
        if answer == QMessageBox.Yes:
            self.store.merge_features(keep=target, drop=self.current)
            self.current = None
            self.refresh(keep=target)
            self.status.setText("Merged.")

    def delete(self) -> None:
        if self.current is None:
            return
        answer = QMessageBox.question(self, "Delete request",
                                      f"Remove “{self.title.text()}” and its meeting history from the tracker?")
        if answer == QMessageBox.Yes:
            self.store.delete_feature(self.current)
            self.current = None
            self.refresh()

    def to_notion(self) -> None:
        """Creates or updates the request's row in the Feature requests database, off the UI thread."""
        self.save()
        row = self.store.feature(self.current)
        feature_id = row["id"]
        # everything that reads the store happens here, on the UI thread
        sync = self.notion.sync()
        try:
            parent = None if self.notion.features_ready else self.notion.features_parent()
        except RuntimeError as e:
            self.status.setText(str(e))
            return

        def push() -> tuple[str, str]:
            if parent:
                sync.features_db = sync.setup_features(sync.client, parent)
            url = sync.save_feature(row["title"], row["detail"], row["status"], row["mentions"],
                                    (row["clients"] or "").replace(",", ", "), row["last_asked"] or "")
            return sync.features_db, url

        def done(result: tuple[str, str]) -> None:
            database_id, url = result
            if parent:
                self.notion.remember_features_db(database_id)
            self.store.update_feature(feature_id, notion_url=url or "")
            self.refresh(keep=feature_id)
            self.notion_button.setEnabled(True)
            self.status.setText(f'Saved in Notion. <a href="{url}">Open it</a>' if url else "Saved in Notion.")

        def failed(message: str) -> None:
            self.notion_button.setEnabled(True)
            self.status.setText(f"Notion: {message}")

        self.notion_button.setEnabled(False)
        self.status.setText("Saving to Notion…")
        run_in_background(self, push, done, failed)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
