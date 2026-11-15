"""SQLite storage for rules, the approvals inbox, the activity log, tasks, email drafts,
contacts, the feature-request tracker and preferences."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .context import Prefs
from .models import Rule

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, data TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, rule_id TEXT, rule_name TEXT, meeting TEXT, at TEXT, status TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, rule_id TEXT, rule_name TEXT, meeting TEXT, effect TEXT,
    status TEXT DEFAULT 'pending', created_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, text TEXT, owner TEXT, due TEXT, meeting TEXT, created_at TEXT, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY, meeting TEXT, folder TEXT, recipient TEXT, to_addrs TEXT, reason TEXT,
    subject TEXT, body TEXT, audience TEXT, warning TEXT DEFAULT '', status TEXT DEFAULT 'draft',
    gmail_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (name TEXT PRIMARY KEY COLLATE NOCASE, email TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS features (
    id INTEGER PRIMARY KEY, title TEXT, detail TEXT DEFAULT '', status TEXT DEFAULT 'new',
    notion_url TEXT DEFAULT '', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS feature_mentions (
    id INTEGER PRIMARY KEY, feature_id INTEGER REFERENCES features(id), meeting TEXT, folder TEXT,
    date TEXT, requested_by TEXT, quote TEXT, timestamp TEXT, client TEXT, flagged INTEGER DEFAULT 0
);
"""

FEATURE_STATUSES = ["new", "planned", "in progress", "shipped", "declined"]

# columns added after the first release; added to existing databases on open
TASK_COLUMNS = {"due_date": "TEXT", "folder": "TEXT DEFAULT ''", "notion_url": "TEXT DEFAULT ''",
                "updated_at": "TEXT", "reminded_on": "TEXT DEFAULT ''"}
TASK_FIELDS = {"text", "owner", "due", "due_date", "status", "notion_url", "reminded_on"}
DRAFT_FIELDS = {"to_addrs", "subject", "body", "status", "gmail_id", "recipient", "warning"}
FEATURE_FIELDS = {"title", "detail", "status", "notion_url"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # the app does Gmail and Notion calls on worker threads that record their result here;
        # sqlite serialises access, and the UI waits for each call before touching that row
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(tasks)")}
        for column, kind in TASK_COLUMNS.items():
            if column not in existing:
                self.db.execute(f"ALTER TABLE tasks ADD COLUMN {column} {kind}")
        self.db.commit()

    # rules
    def rules(self) -> list[Rule]:
        rows = self.db.execute("SELECT data FROM rules ORDER BY position").fetchall()
        return [Rule.model_validate_json(r["data"]) for r in rows]

    def save_rule(self, rule: Rule) -> None:
        position = self.db.execute("SELECT position FROM rules WHERE id = ?", (rule.id,)).fetchone()
        if position is None:
            position = self.db.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM rules").fetchone()[0]
        else:
            position = position[0]
        self.db.execute(
            "INSERT OR REPLACE INTO rules (id, data, position) VALUES (?, ?, ?)",
            (rule.id, rule.model_dump_json(), position),
        )
        self.db.commit()

    def delete_rule(self, rule_id: str) -> None:
        self.db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self.db.commit()

    # activity log
    def log_run(self, rule: Rule, meeting: str, status: str, detail: str) -> None:
        self.db.execute(
            "INSERT INTO runs (rule_id, rule_name, meeting, at, status, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (rule.id, rule.name, meeting, now(), status, detail),
        )
        self.db.commit()

    def runs(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def runs_in_last_hour(self, rule_id: str) -> int:
        since = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        return self.db.execute(
            "SELECT COUNT(*) FROM runs WHERE rule_id = ? AND at >= ? AND status = 'done'", (rule_id, since)
        ).fetchone()[0]

    def last_run(self, rule_id: str) -> str | None:
        row = self.db.execute("SELECT MAX(at) FROM runs WHERE rule_id = ?", (rule_id,)).fetchone()
        return row[0]

    def run_count(self, rule_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM runs WHERE rule_id = ?", (rule_id,)).fetchone()[0]

    # approvals inbox
    def queue(self, rule: Rule, meeting: str, effect: dict) -> None:
        self.db.execute(
            "INSERT INTO outbox (rule_id, rule_name, meeting, effect, created_at) VALUES (?, ?, ?, ?, ?)",
            (rule.id, rule.name, meeting, json.dumps(effect), now()),
        )
        self.db.commit()

    def pending(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM outbox WHERE status = 'pending' ORDER BY id").fetchall()

    def decide(self, outbox_id: int, status: str) -> None:
        self.db.execute("UPDATE outbox SET status = ?, decided_at = ? WHERE id = ?", (status, now(), outbox_id))
        self.db.commit()

    # tasks
    def add_task(self, text: str, owner: str, due: str, meeting: str, *, due_date: str | None = None,
                 status: str = "open", folder: str = "") -> int | None:
        """Adds a task, or returns None if this meeting already has a live task with the same text.
        Scheduling a task that is only a suggestion so far turns the suggestion into the task."""
        duplicate = self.db.execute(
            "SELECT id, status FROM tasks WHERE meeting = ? AND lower(text) = lower(?) AND status != 'dismissed'",
            (meeting, text),
        ).fetchone()
        if duplicate:
            if duplicate["status"] == "suggested" and status == "open":
                self.update_task(duplicate["id"], status="open")
                return duplicate["id"]
            return None
        cur = self.db.execute(
            "INSERT INTO tasks (text, owner, due, due_date, meeting, folder, created_at, updated_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (text, owner, due, due_date, meeting, folder, now(), now(), status),
        )
        self.db.commit()
        return cur.lastrowid

    def tasks(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.db.execute("SELECT * FROM tasks WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        return self.db.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()

    def task(self, task_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def update_task(self, task_id: int, **fields) -> None:
        self._update("tasks", TASK_FIELDS, task_id, fields)

    def due_for_reminder(self, today: str) -> list[sqlite3.Row]:
        """Open tasks due today or earlier that haven't been reminded about today."""
        return self.db.execute(
            "SELECT * FROM tasks WHERE status = 'open' AND due_date IS NOT NULL AND due_date <= ? "
            "AND reminded_on != ? ORDER BY due_date",
            (today, today),
        ).fetchall()

    # email drafts
    def add_draft(self, meeting: str, folder: str, recipient: str, to: list[str], reason: str,
                  subject: str, body: str, audience: str, warning: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO drafts (meeting, folder, recipient, to_addrs, reason, subject, body, audience, warning, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (meeting, folder, recipient, json.dumps(to), reason, subject, body, audience, warning, now(), now()),
        )
        self.db.commit()
        return cur.lastrowid

    def drafts(self, include_closed: bool = False) -> list[sqlite3.Row]:
        where = "" if include_closed else "WHERE status NOT IN ('sent', 'discarded')"
        return self.db.execute(f"SELECT * FROM drafts {where} ORDER BY id DESC").fetchall()

    def draft(self, draft_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()

    def update_draft(self, draft_id: int, **fields) -> None:
        if "to_addrs" in fields and not isinstance(fields["to_addrs"], str):
            fields["to_addrs"] = json.dumps(fields["to_addrs"])
        self._update("drafts", DRAFT_FIELDS, draft_id, fields)

    # feature-request tracker: one row per capability, one mention per time a meeting asked for it
    def add_feature(self, title: str, detail: str = "") -> int:
        cur = self.db.execute("INSERT INTO features (title, detail, created_at, updated_at) VALUES (?, ?, ?, ?)",
                              (title, detail, now(), now()))
        self.db.commit()
        return cur.lastrowid

    def add_mention(self, feature_id: int, meeting: str, folder: str, date: str, requested_by: str,
                    quote: str, timestamp: str, client: str, flagged: bool = False) -> bool:
        """Records that a meeting asked for the feature. False if this meeting was already counted."""
        if self.db.execute("SELECT 1 FROM feature_mentions WHERE feature_id = ? AND folder = ? AND meeting = ?",
                           (feature_id, folder, meeting)).fetchone():
            return False
        self.db.execute(
            "INSERT INTO feature_mentions (feature_id, meeting, folder, date, requested_by, quote, timestamp, "
            "client, flagged) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (feature_id, meeting, folder, date, requested_by, quote, timestamp, client, int(flagged)),
        )
        self.db.execute("UPDATE features SET updated_at = ? WHERE id = ?", (now(), feature_id))
        self.db.commit()
        return True

    def features(self) -> list[sqlite3.Row]:
        """Every feature with how often and by whom it was asked, most asked first."""
        return self.db.execute(
            "SELECT f.*, COUNT(m.id) AS mentions, MAX(m.date) AS last_asked, "
            "GROUP_CONCAT(DISTINCT NULLIF(m.client, '')) AS clients "
            "FROM features f LEFT JOIN feature_mentions m ON m.feature_id = f.id "
            "GROUP BY f.id ORDER BY mentions DESC, last_asked DESC"
        ).fetchall()

    def feature(self, feature_id: int) -> sqlite3.Row | None:
        return next((r for r in self.features() if r["id"] == feature_id), None)

    def mentions(self, feature_id: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM feature_mentions WHERE feature_id = ? ORDER BY date DESC",
                               (feature_id,)).fetchall()

    def update_feature(self, feature_id: int, **fields) -> None:
        if "status" in fields and fields["status"] not in FEATURE_STATUSES:
            raise ValueError(f"Unknown status {fields['status']}")
        self._update("features", FEATURE_FIELDS, feature_id, fields)

    def merge_features(self, keep: int, drop: int) -> None:
        """Two tracker entries turned out to be the same request: move every mention to `keep`."""
        if keep == drop:
            return
        self.db.execute("UPDATE feature_mentions SET feature_id = ? WHERE feature_id = ?", (keep, drop))
        self.db.execute("DELETE FROM features WHERE id = ?", (drop,))
        self.db.commit()

    def delete_feature(self, feature_id: int) -> None:
        self.db.execute("DELETE FROM feature_mentions WHERE feature_id = ?", (feature_id,))
        self.db.execute("DELETE FROM features WHERE id = ?", (feature_id,))
        self.db.commit()

    # contacts, learned from attendee lists and from addresses typed into drafts
    def remember_contact(self, name: str, email: str) -> None:
        if name.strip() and "@" in email:
            self.db.execute("INSERT OR REPLACE INTO contacts (name, email) VALUES (?, ?)", (name.strip(), email.strip()))
            self.db.commit()

    def contacts(self) -> dict[str, str]:
        return {r["name"]: r["email"] for r in self.db.execute("SELECT name, email FROM contacts")}

    def _update(self, table: str, allowed: set[str], row_id: int, fields: dict) -> None:
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Can't update {', '.join(sorted(unknown))} on {table}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now(), row_id))
        self.db.commit()

    # preferences
    def prefs(self) -> Prefs:
        rows = dict(self.db.execute("SELECT key, value FROM prefs").fetchall())
        return Prefs(my_email=rows.get("my_email", ""), company_domain=rows.get("company_domain", ""))

    def get_pref(self, key: str) -> str:
        row = self.db.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
        return row[0] if row else ""

    def set_pref(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    def save_prefs(self, prefs: Prefs) -> None:
        for key, value in (("my_email", prefs.my_email), ("company_domain", prefs.company_domain)):
            self.db.execute("INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()
