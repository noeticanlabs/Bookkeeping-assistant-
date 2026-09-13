from decimal import Decimal
import pytest

from app import Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_reference_suggests_known_work_order():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1842", "Smith", "Water heater"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("742.16"), "materials", reference="Invoice F-99 / WO-1842"))
    assert book.suggest_cost_match("C-1") == "WO-1842"


def test_accept_suggested_match_updates_job_cost():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1842", "Smith", "Water heater"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("742.16"), "materials", reference="WO-1842"))
    book.accept_cost_match("C-1")
    assert book.job_cost("WO-1842") == Decimal("742.16")


def test_no_reference_does_not_guess():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1842", "Smith", "Water heater"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("742.16"), "materials"))
    assert book.suggest_cost_match("C-1") is None


def test_ambiguous_reference_does_not_guess():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_work_order(WorkOrder("WO-2", "Smith", "Install"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials", reference="WO-1 / WO-2"))
    assert book.suggest_cost_match("C-1") is None


def test_unknown_work_order_cannot_receive_cost():
    book = Bookkeeper()
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials"))
    with pytest.raises(ValueError):
        book.match_cost("C-1", "WO-NOT-REAL")


def test_cost_must_be_positive():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_cost(Cost("C-1", "Ferguson", Decimal("0"), "materials"))


def test_invoice_review_uses_matched_job_cost():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    result = book.review_invoice("WO-1")
    assert result.job_cost == Decimal("700")
    assert result.profit == Decimal("1300")


def test_payment_updates_invoice_balance():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400"), "INV-1"))
    assert book.invoices["INV-1"].balance_due == Decimal("600")
