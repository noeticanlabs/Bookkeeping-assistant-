"""Durable scheduler for safe/idempotent connector pulls.

Only read capabilities are eligible. Outbound writes are intentionally excluded.
Retry state survives process restarts because schedules live in SQLite.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


SAFE_PULL_CAPABILITIES = frozenset({"work_orders.read", "payments.read", "deposits.read", "settlements.read"})


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class PullSchedule:
    connector_id: str
    connector_name: str
    capability: str
    enabled: bool
    interval_seconds: int
    consecutive_failures: int
    next_attempt_at: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_error: str | None


class PullScheduleStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pull_schedules (
                    connector_id TEXT NOT NULL,
                    connector_name TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    interval_seconds INTEGER NOT NULL DEFAULT 900,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    PRIMARY KEY(connector_id, capability)
                )
                """
            )

    def ensure(self, connector_id: str, connector_name: str, capability: str,
               *, interval_seconds: int = 900, start_immediately: bool = False) -> PullSchedule:
        if capability not in SAFE_PULL_CAPABILITIES:
            raise ValueError("Capability is not eligible for automatic pull scheduling")
        interval_seconds = max(60, int(interval_seconds))
        next_at = _now_dt() if start_immediately else _now_dt() + timedelta(seconds=interval_seconds)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pull_schedules(
                    connector_id,connector_name,capability,enabled,interval_seconds,
                    consecutive_failures,next_attempt_at,last_attempt_at,last_success_at,last_error
                ) VALUES(?,?,?,?,?,0,?,NULL,NULL,NULL)
                ON CONFLICT(connector_id,capability) DO UPDATE SET
                    connector_name=excluded.connector_name,
                    interval_seconds=excluded.interval_seconds,
                    enabled=1,
                    next_attempt_at=CASE
                        WHEN ?=1 AND pull_schedules.next_attempt_at > excluded.next_attempt_at
                        THEN excluded.next_attempt_at
                        ELSE pull_schedules.next_attempt_at
                    END
                """,
                (connector_id, connector_name, capability, 1, interval_seconds, _iso(next_at), 1 if start_immediately else 0),
            )
            row = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
        return self._record(row)

    def get(self, connector_id: str, capability: str) -> PullSchedule:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown pull schedule")
        return self._record(row)

    def due(self, now: datetime | None = None) -> list[PullSchedule]:
        now_text = _iso(now or _now_dt())
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pull_schedules WHERE enabled=1 AND next_attempt_at<=? ORDER BY next_attempt_at",
                (now_text,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list(self) -> list[PullSchedule]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pull_schedules ORDER BY connector_name, capability"
            ).fetchall()
        return [self._record(row) for row in rows]

    def mark_success(self, connector_id: str, capability: str, *, now: datetime | None = None) -> PullSchedule:
        now = now or _now_dt()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown pull schedule")
            next_at = now + timedelta(seconds=int(row["interval_seconds"]))
            conn.execute(
                """
                UPDATE pull_schedules
                SET consecutive_failures=0,last_attempt_at=?,last_success_at=?,last_error=NULL,next_attempt_at=?
                WHERE connector_id=? AND capability=?
                """,
                (_iso(now), _iso(now), _iso(next_at), connector_id, capability),
            )
            updated = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
        return self._record(updated)

    def mark_failure(self, connector_id: str, capability: str, error: str,
                     *, now: datetime | None = None, jitter: bool = True) -> PullSchedule:
        now = now or _now_dt()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown pull schedule")
            failures = int(row["consecutive_failures"]) + 1
            delay = min(3600, 60 * (2 ** (failures - 1)))
            if jitter:
                delay = max(60, int(delay * random.uniform(0.85, 1.15)))
            next_at = now + timedelta(seconds=delay)
            conn.execute(
                """
                UPDATE pull_schedules
                SET consecutive_failures=?,last_attempt_at=?,last_error=?,next_attempt_at=?
                WHERE connector_id=? AND capability=?
                """,
                (failures, _iso(now), str(error), _iso(next_at), connector_id, capability),
            )
            updated = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
        return self._record(updated)

    @staticmethod
    def _record(row: sqlite3.Row) -> PullSchedule:
        return PullSchedule(
            connector_id=row["connector_id"], connector_name=row["connector_name"],
            capability=row["capability"], enabled=bool(row["enabled"]),
            interval_seconds=int(row["interval_seconds"]),
            consecutive_failures=int(row["consecutive_failures"]),
            next_attempt_at=row["next_attempt_at"], last_attempt_at=row["last_attempt_at"],
            last_success_at=row["last_success_at"], last_error=row["last_error"],
        )
