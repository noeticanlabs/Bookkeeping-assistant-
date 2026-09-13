from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder


def test_vendor_bill_tracks_unpaid_partial_and_paid():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("900")))
    bill = book.vendor_bills["BILL-1"]
    assert bill.payment_status == "unpaid"
    assert bill.balance_due == Decimal("900")

    book.pay_vendor_bill("BILL-1", Decimal("400"))
    assert bill.payment_status == "partial"
    assert bill.balance_due == Decimal("500")

    book.pay_vendor_bill("BILL-1", Decimal("500"))
    assert bill.payment_status == "paid"
    assert bill.balance_due == Decimal("0")


def test_accounts_payable_summary_counts_only_open_bills():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("900"), amount_paid=Decimal("400")))
    book.add_vendor_bill(VendorBill("BILL-2", "City", Decimal("75"), amount_paid=Decimal("75")))
    summary = book.accounts_payable_summary()
    assert summary["open_bill_count"] == 1
    assert summary["open_bill_balance"] == Decimal("500")


def test_vendor_bill_can_link_to_work_order():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater"))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("700"), work_order_id="WO-1"))
    assert book.vendor_bills["BILL-1"].work_order_id == "WO-1"


def test_vendor_bill_rejects_unknown_work_order():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("700"), work_order_id="WO-X"))


def test_vendor_payment_cannot_exceed_balance():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("700")))
    with pytest.raises(ValueError):
        book.pay_vendor_bill("BILL-1", Decimal("701"))


def test_attention_summary_includes_accounts_payable():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("900"), amount_paid=Decimal("400")))
    summary = book.attention_summary()
    assert summary["open_bill_count"] == 1
    assert summary["open_bill_balance"] == Decimal("500")


def test_clean_customer_cash_chain_still_works():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000")))
    book.add_payment(Payment("PAY-1", Decimal("2000"), reference="INV-1"))
    book.accept_payment_match("PAY-1")
    book.add_deposit(BankDeposit("DEP-1", Decimal("1940"), reference="PAY-1", processor_fee=Decimal("60")))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "explained"
