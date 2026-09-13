"""Declarative workflow approval rules for company-specific financial controls."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ACTION_TYPES = ("document_cost", "vendor_bill", "cost_correction")


@dataclass(frozen=True)
class WorkflowRule:
    action_type: str
    min_amount: Decimal | None
    approvals_required: int


DEFAULT_RULES = (
    WorkflowRule("document_cost", None, 1),
    WorkflowRule("vendor_bill", None, 1),
    WorkflowRule("cost_correction", None, 1),
)


class WorkflowPolicyStore:
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
                CREATE TABLE IF NOT EXISTS workflow_rules (
                    action_type TEXT NOT NULL,
                    min_amount TEXT,
                    approvals_required INTEGER NOT NULL,
                    PRIMARY KEY(action_type, min_amount)
                )
                """
            )
            for rule in DEFAULT_RULES:
                conn.execute(
                    "INSERT OR IGNORE INTO workflow_rules(action_type,min_amount,approvals_required) VALUES(?,?,?)",
                    (rule.action_type, None, rule.approvals_required),
                )

    def rules(self, action_type: str | None = None) -> list[WorkflowRule]:
        with self._connect() as conn:
            if action_type:
                rows = conn.execute(
                    "SELECT action_type,min_amount,approvals_required FROM workflow_rules WHERE action_type=?",
                    (action_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT action_type,min_amount,approvals_required FROM workflow_rules"
                ).fetchall()
        result = [
            WorkflowRule(
                row["action_type"],
                Decimal(row["min_amount"]) if row["min_amount"] is not None else None,
                int(row["approvals_required"]),
            ) for row in rows
        ]
        return sorted(result, key=lambda r: (r.action_type, r.min_amount is not None, r.min_amount or Decimal("0")))

    def set_rules(self, action_type: str, bands: list[tuple[Decimal | None, int]]) -> None:
        if action_type not in ACTION_TYPES:
            raise ValueError("Unknown workflow action type")
        normalized: list[tuple[Decimal | None, int]] = []
        for minimum, approvals in bands:
            if minimum is not None and minimum < 0:
                raise ValueError("Amount threshold cannot be negative")
            if approvals not in {0, 1, 2}:
                raise ValueError("Approvals required must be 0, 1, or 2")
            normalized.append((minimum, approvals))
        if not any(minimum is None for minimum, _ in normalized):
            raise ValueError("Each action type needs a base rule")
        if len({minimum for minimum, _ in normalized}) != len(normalized):
            raise ValueError("Duplicate amount thresholds are not allowed")
        with self._connect() as conn:
            conn.execute("DELETE FROM workflow_rules WHERE action_type=?", (action_type,))
            conn.executemany(
                "INSERT INTO workflow_rules(action_type,min_amount,approvals_required) VALUES(?,?,?)",
                [(action_type, str(minimum) if minimum is not None else None, approvals) for minimum, approvals in normalized],
            )

    def approvals_required(self, action_type: str, amount: Decimal | None) -> int:
        candidates = self.rules(action_type)
        if not candidates:
            raise ValueError("No workflow rule configured for this action")
        applicable = [r for r in candidates if r.min_amount is None or (amount is not None and amount >= r.min_amount)]
        if not applicable:
            raise ValueError("No applicable workflow rule")
        return max(applicable, key=lambda r: r.min_amount if r.min_amount is not None else Decimal("-1")).approvals_required
