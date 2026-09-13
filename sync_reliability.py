"""Durable synchronization history and conservative outbound outbox.

Pull syncs are retryable because normalized external IDs are idempotent at the
Bookkeeper domain boundary. Outbound writes use an outbox. If transmission may
have reached the remote provider but the response is uncertain, the item moves
to `uncertain` and is NOT automatically retried.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SyncRun:
    run_id: str
    connector_id: str
    connector_name: str
    capability: str
    direction: str
    status: str
    started_at: str
    finished_at: str | None
    added: int
    skipped: int
    error: str | None


@dataclass(frozen=True)
class OutboxItem:
    item_id: str
    connector_id: str
    connector_name: str
    action: str
    object_type: str
    object_id: str
    idempotency_key: str
    status: str
    attempts: int
    external_id: str | None
    last_error: str | None
    created_at: str
    updated_at: str


class SyncReliabilityStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_runs (
                    run_id TEXT PRIMARY KEY,
                    connector_id TEXT NOT NULL,
                    connector_name TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    added INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    detail TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_sync_runs_connector_started
                    ON sync_runs(connector_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS sync_outbox (
                    item_id TEXT PRIMARY KEY,
                    connector_id TEXT NOT NULL,
                    connector_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    external_id TEXT,
                    last_error TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sync_outbox_status
                    ON sync_outbox(status, created_at);
                """
            )

    def start_run(self, connector_id: str, connector_name: str, capability: str,
                  direction: str = "pull") -> str:
        run_id = f"SYNC-{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_runs(run_id,connector_id,connector_name,capability,direction,status,started_at) "
                "VALUES(?,?,?,?,?,'running',?)",
                (run_id, connector_id, connector_name, capability, direction, _now()),
            )
        return run_id

    def finish_run(self, run_id: str, *, added: int = 0, skipped: int = 0,
                   detail: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sync_runs SET status='success',finished_at=?,added=?,skipped=?,error=NULL,detail=? "
                "WHERE run_id=? AND status='running'",
                (_now(), int(added), int(skipped), json.dumps(detail or {}), run_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Sync run is not active")

    def fail_run(self, run_id: str, error: str, *, detail: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sync_runs SET status='failed',finished_at=?,error=?,detail=? "
                "WHERE run_id=? AND status='running'",
                (_now(), str(error)[:2000], json.dumps(detail or {}), run_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Sync run is not active")

    def list_runs(self, limit: int = 100) -> list[SyncRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [SyncRun(
            run_id=row["run_id"], connector_id=row["connector_id"], connector_name=row["connector_name"],
            capability=row["capability"], direction=row["direction"], status=row["status"],
            started_at=row["started_at"], finished_at=row["finished_at"], added=row["added"],
            skipped=row["skipped"], error=row["error"],
        ) for row in rows]

    def last_success(self, connector_id: str, capability: str) -> SyncRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_runs WHERE connector_id=? AND capability=? AND status='success' "
                "ORDER BY finished_at DESC LIMIT 1", (connector_id, capability),
            ).fetchone()
        if not row:
            return None
        return SyncRun(
            run_id=row["run_id"], connector_id=row["connector_id"], connector_name=row["connector_name"],
            capability=row["capability"], direction=row["direction"], status=row["status"],
            started_at=row["started_at"], finished_at=row["finished_at"], added=row["added"],
            skipped=row["skipped"], error=row["error"],
        )

    def enqueue(self, connector_id: str, connector_name: str, action: str,
                object_type: str, object_id: str, payload: dict[str, Any]) -> OutboxItem:
        key = f"{connector_id}:{action}:{object_type}:{object_id}"
        now = _now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM sync_outbox WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                return self._outbox(existing)
            item_id = f"OUT-{uuid.uuid4().hex[:16]}"
            conn.execute(
                "INSERT INTO sync_outbox(item_id,connector_id,connector_name,action,object_type,object_id," 
                "idempotency_key,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)",
                (item_id, connector_id, connector_name, action, object_type, object_id, key,
                 json.dumps(payload, default=str, separators=(",", ":")), now, now),
            )
            row = conn.execute("SELECT * FROM sync_outbox WHERE item_id=?", (item_id,)).fetchone()
        return self._outbox(row)

    def begin_send(self, item_id: str) -> OutboxItem:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sync_outbox SET status='sending',attempts=attempts+1,updated_at=?,last_error=NULL "
                "WHERE item_id=? AND status IN ('pending','failed')", (_now(), item_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Outbox item is not retryable")
            row = conn.execute("SELECT * FROM sync_outbox WHERE item_id=?", (item_id,)).fetchone()
        return self._outbox(row)

    def mark_sent(self, item_id: str, external_id: str) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sync_outbox SET status='succeeded',external_id=?,updated_at=? "
                "WHERE item_id=? AND status='sending'", (external_id, _now(), item_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Outbox item is not sending")

    def mark_uncertain(self, item_id: str, error: str) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sync_outbox SET status='uncertain',last_error=?,updated_at=? "
                "WHERE item_id=? AND status='sending'", (str(error)[:2000], _now(), item_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Outbox item is not sending")

    def reset_uncertain(self, item_id: str) -> None:
        """Operator-only decision after checking the remote system."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sync_outbox SET status='pending',updated_at=? WHERE item_id=? AND status='uncertain'",
                (_now(), item_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Outbox item is not uncertain")

    def list_outbox(self, limit: int = 100) -> list[OutboxItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_outbox ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._outbox(row) for row in rows]

    def get_outbox(self, item_id: str) -> OutboxItem:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_outbox WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown outbox item")
        return self._outbox(row)

    @staticmethod
    def _outbox(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            item_id=row["item_id"], connector_id=row["connector_id"], connector_name=row["connector_name"],
            action=row["action"], object_type=row["object_type"], object_id=row["object_id"],
            idempotency_key=row["idempotency_key"], status=row["status"], attempts=row["attempts"],
            external_id=row["external_id"], last_error=row["last_error"], created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
