from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_payment_cannot_overpay_invoice():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("100")))
    book.add_payment(Payment("PAY-1", Decimal("101")))
    with pytest.raises(ValueError):
        book.match_payment("PAY-1", "INV-1")


def test_unknown_prelinked_invoice_is_flagged():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("50"), invoice_id="INV-MISSING"))
    assert any(item.kind == "unmatched_payment" for item in book.review())


def test_duplicate_payment_id_cannot_double_count_invoice():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("100")))
    book.add_payment(Payment("PAY-1", Decimal("40"), invoice_id="INV-1"))
    with pytest.raises(ValueError):
        book.add_payment(Payment("PAY-1", Decimal("40"), invoice_id="INV-1"))
    assert book.invoices["INV-1"].amount_paid == Decimal("40")


def test_reference_matching_uses_whole_id_not_prefix():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("C-1", "Vendor", Decimal("10"), "materials", reference="WO-10"))
    assert book.suggest_cost_match("C-1") is None


def test_same_payment_cannot_reconcile_multiple_deposits():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("100")))
    book.add_deposit(BankDeposit("DEP-2", Decimal("100")))
    book.match_deposit("DEP-1", "PAY-1")
    with pytest.raises(ValueError):
        book.match_deposit("DEP-2", "PAY-1")


def test_draft_invoice_is_not_counted_as_expected_receivable():
    book = Bookkeeper()
    book.add_invoice(Invoice("DRAFT-WO-1", "WO-1", "Smith", Decimal("500"), status="draft"))
    assert book.near_term_position()["expected_in"] == Decimal("0")
