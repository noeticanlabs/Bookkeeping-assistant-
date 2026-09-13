from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder


def test_near_term_position_is_open_ar_minus_open_ap():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000"), status="issued", amount_paid=Decimal("500")))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("900"), amount_paid=Decimal("200")))
    position = book.near_term_position()
    assert position["expected_in"] == Decimal("1500")
    assert position["owed_out"] == Decimal("700")
    assert position["net_position"] == Decimal("800")


def test_paid_items_do_not_affect_near_term_position():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("500"), status="issued", amount_paid=Decimal("500")))
    book.add_vendor_bill(VendorBill("BILL-1", "City", Decimal("75"), amount_paid=Decimal("75")))
    assert book.near_term_position()["net_position"] == Decimal("0")


def test_attention_summary_includes_near_term_position():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000"), status="issued", amount_paid=Decimal("250")))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("400")))
    summary = book.attention_summary()
    assert summary["open_invoice_balance"] == Decimal("750")
    assert summary["open_bill_balance"] == Decimal("400")
    assert summary["near_term_net_position"] == Decimal("350")


def test_vendor_bill_tracks_unpaid_partial_and_paid():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("900")))
    bill = book.vendor_bills["BILL-1"]
    book.pay_vendor_bill("BILL-1", Decimal("400"))
    assert bill.payment_status == "partial"
    book.pay_vendor_bill("BILL-1", Decimal("500"))
    assert bill.payment_status == "paid"


def test_vendor_payment_cannot_exceed_balance():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("700")))
    with pytest.raises(ValueError):
        book.pay_vendor_bill("BILL-1", Decimal("701"))


def test_clean_customer_cash_chain_still_works():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", "complete", Decimal("2000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("700"), "materials", reference="WO-1"))
    book.accept_cost_match("C-1")
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2000"), status="issued"))
    book.add_payment(Payment("PAY-1", Decimal("2000"), reference="INV-1"))
    book.accept_payment_match("PAY-1")
    book.add_deposit(BankDeposit("DEP-1", Decimal("1940"), reference="PAY-1", processor_fee=Decimal("60")))
    book.accept_deposit_match("DEP-1")
    assert book.deposit_status("DEP-1") == "explained"
