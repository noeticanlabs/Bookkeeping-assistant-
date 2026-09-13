from decimal import Decimal

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, WorkOrder
from audit_log import AuditLog
from company_config import CompanyConfigStore
from corrections import CorrectionStore
from provenance import ProvenanceStore
from sqlite_store import (
    SQLiteAuditLog,
    SQLiteCompanyConfigStore,
    SQLiteCorrectionStore,
    SQLiteProvenanceStore,
    load_bookkeeper,
    migrate_legacy,
    save_bookkeeper,
)
from storage import save_bookkeeper as save_legacy_bookkeeper


def test_sqlite_bookkeeper_round_trip(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    book = Bookkeeper(vendor_bill_mode="create_cost")
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("1000")))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("250"), "materials", "WO-1"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Smith", Decimal("1000"), "issued", Decimal("400")))
    book.payments["PAY-1"] = Payment("PAY-1", Decimal("400"), "INV-1")
    book.add_deposit(BankDeposit("DEP-1", Decimal("388"), "PAY-1", processor_fee=Decimal("12")))

    save_bookkeeper(book, db)
    loaded = load_bookkeeper(db)

    assert loaded.vendor_bill_mode == "create_cost"
    assert loaded.job_cost("WO-1") == Decimal("250")
    assert loaded.invoices["INV-1"].balance_due == Decimal("600")
    assert loaded.deposit_status("DEP-1") == "explained"


def test_sqlite_company_profile_and_audit_round_trip(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    company = SQLiteCompanyConfigStore(db)
    company.update_from_strings(
        name="Wise Plumbing",
        job_label="Service Call",
        customer_label="Client",
        vendor_bill_mode="create_cost",
        approval_threshold="5000",
        approver_roles="Owner, Office Manager",
        field_service_system="Jobber",
        accounting_system="QuickBooks",
        bank_system="Bank feed",
        document_system="OpenAI",
    )
    audit = SQLiteAuditLog(db)
    first = audit.append("test.one", "COMPANY", {"x": 1}, actor="Owner")
    second = audit.append("test.two", "COMPANY", {"x": 2}, actor="Owner")

    loaded = SQLiteCompanyConfigStore(db).profile
    events = SQLiteAuditLog(db).events()
    assert loaded.name == "Wise Plumbing"
    assert loaded.approval_threshold == Decimal("5000")
    assert [e.event_id for e in events] == [first.event_id, second.event_id]


def test_sqlite_provenance_preserves_source_and_binding(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    source = tmp_path / "receipt.pdf"
    source.write_bytes(b"receipt bytes")
    store = SQLiteProvenanceStore(db, tmp_path / "documents")
    doc = store.capture(source, "receipt.pdf")
    store.add_extraction(doc.evidence_id, {"vendor": "Ferguson", "amount": "42.50"})
    store.bind_record(doc.evidence_id, "cost", "C-1")

    reloaded = SQLiteProvenanceStore(db, tmp_path / "documents")
    assert reloaded.documents[doc.evidence_id].extracted_vendor == "Ferguson"
    assert reloaded.source_for_record("cost", "C-1").sha256 == doc.sha256


def test_sqlite_correction_store_uses_same_domain_rules(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("C-1", "Ferguson", Decimal("500"), "materials", "WO-1"))
    corrections = SQLiteCorrectionStore(db)
    correction = corrections.propose(
        book,
        original_cost_id="C-1",
        replacement_cost_id="C-1-R1",
        vendor="Ferguson",
        amount=Decimal("450"),
        work_order_id="WO-1",
        reference="corrected",
        reason="Vendor credit",
        proposed_by="Bookkeeper",
    )
    corrections.approve(book, correction.correction_id, "Owner")

    assert book.job_cost("WO-1") == Decimal("450")
    assert SQLiteCorrectionStore(db).corrections[correction.correction_id].status == "approved"


def test_one_time_legacy_migration_imports_all_stores(tmp_path):
    legacy = tmp_path / "bookkeeper.json"
    db = tmp_path / "bookkeeper.sqlite3"

    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-OLD", "Legacy Customer", "Legacy repair"))
    save_legacy_bookkeeper(book, legacy)

    company = CompanyConfigStore(tmp_path / "bookkeeper-company.json")
    company.update_from_strings(
        name="Legacy Co", job_label="Job", customer_label="Customer",
        vendor_bill_mode="ask", approval_threshold="", approver_roles="Owner",
        field_service_system="none", accounting_system="none", bank_system="none", document_system="none",
    )

    source = tmp_path / "old.pdf"
    source.write_bytes(b"old evidence")
    provenance = ProvenanceStore(tmp_path / "bookkeeper-provenance.json", tmp_path / "bookkeeper-documents")
    doc = provenance.capture(source, "old.pdf")

    audit = AuditLog(tmp_path / "bookkeeper-audit.jsonl")
    audit.append("legacy.event", doc.evidence_id, {"ok": True}, actor="Owner")

    corrections = CorrectionStore(tmp_path / "bookkeeper-corrections.json")
    corrections.save()

    assert migrate_legacy(db, legacy)
    assert not migrate_legacy(db, legacy)
    assert "WO-OLD" in load_bookkeeper(db).work_orders
    assert SQLiteCompanyConfigStore(db).profile.name == "Legacy Co"
    assert doc.evidence_id in SQLiteProvenanceStore(db, tmp_path / "bookkeeper-documents").documents
    assert SQLiteAuditLog(db).events()[0].event_type == "legacy.event"
