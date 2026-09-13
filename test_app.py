from decimal import Decimal
import pytest

from app import Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_vendor_cost_matches_to_work_order_and_updates_job_cost():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1842", "Smith", "Water heater", "complete", Decimal("2450")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("742.16"), "materials", reference="PO-9382"))
    assert book.job_cost("WO-1842") == Decimal("0")
    book.match_cost("C-1", "WO-1842")
    assert book.costs["C-1"].work_order_id == "WO-1842"
    assert book.job_cost("WO-1842") == Decimal("742.16")


def test_unknown_work_order_cannot_receive_cost():
    book = Bookkeeper()
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials"))
    with pytest.raises(ValueError):
        book.match_cost("C-1", "WO-NOT-REAL")


def test_unmatched_cost_is_flagged_for_review():
    book = Bookkeeper()
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials"))
    assert book.review()[0].kind == "unassigned_cost"


def test_cost_must_be_positive():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_cost(Cost("C-1", "Ferguson", Decimal("0"), "materials"))


def test_prepare_invoice_from_completed_quoted_job():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    invoice = book.prepare_invoice("WO-1")
    assert invoice.total == Decimal("2000")
    assert book.review_invoice("WO-1").status == "ready"


def test_matching_invoice_includes_job_cost_and_profit():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    result = book.review_invoice("WO-1")
    assert result.status == "ready"
    assert result.job_cost == Decimal("700")
    assert result.profit == Decimal("1300")


def test_payment_updates_invoice_balance():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400"), "INV-1"))
    assert book.invoices["INV-1"].balance_due == Decimal("600")
