"""Small economic-relationship layer for Bookkeeper Assistant.

Relationships already encoded directly in core records are derived at read time.
Only extra/candidate/manual links are persisted, avoiding duplicate authoritative state.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RecordLink:
    link_id: str
    from_type: str
    from_id: str
    relationship: str
    to_type: str
    to_id: str
    status: str = "verified"
    source: str = "system"
    evidence_id: str | None = None
    confidence: float | None = None
    created_at: str | None = None


class RecordLinkStore:
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
                CREATE TABLE IF NOT EXISTS record_links (
                    link_id TEXT PRIMARY KEY,
                    from_type TEXT NOT NULL,
                    from_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    to_type TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_id TEXT,
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    UNIQUE(from_type,from_id,relationship,to_type,to_id,source)
                )
                """
            )

    def add(self, *, from_type: str, from_id: str, relationship: str,
            to_type: str, to_id: str, status: str = "candidate",
            source: str = "manual", evidence_id: str | None = None,
            confidence: float | None = None) -> RecordLink:
        fields = [from_type, from_id, relationship, to_type, to_id, source]
        if any(not str(value).strip() for value in fields):
            raise ValueError("Relationship fields are required")
        if status not in {"candidate", "verified", "rejected"}:
            raise ValueError("Invalid relationship status")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ValueError("Relationship confidence must be between 0 and 1")
        created_at = datetime.now(timezone.utc).isoformat()
        link = RecordLink(
            link_id=f"LINK-{uuid.uuid4().hex[:12].upper()}",
            from_type=from_type.strip(), from_id=from_id.strip(),
            relationship=relationship.strip(), to_type=to_type.strip(), to_id=to_id.strip(),
            status=status, source=source.strip(), evidence_id=evidence_id,
            confidence=float(confidence) if confidence is not None else None,
            created_at=created_at,
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO record_links(
                        link_id,from_type,from_id,relationship,to_type,to_id,status,source,evidence_id,confidence,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (link.link_id, link.from_type, link.from_id, link.relationship,
                     link.to_type, link.to_id, link.status, link.source,
                     link.evidence_id, link.confidence, link.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Relationship already exists") from exc
        return link

    def list(self, *, status: str | None = None) -> list[RecordLink]:
        query = "SELECT * FROM record_links"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._record(row) for row in rows]

    def set_status(self, link_id: str, status: str) -> RecordLink:
        if status not in {"candidate", "verified", "rejected"}:
            raise ValueError("Invalid relationship status")
        with self._connect() as conn:
            cur = conn.execute("UPDATE record_links SET status=? WHERE link_id=?", (status, link_id))
            if cur.rowcount != 1:
                raise ValueError("Unknown relationship")
            row = conn.execute("SELECT * FROM record_links WHERE link_id=?", (link_id,)).fetchone()
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> RecordLink:
        return RecordLink(
            link_id=row["link_id"], from_type=row["from_type"], from_id=row["from_id"],
            relationship=row["relationship"], to_type=row["to_type"], to_id=row["to_id"],
            status=row["status"], source=row["source"], evidence_id=row["evidence_id"],
            confidence=row["confidence"], created_at=row["created_at"],
        )


def derived_relationships(book) -> list[RecordLink]:
    """Return verified relationships already encoded by authoritative core fields."""
    links: list[RecordLink] = []

    def add(from_type: str, from_id: str, relationship: str, to_type: str, to_id: str):
        links.append(RecordLink(
            link_id=f"DERIVED:{from_type}:{from_id}:{relationship}:{to_type}:{to_id}",
            from_type=from_type, from_id=from_id, relationship=relationship,
            to_type=to_type, to_id=to_id, status="verified", source="core",
        ))

    for cost in book.costs.values():
        if cost.work_order_id:
            add("cost", cost.id, "belongs_to", "work_order", cost.work_order_id)
        if cost.correction_of:
            add("cost", cost.id, "corrects", "cost", cost.correction_of)

    for bill in book.vendor_bills.values():
        if bill.work_order_id:
            add("vendor_bill", bill.id, "belongs_to", "work_order", bill.work_order_id)
        if bill.linked_cost_id:
            relation = "supports" if bill.treatment == "support_cost" else "creates_cost"
            add("vendor_bill", bill.id, relation, "cost", bill.linked_cost_id)

    for invoice in book.invoices.values():
        add("invoice", invoice.id, "generated_by", "work_order", invoice.work_order_id)

    for payment in book.payments.values():
        if payment.invoice_id:
            add("payment", payment.id, "settles", "invoice", payment.invoice_id)

    for deposit in book.deposits.values():
        if deposit.payment_id:
            add("deposit", deposit.id, "contains", "payment", deposit.payment_id)

    return links


def all_relationships(book, store: RecordLinkStore) -> list[RecordLink]:
    return derived_relationships(book) + store.list()
