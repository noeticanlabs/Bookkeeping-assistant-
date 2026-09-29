"""Generic import paths shared by CSV and future external connectors."""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app import BankDeposit, Bookkeeper, Payment, WorkOrder


@dataclass
class ImportResult:
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def import_work_orders(book: Bookkeeper, work_orders: Iterable[WorkOrder]) -> ImportResult:
    result = ImportResult()
    for wo in work_orders:
        if wo.id in book.work_orders:
            result.skipped += 1
            continue
        try:
            book.add_work_order(wo)
            result.added += 1
        except ValueError as exc:
            result.errors.append(f"{wo.id}: {exc}")
    return result


def parse_work_orders_csv(text: str) -> tuple[list[WorkOrder], list[str]]:
    rows = csv.DictReader(io.StringIO(text))
    required = {"id", "customer", "description"}
    if not rows.fieldnames or not required.issubset(set(rows.fieldnames)):
        return [], ["CSV requires columns: id, customer, description. Optional: status, quoted_total"]

    work_orders: list[WorkOrder] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=2):
        try:
            wo_id = (row.get("id") or "").strip()
            customer = (row.get("customer") or "").strip()
            description = (row.get("description") or "").strip()
            if not wo_id or not customer or not description:
                raise ValueError("id, customer, and description are required")

            quoted_raw = (row.get("quoted_total") or "").strip()
            try:
                quoted_total = Decimal(quoted_raw) if quoted_raw else None
            except InvalidOperation as exc:
                raise ValueError("quoted_total must be a number") from exc
            if quoted_total is not None and quoted_total <= 0:
                raise ValueError("quoted_total must be greater than zero")

            work_orders.append(WorkOrder(
                id=wo_id,
                customer=customer,
                description=description,
                status=(row.get("status") or "complete").strip() or "complete",
                quoted_total=quoted_total,
            ))
        except ValueError as exc:
            errors.append(f"row {number}: {exc}")
    return work_orders, errors


def import_work_orders_csv(book: Bookkeeper, text: str) -> ImportResult:
    work_orders, parse_errors = parse_work_orders_csv(text)
    result = import_work_orders(book, work_orders)
    result.errors = parse_errors + result.errors
    return result


def import_payments(book: Bookkeeper, payments: Iterable[Payment]) -> ImportResult:
    """Import payments as unmatched evidence; never mutate an invoice during import."""
    result = ImportResult()
    for source in payments:
        if source.id in book.payments:
            result.skipped += 1
            continue
        reference = source.reference or source.invoice_id
        payment = Payment(source.id, source.amount, reference=reference)
        try:
            book.add_payment(payment)
            result.added += 1
        except ValueError as exc:
            result.errors.append(f"{source.id}: {exc}")
    return result


def import_deposits(book: Bookkeeper, deposits: Iterable[BankDeposit]) -> ImportResult:
    """Import deposits as unmatched evidence; reconciliation remains a separate action."""
    result = ImportResult()
    for source in deposits:
        if source.id in book.deposits:
            result.skipped += 1
            continue
        reference = source.reference or source.payment_id
        deposit = BankDeposit(
            id=source.id,
            amount=source.amount,
            reference=reference,
            processor_fee=source.processor_fee,
        )
        try:
            book.add_deposit(deposit)
            result.added += 1
        except ValueError as exc:
            result.errors.append(f"{source.id}: {exc}")
    return result


def _positive_decimal(raw: str, name: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def parse_payments_csv(text: str) -> tuple[list[Payment], list[str]]:
    rows = csv.DictReader(io.StringIO(text))
    required = {"id", "amount"}
    if not rows.fieldnames or not required.issubset(set(rows.fieldnames)):
        return [], ["Payment CSV requires columns: id, amount. Optional: reference"]

    payments: list[Payment] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=2):
        try:
            payment_id = (row.get("id") or "").strip()
            if not payment_id:
                raise ValueError("id is required")
            amount = _positive_decimal((row.get("amount") or "").strip(), "amount")
            payments.append(Payment(payment_id, amount, reference=(row.get("reference") or "").strip() or None))
        except ValueError as exc:
            errors.append(f"row {number}: {exc}")
    return payments, errors


def parse_deposits_csv(text: str) -> tuple[list[BankDeposit], list[str]]:
    rows = csv.DictReader(io.StringIO(text))
    required = {"id", "amount"}
    if not rows.fieldnames or not required.issubset(set(rows.fieldnames)):
        return [], ["Deposit CSV requires columns: id, amount. Optional: reference, processor_fee"]

    deposits: list[BankDeposit] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=2):
        try:
            deposit_id = (row.get("id") or "").strip()
            if not deposit_id:
                raise ValueError("id is required")
            amount = _positive_decimal((row.get("amount") or "").strip(), "amount")
            fee_raw = (row.get("processor_fee") or "0").strip() or "0"
            try:
                fee = Decimal(fee_raw)
            except InvalidOperation as exc:
                raise ValueError("processor_fee must be a number") from exc
            if fee < 0:
                raise ValueError("processor_fee cannot be negative")
            deposits.append(BankDeposit(
                deposit_id,
                amount,
                reference=(row.get("reference") or "").strip() or None,
                processor_fee=fee,
            ))
        except ValueError as exc:
            errors.append(f"row {number}: {exc}")
    return deposits, errors


def import_payments_csv(book: Bookkeeper, text: str) -> ImportResult:
    payments, parse_errors = parse_payments_csv(text)
    result = import_payments(book, payments)
    result.errors = parse_errors + result.errors
    return result


def import_deposits_csv(book: Bookkeeper, text: str) -> ImportResult:
    deposits, parse_errors = parse_deposits_csv(text)
    result = import_deposits(book, deposits)
    result.errors = parse_errors + result.errors
    return result
