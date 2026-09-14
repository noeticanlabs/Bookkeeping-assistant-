"""Processor settlement/reconciliation model.

A settlement groups customer payments and explicit processor deductions, compares
that calculation to processor-reported payout evidence, then compares the payout
to one independently observed bank deposit.

No generic balancing adjustment exists by design. Unknown differences remain
visible for review.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable


ADJUSTMENT_KINDS = {"fee", "refund", "chargeback"}


@dataclass(frozen=True)
class SettlementEvidence:
    settlement_id: str
    provider: str
    reported_net: Decimal
    reference: str | None = None


@dataclass(frozen=True)
class Settlement:
    settlement_id: str
    provider: str
    reference: str | None
    reported_net: Decimal | None
    deposit_id: str | None
    created_at: str


@dataclass(frozen=True)
class SettlementAdjustment:
    adjustment_id: str
    settlement_id: str
    kind: str
    amount: Decimal
    reference: str | None


@dataclass(frozen=True)
class SettlementReconciliation:
    settlement_id: str
    status: str  # open | unknown | reconciled | difference
    gross_payments: Decimal
    fees: Decimal
    refunds: Decimal
    chargebacks: Decimal
    expected_net: Decimal
    reported_net: Decimal | None
    component_difference: Decimal | None
    actual_deposit: Decimal | None
    bank_difference: Decimal | None
    difference: Decimal | None
    payment_ids: tuple[str, ...]
    missing_payment_ids: tuple[str, ...]
    deposit_id: str | None


@dataclass
class SettlementImportResult:
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class SettlementStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settlements (
                    settlement_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    reference TEXT,
                    reported_net TEXT,
                    deposit_id TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settlement_payments (
                    settlement_id TEXT NOT NULL,
                    payment_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(settlement_id,payment_id),
                    FOREIGN KEY(settlement_id) REFERENCES settlements(settlement_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS settlement_adjustments (
                    adjustment_id TEXT PRIMARY KEY,
                    settlement_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    reference TEXT,
                    FOREIGN KEY(settlement_id) REFERENCES settlements(settlement_id) ON DELETE CASCADE
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(settlements)").fetchall()}
            if "reported_net" not in columns:
                conn.execute("ALTER TABLE settlements ADD COLUMN reported_net TEXT")

    def create(self, settlement_id: str, provider: str, reference: str | None = None,
               reported_net: Decimal | None = None) -> Settlement:
        settlement_id = settlement_id.strip()
        provider = provider.strip()
        if not settlement_id or not provider:
            raise ValueError("Settlement ID and provider are required")
        if reported_net is not None:
            reported_net = Decimal(reported_net)
            if reported_net <= 0:
                raise ValueError("Reported settlement amount must be greater than zero")
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO settlements(settlement_id,provider,reference,reported_net,deposit_id,created_at) VALUES(?,?,?,?,?,?)",
                    (settlement_id, provider, reference.strip() if reference else None,
                     str(reported_net) if reported_net is not None else None, None, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate settlement ID") from exc
        return Settlement(settlement_id, provider, reference.strip() if reference else None, reported_net, None, created_at)

    def import_evidence(self, evidences: Iterable[SettlementEvidence]) -> SettlementImportResult:
        result = SettlementImportResult()
        for evidence in evidences:
            try:
                reported = Decimal(evidence.reported_net)
                if reported <= 0:
                    raise ValueError("Reported settlement amount must be greater than zero")
                try:
                    existing = self.get(evidence.settlement_id)
                except ValueError:
                    self.create(evidence.settlement_id, evidence.provider, evidence.reference, reported)
                    result.added += 1
                    continue

                same_provider = existing.provider == evidence.provider
                same_reference = (existing.reference or None) == (evidence.reference or None)
                if existing.reported_net is None and same_provider:
                    with self._connect() as conn:
                        conn.execute(
                            "UPDATE settlements SET reference=?, reported_net=? WHERE settlement_id=?",
                            (evidence.reference, str(reported), evidence.settlement_id),
                        )
                    result.added += 1
                elif same_provider and same_reference and existing.reported_net == reported:
                    result.skipped += 1
                else:
                    raise ValueError("Imported settlement evidence conflicts with existing settlement state")
            except ValueError as exc:
                result.errors.append(f"{evidence.settlement_id}: {exc}")
        return result

    def get(self, settlement_id: str) -> Settlement:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM settlements WHERE settlement_id=?", (settlement_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown settlement")
        return self._settlement(row)

    def list(self) -> list[Settlement]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM settlements ORDER BY created_at DESC").fetchall()
        return [self._settlement(row) for row in rows]

    def add_payment(self, settlement_id: str, payment_id: str, book) -> None:
        self.get(settlement_id)
        payment_id = payment_id.strip()
        if payment_id not in book.payments:
            raise ValueError("Unknown payment")
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO settlement_payments(settlement_id,payment_id) VALUES(?,?)",
                    (settlement_id, payment_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Payment is already assigned to a settlement") from exc

    def add_adjustment(self, adjustment_id: str, settlement_id: str, kind: str,
                       amount: Decimal, reference: str | None = None) -> SettlementAdjustment:
        self.get(settlement_id)
        adjustment_id = adjustment_id.strip()
        kind = kind.strip().lower()
        amount = Decimal(amount)
        if not adjustment_id:
            raise ValueError("Adjustment ID is required")
        if kind not in ADJUSTMENT_KINDS:
            raise ValueError("Adjustment kind must be fee, refund, or chargeback")
        if amount <= 0:
            raise ValueError("Adjustment amount must be greater than zero")
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO settlement_adjustments(adjustment_id,settlement_id,kind,amount,reference) VALUES(?,?,?,?,?)",
                    (adjustment_id, settlement_id, kind, str(amount), reference.strip() if reference else None),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate settlement adjustment ID") from exc
        return SettlementAdjustment(adjustment_id, settlement_id, kind, amount, reference.strip() if reference else None)

    def link_deposit(self, settlement_id: str, deposit_id: str, book) -> Settlement:
        settlement = self.get(settlement_id)
        deposit_id = deposit_id.strip()
        if deposit_id not in book.deposits:
            raise ValueError("Unknown bank deposit")
        deposit = book.deposits[deposit_id]
        if deposit.payment_id:
            raise ValueError("Bank deposit is directly matched to a payment; remove that single-payment match before using aggregate settlement reconciliation")
        if settlement.deposit_id and settlement.deposit_id != deposit_id:
            raise ValueError("Settlement is already linked to a bank deposit")
        try:
            with self._connect() as conn:
                conn.execute("UPDATE settlements SET deposit_id=? WHERE settlement_id=?", (deposit_id, settlement_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Bank deposit is already assigned to another settlement") from exc
        return self.get(settlement_id)

    def payment_ids(self, settlement_id: str) -> list[str]:
        self.get(settlement_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payment_id FROM settlement_payments WHERE settlement_id=? ORDER BY payment_id",
                (settlement_id,),
            ).fetchall()
        return [row["payment_id"] for row in rows]

    def adjustments(self, settlement_id: str) -> list[SettlementAdjustment]:
        self.get(settlement_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM settlement_adjustments WHERE settlement_id=? ORDER BY adjustment_id",
                (settlement_id,),
            ).fetchall()
        return [
            SettlementAdjustment(
                row["adjustment_id"], row["settlement_id"], row["kind"],
                Decimal(row["amount"]), row["reference"],
            )
            for row in rows
        ]

    def reconcile(self, settlement_id: str, book) -> SettlementReconciliation:
        settlement = self.get(settlement_id)
        payment_ids = self.payment_ids(settlement_id)
        missing = tuple(pid for pid in payment_ids if pid not in book.payments)
        gross = sum((book.payments[pid].amount for pid in payment_ids if pid in book.payments), Decimal("0"))
        adjustments = self.adjustments(settlement_id)
        fees = sum((a.amount for a in adjustments if a.kind == "fee"), Decimal("0"))
        refunds = sum((a.amount for a in adjustments if a.kind == "refund"), Decimal("0"))
        chargebacks = sum((a.amount for a in adjustments if a.kind == "chargeback"), Decimal("0"))
        expected_net = gross - fees - refunds - chargebacks

        reported_net = settlement.reported_net
        component_difference = reported_net - expected_net if reported_net is not None else None
        target_net = reported_net if reported_net is not None else expected_net

        actual: Decimal | None = None
        bank_difference: Decimal | None = None
        if settlement.deposit_id and settlement.deposit_id in book.deposits:
            actual = book.deposits[settlement.deposit_id].amount
            bank_difference = actual - target_net

        if missing or (settlement.deposit_id and settlement.deposit_id not in book.deposits):
            status = "unknown"
        elif settlement.deposit_id is not None and not payment_ids:
            status = "unknown"
        elif component_difference is not None and component_difference != 0:
            status = "difference"
        elif settlement.deposit_id is None:
            status = "open"
        elif bank_difference == 0:
            status = "reconciled"
        else:
            status = "difference"

        unresolved_difference = component_difference if component_difference not in {None, Decimal("0")} else bank_difference
        return SettlementReconciliation(
            settlement_id=settlement_id,
            status=status,
            gross_payments=gross,
            fees=fees,
            refunds=refunds,
            chargebacks=chargebacks,
            expected_net=expected_net,
            reported_net=reported_net,
            component_difference=component_difference,
            actual_deposit=actual,
            bank_difference=bank_difference,
            difference=unresolved_difference,
            payment_ids=tuple(payment_ids),
            missing_payment_ids=missing,
            deposit_id=settlement.deposit_id,
        )

    @staticmethod
    def _settlement(row: sqlite3.Row) -> Settlement:
        return Settlement(
            settlement_id=row["settlement_id"], provider=row["provider"],
            reference=row["reference"],
            reported_net=Decimal(row["reported_net"]) if row["reported_net"] is not None else None,
            deposit_id=row["deposit_id"], created_at=row["created_at"],
        )
