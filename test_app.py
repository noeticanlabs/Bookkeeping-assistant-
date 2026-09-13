from decimal import Decimal
import pytest

from app import Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_payment_reference_suggests_invoice():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-100", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400"), reference="Card payment for INV-100"))
    assert book.suggest_payment_match("PAY-1") == "INV-100"


def test_accept_payment_match_updates_balance_and_partial_status():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-100", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400"), reference="INV-100"))
    book.accept_payment_match("PAY-1")
    invoice = book.invoices["INV-100"]
    assert invoice.balance_due == Decimal("600")
    assert invoice.payment_status == "partial"


def test_full_payment_marks_invoice_paid():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-100", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("1000"), reference="INV-100"))
    book.accept_payment_match("PAY-1")
    assert book.invoices["INV-100"].payment_status == "paid"


def test_payment_without_reference_remains_unmatched():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-100", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400")))
    assert book.suggest_payment_match("PAY-1") is None
    assert book.review()[0].kind == "unmatched_payment"


def test_payment_cannot_match_unknown_invoice():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("400")))
    with pytest.raises(ValueError):
        book.match_payment("PAY-1", "INV-NOT-REAL")


def test_payment_cannot_be_matched_twice():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-100", "WO-1", "Smith", Decimal("1000")))
    book.add_payment(Payment("PAY-1", Decimal("400")))
    book.match_payment("PAY-1", "INV-100")
    with pytest.raises(ValueError):
        book.match_payment("PAY-1", "INV-100")


def test_payment_must_be_positive():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_payment(Payment("PAY-1", Decimal("0")))


def test_cost_and_profit_chain_still_works():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    result = book.review_invoice("WO-1")
    assert result.job_cost == Decimal("700")
    assert result.profit == Decimal("1300")
