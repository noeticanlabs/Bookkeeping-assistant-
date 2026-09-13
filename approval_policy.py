"""Configurable separation-of-duties and multi-person approval policy."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass
class ApprovalRequest:
    request_id: str
    subject_type: str
    subject_id: str
    proposer_user_id: str
    amount: str | None
    required_approvals: int
    payload: dict[str, object]
    status: str
    created_at: str


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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    proposer_user_id TEXT NOT NULL,
                    amount TEXT,
                    required_approvals INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(subject_type, subject_id)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    UNIQUE(request_id, user_id),
                    FOREIGN KEY(request_id) REFERENCES approval_requests(request_id)
                );
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
        if policy.second_approval_threshold is not None and policy.second_approval_threshold < 0:
            raise ValueError("Second-approval threshold cannot be negative")
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

    def create_request(self, subject_type: str, subject_id: str, proposer_user_id: str,
                       payload: dict[str, object], amount: Decimal | None = None) -> ApprovalRequest:
        policy = self.load()
        req = ApprovalRequest(
            request_id=f"APR-{uuid.uuid4().hex[:16]}",
            subject_type=subject_type,
            subject_id=subject_id,
            proposer_user_id=proposer_user_id,
            amount=str(amount) if amount is not None else None,
            required_approvals=policy.approvals_required(amount),
            payload=payload,
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO approval_requests(request_id,subject_type,subject_id,proposer_user_id,amount,required_approvals,payload,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (req.request_id, req.subject_type, req.subject_id, req.proposer_user_id, req.amount,
                     req.required_approvals, json.dumps(req.payload, default=str), req.status, req.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("This item already has an approval request") from exc
        return req

    def _from_row(self, row) -> ApprovalRequest:
        return ApprovalRequest(row["request_id"], row["subject_type"], row["subject_id"], row["proposer_user_id"],
                               row["amount"], int(row["required_approvals"]), json.loads(row["payload"]),
                               row["status"], row["created_at"])

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()
        return self._from_row(row) if row else None

    def for_subject(self, subject_type: str, subject_id: str) -> ApprovalRequest | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM approval_requests WHERE subject_type=? AND subject_id=?", (subject_type, subject_id)).fetchone()
        return self._from_row(row) if row else None

    def pending(self) -> list[ApprovalRequest]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM approval_requests WHERE status='pending' ORDER BY created_at").fetchall()
        return [self._from_row(row) for row in rows]

    def record_approval(self, request_id: str, user_id: str) -> tuple[ApprovalRequest, int, bool]:
        req = self.get(request_id)
        if req is None:
            raise ValueError("Unknown approval request")
        if req.status != "pending":
            raise ValueError("Approval request is no longer pending")
        policy = self.load()
        if policy.prevent_self_approval and req.proposer_user_id == user_id:
            raise ValueError("Proposer cannot approve their own request")
        try:
            with self._connect() as conn:
                conn.execute("INSERT INTO approvals(request_id,user_id,approved_at) VALUES(?,?,?)",
                             (request_id, user_id, datetime.now(timezone.utc).isoformat()))
                count = int(conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE request_id=?", (request_id,)).fetchone()["n"])
        except sqlite3.IntegrityError as exc:
            raise ValueError("This user has already approved this item") from exc
        return req, count, count >= req.required_approvals

    def complete(self, request_id: str) -> None:
        with self._connect() as conn:
            if conn.execute("UPDATE approval_requests SET status='approved' WHERE request_id=? AND status='pending'", (request_id,)).rowcount != 1:
                raise ValueError("Approval request is no longer pending")

    def reject(self, request_id: str) -> None:
        with self._connect() as conn:
            if conn.execute("UPDATE approval_requests SET status='rejected' WHERE request_id=? AND status='pending'", (request_id,)).rowcount != 1:
                raise ValueError("Approval request is no longer pending")

    def approver_ids(self, request_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT user_id FROM approvals WHERE request_id=? ORDER BY approval_id", (request_id,)).fetchall()
        return [row["user_id"] for row in rows]
