"""Atomic approval/finalization unit of work.

Approval-required and zero-approval finalization paths share the same transaction
boundary. Financial state, provenance/correction state, audit history, and approval
state either commit together or roll back together.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app import Cost
from document_intake import record_approved_document
from sqlite_store import _connect, _json, initialize


def _audit_row(conn: sqlite3.Connection, event_type: str, evidence_id: str,
               payload: dict[str, object], actor: str) -> None:
    conn.execute(
        "INSERT INTO audit_events(event_id,event_type,evidence_id,actor,created_at,data) VALUES(?,?,?,?,?,?)",
        (
            f"AUD-{uuid.uuid4().hex[:16]}", event_type, evidence_id, actor,
            datetime.now(timezone.utc).isoformat(), _json(payload),
        ),
    )


def _persist_book(conn: sqlite3.Connection, book) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('vendor_bill_mode', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (book.vendor_bill_mode,),
    )
    groups = (
        ("work_orders", book.work_orders), ("costs", book.costs),
        ("vendor_bills", book.vendor_bills), ("invoices", book.invoices),
        ("payments", book.payments), ("deposits", book.deposits),
    )
    for table, items in groups:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table}(id,data) VALUES(?,?)",
            [(item_id, _json(asdict(item))) for item_id, item in items.items()],
        )


def _persist_corrections(conn: sqlite3.Connection, corrections) -> None:
    conn.execute("DELETE FROM corrections")
    conn.executemany(
        "INSERT INTO corrections(correction_id,status,original_cost_id,replacement_cost_id,data) VALUES(?,?,?,?,?)",
        [
            (c.correction_id, c.status, c.original_cost_id, c.replacement_cost_id, _json(asdict(c)))
            for c in corrections.corrections.values()
        ],
    )


def _restore_book(book, snapshot) -> None:
    book.vendor_bill_mode = snapshot.vendor_bill_mode
    for name in ("work_orders", "costs", "vendor_bills", "invoices", "payments", "deposits"):
        setattr(book, name, copy.deepcopy(getattr(snapshot, name)))


class AtomicApprovalUnitOfWork:
    def __init__(self, db_path: str | Path, book, provenance, corrections):
        self.db_path = Path(db_path)
        self.book = book
        self.provenance = provenance
        self.corrections = corrections
        initialize(self.db_path)

    def _snapshots(self):
        return (
            copy.deepcopy(self.book),
            copy.deepcopy(self.provenance.documents),
            copy.deepcopy(self.corrections.corrections),
        )

    def _restore(self, snapshots) -> None:
        book_before, provenance_before, corrections_before = snapshots
        _restore_book(self.book, book_before)
        self.provenance.documents = provenance_before
        self.corrections.corrections = corrections_before

    def _finalize_document(self, conn: sqlite3.Connection, payload: dict[str, object],
                           evidence_id: str, actor: str, *, request_id: str | None,
                           approver_ids: list[str]) -> object:
        if evidence_id not in self.provenance.documents:
            raise ValueError("Unknown source document")
        source = self.provenance.documents[evidence_id]
        if source.approved_record_id:
            raise ValueError("Source document is already bound to a bookkeeping record")
        treatment = payload["treatment"]
        if payload["record_type"] == "vendor_bill" and treatment == "ask" and self.book.vendor_bill_mode != "ask":
            treatment = self.book.vendor_bill_mode
        record = record_approved_document(
            self.book, record_id=payload["record_id"], vendor=payload["vendor"],
            amount=Decimal(payload["amount"]), reference=payload["reference"],
            work_order_id=payload["work_order_id"], record_type=payload["record_type"],
            treatment=treatment, linked_cost_id=payload["linked_cost_id"],
        )
        record_type = "cost" if record.id in self.book.costs else "vendor_bill"
        source.approved_record_type = record_type
        source.approved_record_id = record.id
        conn.execute(
            "UPDATE source_documents SET data=? WHERE evidence_id=?",
            (_json(asdict(source)), evidence_id),
        )
        _persist_book(conn, self.book)
        _audit_row(conn, "document.approved", evidence_id, {
            "result": {"record_type": record_type, "record_id": record.id},
            "approver_ids": approver_ids, "request_id": request_id,
            "workflow_direct": request_id is None,
        }, actor)
        return record

    def _finalize_correction(self, conn: sqlite3.Connection, correction_id: str,
                             actor: str, *, approver_ids: list[str],
                             workflow_direct: bool) -> Cost:
        if correction_id not in self.corrections.corrections:
            raise ValueError("Unknown correction")
        correction = self.corrections.corrections[correction_id]
        if correction.status != "pending":
            raise ValueError("Correction is no longer pending")
        original = self.book.costs[correction.original_cost_id]
        replacement = Cost(
            id=correction.replacement_cost_id,
            vendor=correction.proposed_vendor,
            amount=Decimal(correction.proposed_amount),
            kind=original.kind,
            work_order_id=correction.proposed_work_order_id,
            reference=correction.proposed_reference,
            correction_of=original.id,
        )
        self.book.supersede_cost(original.id, replacement)
        correction.status = "approved"
        correction.approved_by = actor
        correction.approved_at = datetime.now(timezone.utc).isoformat()
        _persist_book(conn, self.book)
        _persist_corrections(conn, self.corrections)
        _audit_row(conn, "cost.correction.finalized", f"CORRECTION:{correction_id}", {
            "replacement_cost_id": replacement.id,
            "approver_ids": approver_ids,
            "workflow_direct": workflow_direct,
        }, actor)
        return replacement

    def finalize_direct_document(self, payload: dict[str, object], evidence_id: str, actor: str):
        """Atomically post a zero-approval document workflow."""
        snapshots = self._snapshots()
        try:
            with _connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self._finalize_document(
                    conn, payload, evidence_id, actor, request_id=None, approver_ids=[]
                )
        except Exception:
            self._restore(snapshots)
            raise

    def finalize_direct_correction(self, correction_id: str, actor: str) -> Cost:
        """Atomically finalize a zero-approval correction workflow."""
        snapshots = self._snapshots()
        try:
            with _connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self._finalize_correction(
                    conn, correction_id, actor, approver_ids=[], workflow_direct=True
                )
        except Exception:
            self._restore(snapshots)
            raise

    def approve(self, request_id: str, user_id: str, actor: str) -> tuple[int, int, bool]:
        """Record one approval and atomically finalize if it satisfies the request."""
        snapshots = self._snapshots()

        try:
            with _connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM approval_requests WHERE request_id=?", (request_id,)).fetchone()
                if row is None:
                    raise ValueError("Unknown approval request")
                if row["status"] != "pending":
                    raise ValueError("Approval request is no longer pending")

                policy_row = conn.execute("SELECT data FROM approval_policy WHERE singleton=1").fetchone()
                prevent_self = True
                if policy_row:
                    prevent_self = bool(json.loads(policy_row["data"]).get("prevent_self_approval", True))
                if prevent_self and row["proposer_user_id"] == user_id:
                    raise ValueError("Proposer cannot approve their own request")

                try:
                    conn.execute(
                        "INSERT INTO approvals(request_id,user_id,approved_at) VALUES(?,?,?)",
                        (request_id, user_id, datetime.now(timezone.utc).isoformat()),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("This user has already approved this item") from exc

                count = int(conn.execute(
                    "SELECT COUNT(*) AS n FROM approvals WHERE request_id=?", (request_id,)
                ).fetchone()["n"])
                required = int(row["required_approvals"])
                payload = json.loads(row["payload"])
                subject_type = row["subject_type"]
                subject_id = row["subject_id"]

                _audit_row(conn, "approval.recorded", f"APPROVAL:{request_id}", {
                    "request_id": request_id, "subject_type": subject_type,
                    "subject_id": subject_id, "approval_number": count,
                    "required_approvals": required, "authenticated_user_id": user_id,
                }, actor)

                if count < required:
                    return count, required, False

                approver_ids = [r["user_id"] for r in conn.execute(
                    "SELECT user_id FROM approvals WHERE request_id=? ORDER BY approval_id", (request_id,)
                ).fetchall()]

                if subject_type == "document":
                    self._finalize_document(
                        conn, payload, subject_id, actor,
                        request_id=request_id, approver_ids=approver_ids,
                    )
                elif subject_type == "cost_correction":
                    self._finalize_correction(
                        conn, subject_id, actor,
                        approver_ids=approver_ids, workflow_direct=False,
                    )
                else:
                    raise ValueError("Unsupported approval subject")

                updated = conn.execute(
                    "UPDATE approval_requests SET status='approved' WHERE request_id=? AND status='pending'",
                    (request_id,),
                ).rowcount
                if updated != 1:
                    raise ValueError("Approval request is no longer pending")
                return count, required, True
        except Exception:
            self._restore(snapshots)
            raise
