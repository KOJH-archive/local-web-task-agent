from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AgentDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    read_only INTEGER NOT NULL DEFAULT 1,
                    allowed_domains_json TEXT NOT NULL DEFAULT '[]',
                    selected_models_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    control TEXT NOT NULL DEFAULT 'run',
                    retry_of INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(retry_of) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    step_no INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action_json TEXT NOT NULL DEFAULT '{}',
                    observation_json TEXT NOT NULL DEFAULT '{}',
                    message TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    step_no INTEGER NOT NULL,
                    action_json TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    decided_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id, step_no);
                CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id, status);
                CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
                """
            )

    def create_task(
        self, command: str, read_only: bool, allowed_domains: list[str], retry_of: int | None = None
    ) -> int:
        now = utc_now()
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO tasks
                (command,status,read_only,allowed_domains_json,retry_of,created_at,updated_at)
                VALUES (?, 'queued', ?, ?, ?, ?, ?)""",
                (command, int(read_only), json.dumps(allowed_domains), retry_of, now, now),
            )
            if cur.lastrowid is None:
                raise RuntimeError("SQLite did not return a task id")
            task_id = cur.lastrowid
        self.log_event(task_id, "info", "task_created", {"retry_of": retry_of})
        return task_id

    def update_task(self, task_id: int, **values: Any) -> None:
        allowed = {
            "status", "selected_models_json", "plan_json", "result_json", "error", "control"
        }
        bad = set(values) - allowed
        if bad:
            raise ValueError(f"Unsupported task fields: {bad}")
        encoded = dict(values)
        for key in ("selected_models_json", "plan_json", "result_json"):
            if key in encoded and not isinstance(encoded[key], str):
                encoded[key] = json.dumps(encoded[key], ensure_ascii=False)
        encoded["updated_at"] = utc_now()
        clauses = ", ".join(f"{key}=?" for key in encoded)
        with self._lock, self.connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {clauses} WHERE id=?", (*encoded.values(), task_id)
            )

    def add_step(
        self,
        task_id: int,
        step_no: int,
        kind: str,
        status: str,
        action: dict[str, Any] | None = None,
        observation: dict[str, Any] | None = None,
        message: str = "",
    ) -> int:
        now = utc_now()
        finished = now if status in {"completed", "failed", "blocked", "skipped"} else None
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO steps
                (task_id,step_no,kind,status,action_json,observation_json,message,started_at,finished_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    step_no,
                    kind,
                    status,
                    json.dumps(action or {}, ensure_ascii=False),
                    json.dumps(observation or {}, ensure_ascii=False),
                    message,
                    now,
                    finished,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("SQLite did not return a step id")
            return cur.lastrowid

    def request_approval(
        self, task_id: int, step_no: int, action: dict[str, Any], risk: str, reason: str
    ) -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO approvals
                (task_id,step_no,action_json,risk,reason,status,created_at)
                VALUES (?,?,?,?,?,'pending',?)""",
                (task_id, step_no, json.dumps(action, ensure_ascii=False), risk, reason, utc_now()),
            )
            if cur.lastrowid is None:
                raise RuntimeError("SQLite did not return an approval id")
            approval_id = cur.lastrowid
        self.update_task(task_id, status="awaiting_approval")
        self.log_event(task_id, "warning", "approval_requested", {"approval_id": approval_id, "reason": reason})
        return approval_id

    def decide_approval(self, approval_id: int, approved: bool) -> None:
        status = "approved" if approved else "denied"
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT task_id FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row:
                raise KeyError(f"Approval {approval_id} not found")
            conn.execute(
                "UPDATE approvals SET status=?, decided_at=? WHERE id=? AND status='pending'",
                (status, utc_now(), approval_id),
            )
        self.log_event(int(row["task_id"]), "info", "approval_decided", {"approval_id": approval_id, "status": status})

    def log_event(self, task_id: int | None, level: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO events(task_id,level,event_type,payload_json,created_at) VALUES (?,?,?,?,?)",
                (task_id, level, event_type, json.dumps(payload, ensure_ascii=False), utc_now()),
            )

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._decode_task(row) if row else None

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode_task(row) for row in rows]

    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM steps WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return [self._decode_row(row, ("action_json", "observation_json")) for row in rows]

    def list_approvals(self, task_id: int, pending_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM approvals WHERE task_id=?"
        if pending_only:
            query += " AND status='pending'"
        query += " ORDER BY id"
        with self.connect() as conn:
            rows = conn.execute(query, (task_id,)).fetchall()
        return [self._decode_row(row, ("action_json",)) for row in rows]

    def list_events(self, task_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit)
            ).fetchall()
        return [self._decode_row(row, ("payload_json",)) for row in reversed(rows)]

    @staticmethod
    def _decode_row(row: sqlite3.Row, json_fields: tuple[str, ...]) -> dict[str, Any]:
        item = dict(row)
        for key in json_fields:
            item[key.removesuffix("_json")] = json.loads(item.pop(key) or "{}")
        return item

    def _decode_task(self, row: sqlite3.Row) -> dict[str, Any]:
        item = self._decode_row(
            row,
            ("allowed_domains_json", "selected_models_json", "plan_json", "result_json"),
        )
        item["read_only"] = bool(item["read_only"])
        return item
