from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_deposit_reference_suggests_payment():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("1000"), reference="Bank deposit for PAY-1"))
    assert book.suggest_deposit_match("DEP-1") == "PAY-1"


def test_matching_equal_deposit_reconciles():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("1000"), reference="PAY-1"))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "matched"
    assert book.deposit_difference("DEP-1") == Decimal("0")


def test_deposit_difference_is_flagged():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("970"), reference="PAY-1"))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "difference"
    assert book.deposit_difference("DEP-1") == Decimal("-30")
    assert any(item.kind == "deposit_difference" for item in book.review())


def test_unmatched_deposit_is_flagged():
    book = Bookkeeper()
    book.add_deposit(BankDeposit("DEP-1", Decimal("500")))
    assert book.deposit_status("DEP-1") == "unmatched"
    assert book.review()[0].kind == "unmatched_deposit"


def test_deposit_cannot_match_unknown_payment():
    book = Bookkeeper()
    book.add_deposit(BankDeposit("DEP-1", Decimal("500")))
    with pytest.raises(ValueError):
        book.match_deposit("DEP-1", "PAY-NOT-REAL")


def test_deposit_must_be_positive():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_deposit(BankDeposit("DEP-1", Decimal("0")))


def test_end_to_end_job_to_bank_chain():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    book.add_payment(Payment("PAY-1", Decimal("2000"), reference="INV-1"))
    book.accept_payment_match("PAY-1")
    book.add_deposit(BankDeposit("DEP-1", Decimal("2000"), reference="PAY-1"))
    book.accept_deposit_match("DEP-1")
    assert book.invoices["INV-1"].payment_status == "paid"
    assert book.deposit_status("DEP-1") == "matched"
    assert book.job_profit("WO-1") == Decimal("1300")
