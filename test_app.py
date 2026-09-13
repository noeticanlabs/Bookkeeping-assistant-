from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_processor_fee_explains_deposit_difference():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("970"), reference="PAY-1", processor_fee=Decimal("30")))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_difference("DEP-1") == Decimal("0")
    assert book.deposit_status("DEP-1") == "explained"
    assert not any(item.kind == "deposit_difference" for item in book.review())


def test_wrong_fee_keeps_real_difference():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("970"), reference="PAY-1", processor_fee=Decimal("20")))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_difference("DEP-1") == Decimal("-10")
    assert book.deposit_status("DEP-1") == "difference"


def test_negative_processor_fee_is_rejected():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_deposit(BankDeposit("DEP-1", Decimal("970"), processor_fee=Decimal("-1")))


def test_equal_deposit_without_fee_is_matched():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("1000"), reference="PAY-1"))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "matched"


def test_end_to_end_card_payment_chain():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    book.add_payment(Payment("PAY-1", Decimal("2000"), reference="INV-1"))
    book.accept_payment_match("PAY-1")
    book.add_deposit(BankDeposit("DEP-1", Decimal("1940"), reference="PAY-1", processor_fee=Decimal("60")))
    book.accept_deposit_match("DEP-1")
    assert book.invoices["INV-1"].payment_status == "paid"
    assert book.deposit_status("DEP-1") == "explained"
    assert book.job_profit("WO-1") == Decimal("1300")
