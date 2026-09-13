from decimal import Decimal
import pytest

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder


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


def test_duplicate_invoice_id_is_rejected():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("100")))
    with pytest.raises(ValueError):
        book.add_invoice(Invoice("INV-1", "WO-2", "Jones", Decimal("200")))


def test_duplicate_cost_id_is_rejected():
    book = Bookkeeper()
    book.add_cost(Cost("C-1", "Vendor", Decimal("10"), "materials"))
    with pytest.raises(ValueError):
        book.add_cost(Cost("C-1", "Vendor", Decimal("20"), "materials"))


def test_duplicate_vendor_bill_id_is_rejected():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Vendor", Decimal("10")))
    with pytest.raises(ValueError):
        book.add_vendor_bill(VendorBill("BILL-1", "Vendor", Decimal("20")))


def test_duplicate_deposit_id_is_rejected():
    book = Bookkeeper()
    book.add_deposit(BankDeposit("DEP-1", Decimal("100")))
    with pytest.raises(ValueError):
        book.add_deposit(BankDeposit("DEP-1", Decimal("200")))


def test_invoice_must_have_positive_total_and_valid_paid_amount():
    book = Bookkeeper()
    with pytest.raises(ValueError):
        book.add_invoice(Invoice("INV-0", "WO-1", "Smith", Decimal("0")))
    with pytest.raises(ValueError):
        book.add_invoice(Invoice("INV-2", "WO-1", "Smith", Decimal("100"), amount_paid=Decimal("101")))


def test_prelinked_payment_cannot_overpay_invoice():
    book = Bookkeeper()
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("100")))
    with pytest.raises(ValueError):
        book.add_payment(Payment("PAY-1", Decimal("101"), invoice_id="INV-1"))


def test_ask_mode_flags_untreated_vendor_bill():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("100")))
    assert any(item.kind == "vendor_bill_treatment" for item in book.review())


def test_create_cost_mode_adds_bill_once_to_job_cost():
    book = Bookkeeper(vendor_bill_mode="create_cost")
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("100"), work_order_id="WO-1"))
    bill = book.treat_vendor_bill("BILL-1")
    assert bill.treatment == "create_cost"
    assert book.job_cost("WO-1") == Decimal("100")
    with pytest.raises(ValueError):
        book.treat_vendor_bill("BILL-1")
    assert book.job_cost("WO-1") == Decimal("100")


def test_support_cost_mode_does_not_double_count_job_cost():
    book = Bookkeeper(vendor_bill_mode="support_cost")
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials", work_order_id="WO-1"))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("100"), work_order_id="WO-1"))
    bill = book.treat_vendor_bill("BILL-1", linked_cost_id="C-1")
    assert bill.treatment == "support_cost"
    assert bill.linked_cost_id == "C-1"
    assert book.job_cost("WO-1") == Decimal("100")


def test_overhead_vendor_bill_does_not_enter_job_cost():
    book = Bookkeeper()
    book.add_vendor_bill(VendorBill("BILL-1", "Insurance", Decimal("500")))
    bill = book.treat_vendor_bill("BILL-1", treatment="overhead")
    assert bill.treatment == "overhead"
    assert sum(book.job_cost(wo_id) for wo_id in book.work_orders) == Decimal("0")


def test_support_cost_rejects_different_work_order():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_work_order(WorkOrder("WO-2", "Jones", "Repair"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("100"), "materials", work_order_id="WO-1"))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("100"), work_order_id="WO-2"))
    with pytest.raises(ValueError):
        book.treat_vendor_bill("BILL-1", treatment="support_cost", linked_cost_id="C-1")
