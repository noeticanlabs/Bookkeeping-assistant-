"""Legacy JSON persistence layer.

The runnable app now uses SQLite. These helpers remain for legacy import/tests and can
transparently read the sibling SQLite database after migration.
"""

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder


def _money(value: object) -> Decimal:
    return Decimal(str(value))


def save_bookkeeper(book: Bookkeeper, path: str | Path) -> None:
    target = Path(path)
    if target.suffix in {".sqlite", ".sqlite3", ".db"}:
        from sqlite_store import save_bookkeeper as save_sqlite
        save_sqlite(book, target)
        return
    data = {
        "vendor_bill_mode": book.vendor_bill_mode,
        "work_orders": [asdict(x) for x in book.work_orders.values()],
        "costs": [asdict(x) for x in book.costs.values()],
        "vendor_bills": [asdict(x) for x in book.vendor_bills.values()],
        "invoices": [asdict(x) for x in book.invoices.values()],
        "payments": [asdict(x) for x in book.payments.values()],
        "deposits": [asdict(x) for x in book.deposits.values()],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")


def load_bookkeeper(path: str | Path) -> Bookkeeper:
    source = Path(path)
    if source.suffix in {".sqlite", ".sqlite3", ".db"}:
        from sqlite_store import load_bookkeeper as load_sqlite
        return load_sqlite(source)
    if not source.exists():
        sqlite_path = source.with_suffix(".sqlite3")
        if sqlite_path.exists():
            from sqlite_store import load_bookkeeper as load_sqlite
            return load_sqlite(sqlite_path)
        return Bookkeeper()

    data = json.loads(source.read_text(encoding="utf-8"))
    book = Bookkeeper(vendor_bill_mode=data.get("vendor_bill_mode", "ask"))

    for row in data.get("work_orders", []):
        row["quoted_total"] = _money(row["quoted_total"]) if row.get("quoted_total") is not None else None
        book.add_work_order(WorkOrder(**row))
    for row in data.get("costs", []):
        row["amount"] = _money(row["amount"])
        book.add_cost(Cost(**row))
    for row in data.get("vendor_bills", []):
        row["amount"] = _money(row["amount"])
        row["amount_paid"] = _money(row.get("amount_paid", "0"))
        book.add_vendor_bill(VendorBill(**row))
    for row in data.get("invoices", []):
        row["total"] = _money(row["total"])
        row["amount_paid"] = _money(row.get("amount_paid", "0"))
        book.add_invoice(Invoice(**row))
    for row in data.get("payments", []):
        row["amount"] = _money(row["amount"])
        payment = Payment(**row)
        if payment.id in book.payments:
            raise ValueError("Duplicate payment ID in storage")
        book.payments[payment.id] = payment
    for row in data.get("deposits", []):
        row["amount"] = _money(row["amount"])
        row["processor_fee"] = _money(row.get("processor_fee", "0"))
        book.add_deposit(BankDeposit(**row))

    return book
