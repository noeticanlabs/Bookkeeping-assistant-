from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, WorkOrder


def test_attention_summary_counts_real_work():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials"))
    book.add_payment(Payment("PAY-1", Decimal("200")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("200")))

    summary = book.attention_summary()
    assert summary["completed_unbilled"] == 1
    assert summary["unassigned_costs"] == 1
    assert summary["unmatched_payments"] == 1
    assert summary["bank_issues"] == 1


def test_attention_summary_tracks_open_invoice_balance():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000"), amount_paid=Decimal("400")))
    book.add_invoice(Invoice("INV-2", "WO-2", "Jones", Decimal("500"), amount_paid=Decimal("500")))
    summary = book.attention_summary()
    assert summary["open_invoice_count"] == 1
    assert summary["open_invoice_balance"] == Decimal("600")


def test_clean_chain_has_no_attention_items():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    book.add_payment(Payment("PAY-1", Decimal("2000"), reference="INV-1"))
    book.accept_payment_match("PAY-1")
    book.add_deposit(BankDeposit("DEP-1", Decimal("1940"), reference="PAY-1", processor_fee=Decimal("60")))
    book.accept_deposit_match("DEP-1")
    summary = book.attention_summary()
    assert summary["open_invoice_count"] == 0
    assert summary["open_invoice_balance"] == Decimal("0")
    assert summary["needs_attention"] == []


def test_processor_fee_explains_deposit_difference():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("970"), reference="PAY-1", processor_fee=Decimal("30")))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "explained"


def test_wrong_fee_keeps_real_difference():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("970"), reference="PAY-1", processor_fee=Decimal("20")))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "difference"


def test_negative_processor_fee_is_rejected():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_deposit(BankDeposit("DEP-1", Decimal("970"), processor_fee=Decimal("-1")))
