from decimal import Decimal

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder
from business_intelligence import exception_queue, invoice_readiness
from economic_links import RecordLinkStore, all_relationships, derived_relationships


def test_core_relationships_are_derived_without_duplicate_storage(tmp_path):
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", status="complete", quoted_total=Decimal("2500")))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("800"), "material", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("2500"), status="issued"))
    book.add_payment(Payment("PAY-1", Decimal("2500"), "INV-1"))
    book.add_deposit(BankDeposit("DEP-1", Decimal("2427.20"), "PAY-1", processor_fee=Decimal("72.80")))

    links = derived_relationships(book)
    tuples = {(x.from_type, x.from_id, x.relationship, x.to_type, x.to_id) for x in links}

    assert ("cost", "COST-1", "belongs_to", "work_order", "WO-1") in tuples
    assert ("invoice", "INV-1", "generated_by", "work_order", "WO-1") in tuples
    assert ("payment", "PAY-1", "settles", "invoice", "INV-1") in tuples
    assert ("deposit", "DEP-1", "contains", "payment", "PAY-1") in tuples

    store = RecordLinkStore(tmp_path / "bookkeeper.sqlite3")
    assert store.list() == []
    assert len(all_relationships(book, store)) == len(links)


def test_candidate_relationship_persists_without_mutating_books(tmp_path):
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("100"), "material"))
    store = RecordLinkStore(tmp_path / "bookkeeper.sqlite3")

    link = store.add(
        from_type="cost", from_id="COST-1", relationship="belongs_to",
        to_type="work_order", to_id="WO-1", confidence=.91,
    )

    assert link.status == "candidate"
    assert book.costs["COST-1"].work_order_id is None
    assert store.list()[0].confidence == .91

    verified = store.set_status(link.link_id, "verified")
    assert verified.status == "verified"
    assert book.costs["COST-1"].work_order_id is None


def test_invoice_readiness_blocks_unresolved_vendor_bill_treatment():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Water heater", status="complete", quoted_total=Decimal("2500")))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("800"), "material", "WO-1"))
    book.add_vendor_bill(VendorBill("BILL-1", "Ferguson", Decimal("800"), work_order_id="WO-1"))

    readiness = invoice_readiness(book, "WO-1")

    assert readiness.status == "needs_attention"
    assert readiness.job_cost == Decimal("800")
    assert readiness.projected_margin == Decimal("1700")
    assert any("BILL-1" in blocker for blocker in readiness.blockers)

    book.treat_vendor_bill("BILL-1", "support_cost", linked_cost_id="COST-1")
    readiness = invoice_readiness(book, "WO-1")
    assert readiness.status == "ready"


def test_exception_queue_prioritizes_financial_mismatches():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", status="complete", quoted_total=Decimal("1000")))
    book.add_payment(Payment("PAY-UNMATCHED", Decimal("200"), reference="unknown"))
    book.add_deposit(BankDeposit("DEP-UNMATCHED", Decimal("200"), reference="unknown"))

    items = exception_queue(book)

    assert any(item.kind == "unmatched_payment" and item.severity == "S3" for item in items)
    assert any(item.kind == "unmatched_deposit" and item.severity == "S3" for item in items)
    assert any(item.kind == "unbilled_job" for item in items)
    ranks = {"S5": 5, "S4": 4, "S3": 3, "S2": 2, "S1": 1, "S0": 0}
    assert [ranks[item.severity] for item in items] == sorted(
        [ranks[item.severity] for item in items], reverse=True
    )
