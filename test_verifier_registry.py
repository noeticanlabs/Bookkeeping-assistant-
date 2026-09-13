from decimal import Decimal

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder
from business_intelligence import exception_queue
from verifier_registry import FAIL, PASS, UNKNOWN, default_registry


def _result(results, verifier_id, object_id):
    return next(r for r in results if r.verifier_id == verifier_id and r.object_id == object_id)


def test_invoice_balance_verifier_detects_state_drift():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000"), status="issued"))
    book.add_payment(Payment("PAY-1", Decimal("250"), "INV-1"))

    registry = default_registry()
    healthy = _result(registry.run(book), "V-INVOICE-BALANCE", "INV-1")
    assert healthy.status == PASS
    assert healthy.evidence["linked_payment_total"] == "250"
    assert healthy.evidence["computed_remaining_receivable"] == "750"

    # Simulate legacy/import/storage drift that normal write paths would reject.
    book.invoices["INV-1"].amount_paid = Decimal("200")
    failed = _result(registry.run(book), "V-INVOICE-BALANCE", "INV-1")
    assert failed.status == FAIL
    assert failed.review_required is True


def test_processor_settlement_verifier_pass_fail_and_unknown():
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))
    book.add_payment(Payment("PAY-2", Decimal("100")))
    book.add_deposit(BankDeposit("DEP-PASS", Decimal("97"), "PAY-1", processor_fee=Decimal("3")))
    book.add_deposit(BankDeposit("DEP-FAIL", Decimal("96"), "PAY-2", processor_fee=Decimal("3")))
    book.add_deposit(BankDeposit("DEP-UNKNOWN", Decimal("50")))

    results = default_registry().run(book)

    assert _result(results, "V-PROCESSOR-SETTLEMENT", "DEP-PASS").status == PASS
    failed = _result(results, "V-PROCESSOR-SETTLEMENT", "DEP-FAIL")
    assert failed.status == FAIL
    assert failed.evidence["difference"] == "-1"
    assert _result(results, "V-PROCESSOR-SETTLEMENT", "DEP-UNKNOWN").status == UNKNOWN


def test_work_order_link_verifier_distinguishes_unknown_from_contradiction():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-UNASSIGNED", "Supply", Decimal("10"), "material"))
    book.add_cost(Cost("COST-BROKEN", "Supply", Decimal("20"), "material", "WO-1"))

    # Simulate a broken legacy reference after valid creation.
    book.costs["COST-BROKEN"].work_order_id = "WO-MISSING"

    results = default_registry().run(book)
    assert _result(results, "V-WORK-ORDER-LINK", "COST-UNASSIGNED").status == UNKNOWN
    assert _result(results, "V-WORK-ORDER-LINK", "COST-BROKEN").status == FAIL


def test_vendor_bill_treatment_verifier_detects_duplicate_cost_semantic_drift():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("400"), work_order_id="WO-1"))
    book.treat_vendor_bill("BILL-1", "create_cost")

    registry = default_registry()
    assert _result(registry.run(book), "V-VENDOR-BILL-TREATMENT", "BILL-1").status == PASS

    linked_cost_id = book.vendor_bills["BILL-1"].linked_cost_id
    book.costs[linked_cost_id].amount = Decimal("450")
    failed = _result(registry.run(book), "V-VENDOR-BILL-TREATMENT", "BILL-1")
    assert failed.status == FAIL
    assert "no longer agrees" in failed.summary


def test_job_margin_verifier_calculates_but_does_not_claim_profitability_policy():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", status="complete", quoted_total=Decimal("1000")))
    book.add_cost(Cost("COST-1", "Supply", Decimal("400"), "material", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000"), status="issued"))

    result = _result(default_registry().run(book), "V-JOB-MARGIN-CALC", "WO-1")
    assert result.status == PASS
    assert result.evidence["margin"] == "600"
    assert "does not assert" in result.summary


def test_verifier_failures_enter_exception_queue_but_unknowns_do_not_become_failures():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000"), status="issued"))
    book.add_payment(Payment("PAY-1", Decimal("250"), "INV-1"))
    book.invoices["INV-1"].amount_paid = Decimal("200")
    book.add_deposit(BankDeposit("DEP-UNKNOWN", Decimal("50")))

    items = exception_queue(book, verifier_registry=default_registry())

    assert any(
        item.kind == "verification_failed" and "V-INVOICE-BALANCE" in item.message
        for item in items
    )
    assert not any(
        item.kind == "verification_failed" and item.reference_id == "DEP-UNKNOWN"
        for item in items
    )
