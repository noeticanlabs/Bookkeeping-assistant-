"""Generic work-order import path shared by CSV and future field-service connectors."""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app import Bookkeeper, WorkOrder


@dataclass
class ImportResult:
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def import_work_orders(book: Bookkeeper, work_orders: Iterable[WorkOrder]) -> ImportResult:
    """Import normalized work orders without weakening core duplicate protection."""
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
    """Parse the smallest portable CSV contract for field-service exports."""
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

            work_orders.append(
                WorkOrder(
                    id=wo_id,
                    customer=customer,
                    description=description,
                    status=(row.get("status") or "complete").strip() or "complete",
                    quoted_total=quoted_total,
                )
            )
        except ValueError as exc:
            errors.append(f"row {number}: {exc}")
    return work_orders, errors


def import_work_orders_csv(book: Bookkeeper, text: str) -> ImportResult:
    work_orders, parse_errors = parse_work_orders_csv(text)
    result = import_work_orders(book, work_orders)
    result.errors = parse_errors + result.errors
    return result
