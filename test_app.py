from decimal import Decimal

from app import Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_complete_job_without_invoice_is_flagged():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete"))
    assert book.review()[0].kind == "unbilled_job"


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


def test_invoice_difference_is_flagged():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("550")))
    assert book.review()[0].kind == "invoice_total"
