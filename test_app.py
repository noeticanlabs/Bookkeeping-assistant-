from decimal import Decimal
import pytest

from app import Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_complete_job_without_invoice_is_flagged():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete"))
    result = book.review_invoice("WO-1")
    assert result.status == "needs_attention"


def test_prepare_invoice_from_completed_quoted_job():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    invoice = book.prepare_invoice("WO-1")
    assert invoice.id == "DRAFT-WO-1"
    assert invoice.customer == "Smith"
    assert invoice.total == Decimal("2000")
    assert invoice.status == "draft"
    assert book.review_invoice("WO-1").status == "ready"


def test_prepare_invoice_can_use_reviewed_total_when_no_quote():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Service call", "complete"))
    invoice = book.prepare_invoice("WO-1", Decimal("325"))
    assert invoice.total == Decimal("325")


def test_cannot_prepare_invoice_for_open_job():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "open", Decimal("500")))
    with pytest.raises(ValueError):
        book.prepare_invoice("WO-1")


def test_cannot_create_duplicate_invoice():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.prepare_invoice("WO-1")
    with pytest.raises(ValueError):
        book.prepare_invoice("WO-1")


def test_matching_invoice_is_ready():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    result = book.review_invoice("WO-1")
    assert result.status == "ready"
    assert result.job_cost == Decimal("700")
    assert result.profit == Decimal("1300")


def test_invoice_difference_needs_attention():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("550")))
    assert book.review_invoice("WO-1").status == "needs_attention"


def test_payment_updates_invoice_balance():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400"), "INV-1"))
    assert book.invoices["INV-1"].balance_due == Decimal("600")


def test_unmatched_payment_is_flagged():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("400")))
    assert book.review()[0].kind == "unmatched_payment"
