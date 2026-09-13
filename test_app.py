from decimal import Decimal

from app import Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_complete_job_without_invoice_is_flagged():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete"))
    result = book.review_invoice("WO-1")
    assert result.status == "needs_attention"
    assert "Completed work order has no invoice" in result.issues


def test_matching_invoice_is_ready():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    result = book.review_invoice("WO-1")
    assert result.status == "ready"
    assert result.job_cost == Decimal("700")
    assert result.profit == Decimal("1300")
    assert result.issues == []


def test_invoice_difference_needs_attention():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("550")))
    result = book.review_invoice("WO-1")
    assert result.status == "needs_attention"
    assert "Invoice total differs from quoted total" in result.issues


def test_wrong_customer_needs_attention():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_invoice(Invoice("INV-1", "WO-1", "Jones", Decimal("500")))
    assert book.review_invoice("WO-1").status == "needs_attention"


def test_job_cost_and_profit():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", "WO-1"))
    book.add_cost(Cost("C-2", "City", Decimal("75"), "permit", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    assert book.job_cost("WO-1") == Decimal("775")
    assert book.job_profit("WO-1") == Decimal("1225")


def test_payment_updates_invoice_balance():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400"), "INV-1"))
    assert book.invoices["INV-1"].balance_due == Decimal("600")


def test_unmatched_payment_is_flagged():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("400")))
    assert book.review()[0].kind == "unmatched_payment"
