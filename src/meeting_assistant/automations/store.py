"""SQLite storage for rules, the approvals inbox, the activity log, tasks and preferences."""

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
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

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
    def add_task(self, text: str, owner: str, due: str, meeting: str) -> None:
        self.db.execute(
            "INSERT INTO tasks (text, owner, due, meeting, created_at) VALUES (?, ?, ?, ?, ?)",
            (text, owner, due, meeting, now()),
        )
        self.db.commit()

    def tasks(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()

    # preferences
    def prefs(self) -> Prefs:
        rows = dict(self.db.execute("SELECT key, value FROM prefs").fetchall())
        return Prefs(my_email=rows.get("my_email", ""), company_domain=rows.get("company_domain", ""))

    def save_prefs(self, prefs: Prefs) -> None:
        for key, value in (("my_email", prefs.my_email), ("company_domain", prefs.company_domain)):
            self.db.execute("INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()
