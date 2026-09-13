"""Configurable separation-of-duties and multi-person approval policy."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass
class ApprovalPolicy:
    prevent_self_approval: bool = True
    second_approval_threshold: Decimal | None = None

    def approvals_required(self, amount: Decimal | None = None) -> int:
        if amount is not None and self.second_approval_threshold is not None and amount >= self.second_approval_threshold:
            return 2
        return 1


class ApprovalPolicyStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    data TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    UNIQUE(subject_type, subject_id, user_id)
                )
                """
            )

    def load(self) -> ApprovalPolicy:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM approval_policy WHERE singleton=1").fetchone()
        if not row:
            return ApprovalPolicy()
        data = json.loads(row["data"])
        threshold = data.get("second_approval_threshold")
        return ApprovalPolicy(
            prevent_self_approval=bool(data.get("prevent_self_approval", True)),
            second_approval_threshold=Decimal(str(threshold)) if threshold not in (None, "") else None,
        )

    def save(self, policy: ApprovalPolicy) -> None:
        data = {
            "prevent_self_approval": policy.prevent_self_approval,
            "second_approval_threshold": str(policy.second_approval_threshold) if policy.second_approval_threshold is not None else None,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approval_policy(singleton,data) VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET data=excluded.data",
                (json.dumps(data),),
            )

    def record_approval(self, subject_type: str, subject_id: str, user_id: str, approved_at: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO approvals(subject_type,subject_id,user_id,approved_at) VALUES(?,?,?,?)",
                    (subject_type, subject_id, user_id, approved_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("This user has already approved this item") from exc

    def approver_ids(self, subject_type: str, subject_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id FROM approvals WHERE subject_type=? AND subject_id=? ORDER BY approval_id",
                (subject_type, subject_id),
            ).fetchall()
        return [row["user_id"] for row in rows]

    def count(self, subject_type: str, subject_id: str) -> int:
        return len(self.approver_ids(subject_type, subject_id))
