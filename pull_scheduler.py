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


SAFE_PULL_CAPABILITIES = frozenset({"work_orders.read", "payments.read", "deposits.read"})


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
                INSERT INTO pull_schedules(connector_id,connector_name,capability,enabled,interval_seconds,next_attempt_at)
                VALUES(?,?,?,1,?,?)
                ON CONFLICT(connector_id,capability) DO UPDATE SET
                    connector_name=excluded.connector_name,
                    interval_seconds=excluded.interval_seconds
                """,
                (connector_id, connector_name, capability, interval_seconds, _iso(next_at)),
            )
            # Normal discovery must preserve durable backoff state. An explicit
            # start_immediately request, however, is an operator/test instruction
            # to pull an existing schedule forward without resetting failures.
            if start_immediately:
                conn.execute(
                    "UPDATE pull_schedules SET next_attempt_at=? WHERE connector_id=? AND capability=?",
                    (_iso(next_at), connector_id, capability),
                )
        return self.get(connector_id, capability)

    def get(self, connector_id: str, capability: str) -> PullSchedule:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pull_schedules WHERE connector_id=? AND capability=?",
                (connector_id, capability),
            ).fetchone()
        if row is None:
            raise KeyError("Unknown pull schedule")
        return self._record(row)

    def list(self) -> list[PullSchedule]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pull_schedules ORDER BY connector_name, capability"
            ).fetchall()
        return [self._record(row) for row in rows]

    def due(self, now: datetime | None = None) -> list[PullSchedule]:
        now = now or _now_dt()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pull_schedules WHERE enabled=1 AND next_attempt_at<=? ORDER BY next_attempt_at",
                (_iso(now),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def mark_success(self, connector_id: str, capability: str, *, now: datetime | None = None) -> None:
        now = now or _now_dt()
        current = self.get(connector_id, capability)
        next_at = now + timedelta(seconds=current.interval_seconds)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pull_schedules
                SET consecutive_failures=0,last_attempt_at=?,last_success_at=?,last_error=NULL,next_attempt_at=?
                WHERE connector_id=? AND capability=?
                """,
                (_iso(now), _iso(now), _iso(next_at), connector_id, capability),
            )

    def mark_failure(self, connector_id: str, capability: str, error: str,
                     *, now: datetime | None = None, jitter: bool = True) -> int:
        now = now or _now_dt()
        current = self.get(connector_id, capability)
        failures = current.consecutive_failures + 1
        # 1m, 2m, 4m ... capped at 1h. Jitter prevents provider thundering-herd retries.
        delay = min(3600, 60 * (2 ** (failures - 1)))
        if jitter:
            delay = max(30, int(delay * random.uniform(0.8, 1.2)))
        next_at = now + timedelta(seconds=delay)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pull_schedules
                SET consecutive_failures=?,last_attempt_at=?,last_error=?,next_attempt_at=?
                WHERE connector_id=? AND capability=?
                """,
                (failures, _iso(now), str(error)[:2000], _iso(next_at), connector_id, capability),
            )
        return delay

    def set_enabled(self, connector_id: str, capability: str, enabled: bool) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE pull_schedules SET enabled=? WHERE connector_id=? AND capability=?",
                (1 if enabled else 0, connector_id, capability),
            )
            if cur.rowcount != 1:
                raise KeyError("Unknown pull schedule")

    @staticmethod
    def _record(row: sqlite3.Row) -> PullSchedule:
        return PullSchedule(
            connector_id=row["connector_id"], connector_name=row["connector_name"],
            capability=row["capability"], enabled=bool(row["enabled"]),
            interval_seconds=row["interval_seconds"], consecutive_failures=row["consecutive_failures"],
            next_attempt_at=row["next_attempt_at"], last_attempt_at=row["last_attempt_at"],
            last_success_at=row["last_success_at"], last_error=row["last_error"],
        )
