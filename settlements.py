"""Processor settlement/reconciliation model.

A settlement can be assembled manually from bookkeeping evidence or reconstructed
from immutable processor balance transactions. Processor composition and bank
evidence remain separate. Unknown or unsupported movements remain visible; there
is no generic balancing adjustment.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable


ADJUSTMENT_KINDS = {"fee", "refund", "chargeback"}
COMPONENT_KINDS = {"payment", "refund", "chargeback", "fee", "other"}


@dataclass(frozen=True)
class SettlementComponentEvidence:
    component_id: str
    kind: str
    amount: Decimal
    fee: Decimal
    net: Decimal
    currency: str
    reporting_category: str
    transaction_type: str
    source_id: str | None = None
    payment_id: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SettlementEvidence:
    settlement_id: str
    provider: str
    reported_net: Decimal
    reference: str | None = None
    components: tuple[SettlementComponentEvidence, ...] = ()
    composition_complete: bool = False
    composition_note: str | None = None


@dataclass(frozen=True)
class Settlement:
    settlement_id: str
    provider: str
    reference: str | None
    reported_net: Decimal | None
    deposit_id: str | None
    created_at: str
    composition_complete: bool = False
    composition_note: str | None = None


@dataclass(frozen=True)
class SettlementAdjustment:
    adjustment_id: str
    settlement_id: str
    kind: str
    amount: Decimal
    reference: str | None


@dataclass(frozen=True)
class SettlementComponent:
    component_id: str
    settlement_id: str
    kind: str
    amount: Decimal
    fee: Decimal
    net: Decimal
    currency: str
    reporting_category: str
    transaction_type: str
    source_id: str | None
    payment_id: str | None
    description: str | None


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
    processor_component_count: int = 0
    processor_component_net: Decimal | None = None
    composition_complete: bool = False
    composition_note: str | None = None
    unmapped_payment_component_ids: tuple[str, ...] = ()
    unclassified_component_ids: tuple[str, ...] = ()
    category_totals: tuple[tuple[str, Decimal], ...] = ()


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
                    created_at TEXT NOT NULL,
                    composition_complete INTEGER NOT NULL DEFAULT 0,
                    composition_note TEXT
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

                CREATE TABLE IF NOT EXISTS settlement_components (
                    component_id TEXT PRIMARY KEY,
                    settlement_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    fee TEXT NOT NULL,
                    net TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    reporting_category TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    source_id TEXT,
                    payment_id TEXT,
                    description TEXT,
                    FOREIGN KEY(settlement_id) REFERENCES settlements(settlement_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_settlement_components_settlement
                    ON settlement_components(settlement_id);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(settlements)").fetchall()}
            if "reported_net" not in columns:
                conn.execute("ALTER TABLE settlements ADD COLUMN reported_net TEXT")
            if "composition_complete" not in columns:
                conn.execute("ALTER TABLE settlements ADD COLUMN composition_complete INTEGER NOT NULL DEFAULT 0")
            if "composition_note" not in columns:
                conn.execute("ALTER TABLE settlements ADD COLUMN composition_note TEXT")

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
                    """INSERT INTO settlements(
                        settlement_id,provider,reference,reported_net,deposit_id,created_at,
                        composition_complete,composition_note
                    ) VALUES(?,?,?,?,?,?,0,NULL)""",
                    (settlement_id, provider, reference.strip() if reference else None,
                     str(reported_net) if reported_net is not None else None, None, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate settlement ID") from exc
        return Settlement(settlement_id, provider, reference.strip() if reference else None, reported_net, None, created_at)

    @staticmethod
    def _validate_component(component: SettlementComponentEvidence) -> SettlementComponentEvidence:
        component_id = component.component_id.strip()
        kind = component.kind.strip().lower()
        currency = component.currency.strip().lower()
        reporting_category = component.reporting_category.strip()
        transaction_type = component.transaction_type.strip()
        amount = Decimal(component.amount)
        fee = Decimal(component.fee)
        net = Decimal(component.net)
        if not component_id:
            raise ValueError("Processor component ID is required")
        if kind not in COMPONENT_KINDS:
            raise ValueError(f"Unsupported processor component kind: {kind}")
        if not currency:
            raise ValueError("Processor component currency is required")
        if net != amount - fee:
            raise ValueError(
                f"Processor component {component_id} violates Stripe amount - fee = net identity"
            )
        return SettlementComponentEvidence(
            component_id=component_id,
            kind=kind,
            amount=amount,
            fee=fee,
            net=net,
            currency=currency,
            reporting_category=reporting_category,
            transaction_type=transaction_type,
            source_id=component.source_id.strip() if component.source_id else None,
            payment_id=component.payment_id.strip() if component.payment_id else None,
            description=component.description.strip() if component.description else None,
        )

    def import_evidence(self, evidences: Iterable[SettlementEvidence], book=None) -> SettlementImportResult:
        """Import processor-reported payouts and immutable component evidence.

        Existing component IDs must be byte-for-byte equivalent in accounting
        meaning. Re-syncs are idempotent; contradictory processor evidence is
        rejected instead of overwritten.
        """
        result = SettlementImportResult()
        for evidence in evidences:
            try:
                settlement_id = evidence.settlement_id.strip()
                provider = evidence.provider.strip()
                reported = Decimal(evidence.reported_net)
                if not settlement_id or not provider:
                    raise ValueError("Settlement ID and provider are required")
                if reported <= 0:
                    raise ValueError("Reported settlement amount must be greater than zero")
                components = tuple(self._validate_component(c) for c in evidence.components)
                component_ids = [c.component_id for c in components]
                if len(set(component_ids)) != len(component_ids):
                    raise ValueError("Processor composition contains duplicate component IDs")

                changed = False
                try:
                    existing = self.get(settlement_id)
                except ValueError:
                    existing = self.create(settlement_id, provider, evidence.reference, reported)
                    changed = True

                same_provider = existing.provider == provider
                same_reference = (existing.reference or None) == (evidence.reference or None)
                if not same_provider:
                    raise ValueError("Imported settlement evidence conflicts with existing settlement provider")
                if existing.reported_net not in {None, reported}:
                    raise ValueError("Imported settlement amount conflicts with existing settlement state")
                if existing.reference and evidence.reference and not same_reference:
                    raise ValueError("Imported settlement reference conflicts with existing settlement state")

                with self._connect() as conn:
                    if existing.reported_net is None or (existing.reference is None and evidence.reference):
                        conn.execute(
                            "UPDATE settlements SET reference=COALESCE(reference,?), reported_net=? WHERE settlement_id=?",
                            (evidence.reference, str(reported), settlement_id),
                        )
                        changed = True

                    incoming_note = evidence.composition_note.strip() if evidence.composition_note else None
                    desired_complete = bool(existing.composition_complete or evidence.composition_complete)
                    desired_note = incoming_note or existing.composition_note
                    if desired_complete != existing.composition_complete or desired_note != existing.composition_note:
                        conn.execute(
                            "UPDATE settlements SET composition_complete=?, composition_note=? WHERE settlement_id=?",
                            (1 if desired_complete else 0, desired_note, settlement_id),
                        )
                        changed = True

                    for component in components:
                        row = conn.execute(
                            "SELECT * FROM settlement_components WHERE component_id=?",
                            (component.component_id,),
                        ).fetchone()
                        values = (
                            settlement_id, component.kind, str(component.amount), str(component.fee), str(component.net),
                            component.currency, component.reporting_category, component.transaction_type,
                            component.source_id, component.payment_id, component.description,
                        )
                        if row is None:
                            conn.execute(
                                """INSERT INTO settlement_components(
                                    component_id,settlement_id,kind,amount,fee,net,currency,
                                    reporting_category,transaction_type,source_id,payment_id,description
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (component.component_id, *values),
                            )
                            changed = True
                            continue
                        existing_values = (
                            row["settlement_id"], row["kind"], row["amount"], row["fee"], row["net"],
                            row["currency"], row["reporting_category"], row["transaction_type"],
                            row["source_id"], row["payment_id"], row["description"],
                        )
                        if existing_values != values:
                            raise ValueError(
                                f"Processor component {component.component_id} conflicts with previously imported immutable evidence"
                            )

                if book is not None:
                    for component in components:
                        if component.payment_id and component.payment_id in book.payments:
                            self.ensure_payment(settlement_id, component.payment_id, book)

                if changed:
                    result.added += 1
                else:
                    result.skipped += 1
            except ValueError as exc:
                result.errors.append(f"{getattr(evidence, 'settlement_id', 'unknown')}: {exc}")
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

    def ensure_payment(self, settlement_id: str, payment_id: str, book) -> None:
        self.get(settlement_id)
        payment_id = payment_id.strip()
        if payment_id not in book.payments:
            raise ValueError("Unknown payment")
        with self._connect() as conn:
            owner = conn.execute(
                "SELECT settlement_id FROM settlement_payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if owner is not None:
                if owner["settlement_id"] == settlement_id:
                    return
                raise ValueError("Payment is already assigned to a settlement")
            conn.execute(
                "INSERT INTO settlement_payments(settlement_id,payment_id) VALUES(?,?)",
                (settlement_id, payment_id),
            )

    def add_payment(self, settlement_id: str, payment_id: str, book) -> None:
        self.ensure_payment(settlement_id, payment_id, book)

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

    def components(self, settlement_id: str) -> list[SettlementComponent]:
        self.get(settlement_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM settlement_components WHERE settlement_id=? ORDER BY component_id",
                (settlement_id,),
            ).fetchall()
        return [
            SettlementComponent(
                component_id=row["component_id"], settlement_id=row["settlement_id"], kind=row["kind"],
                amount=Decimal(row["amount"]), fee=Decimal(row["fee"]), net=Decimal(row["net"]),
                currency=row["currency"], reporting_category=row["reporting_category"],
                transaction_type=row["transaction_type"], source_id=row["source_id"],
                payment_id=row["payment_id"], description=row["description"],
            )
            for row in rows
        ]

    def reconcile(self, settlement_id: str, book) -> SettlementReconciliation:
        settlement = self.get(settlement_id)
        stored_payment_ids = self.payment_ids(settlement_id)
        missing = tuple(pid for pid in stored_payment_ids if pid not in book.payments)
        adjustments = self.adjustments(settlement_id)
        components = self.components(settlement_id)

        processor_component_net: Decimal | None = None
        unmapped_component_ids: tuple[str, ...] = ()
        unclassified_component_ids: tuple[str, ...] = ()
        category_totals: tuple[tuple[str, Decimal], ...] = ()

        if components:
            processor_component_net = sum((c.net for c in components), Decimal("0"))
            expected_net = processor_component_net
            gross = sum((c.amount for c in components if c.kind == "payment" and c.amount > 0), Decimal("0"))
            embedded_fees = sum((c.fee for c in components if c.fee > 0), Decimal("0"))
            standalone_fees = sum((-c.net for c in components if c.kind == "fee" and c.net < 0), Decimal("0"))
            fees = embedded_fees + standalone_fees
            refunds = sum((-c.amount for c in components if c.kind == "refund" and c.amount < 0), Decimal("0"))
            chargebacks = sum((-c.amount for c in components if c.kind == "chargeback" and c.amount < 0), Decimal("0"))

            category_map: dict[str, Decimal] = {}
            for component in components:
                category = component.reporting_category or component.transaction_type or "unknown"
                category_map[category] = category_map.get(category, Decimal("0")) + component.net
            category_totals = tuple(sorted(category_map.items()))

            unmapped_component_ids = tuple(
                c.component_id for c in components
                if c.kind == "payment" and (not c.payment_id or c.payment_id not in book.payments)
            )
            unclassified_component_ids = tuple(c.component_id for c in components if c.kind == "other")
            resolved_component_payments = {
                c.payment_id for c in components
                if c.payment_id and c.payment_id in book.payments
            }
            payment_ids = tuple(sorted(set(stored_payment_ids) | resolved_component_payments))
        else:
            payment_ids = tuple(stored_payment_ids)
            gross = sum((book.payments[pid].amount for pid in stored_payment_ids if pid in book.payments), Decimal("0"))
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

        composition_attempted = bool(components) or settlement.composition_note is not None
        if missing or (settlement.deposit_id and settlement.deposit_id not in book.deposits):
            status = "unknown"
        elif composition_attempted:
            if not settlement.composition_complete or not components:
                status = "unknown"
            elif component_difference is not None and component_difference != 0:
                status = "difference"
            elif unmapped_component_ids or unclassified_component_ids:
                status = "unknown"
            elif settlement.deposit_id is None:
                status = "open"
            elif bank_difference == 0:
                status = "reconciled"
            else:
                status = "difference"
        elif not stored_payment_ids:
            status = "open"
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
            payment_ids=payment_ids,
            missing_payment_ids=missing,
            deposit_id=settlement.deposit_id,
            processor_component_count=len(components),
            processor_component_net=processor_component_net,
            composition_complete=settlement.composition_complete,
            composition_note=settlement.composition_note,
            unmapped_payment_component_ids=unmapped_component_ids,
            unclassified_component_ids=unclassified_component_ids,
            category_totals=category_totals,
        )

    @staticmethod
    def _settlement(row: sqlite3.Row) -> Settlement:
        return Settlement(
            settlement_id=row["settlement_id"], provider=row["provider"],
            reference=row["reference"],
            reported_net=Decimal(row["reported_net"]) if row["reported_net"] is not None else None,
            deposit_id=row["deposit_id"], created_at=row["created_at"],
            composition_complete=bool(row["composition_complete"]),
            composition_note=row["composition_note"],
        )
