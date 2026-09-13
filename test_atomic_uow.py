from decimal import Decimal

import pytest

import atomic_uow
from app import Cost, WorkOrder
from atomic_uow import AtomicApprovalUnitOfWork
from secure_web_app import create_secure_app


def test_correction_finalization_rolls_back_approval_and_domain_state_on_failure(tmp_path, monkeypatch):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    book = app.config["BOOKKEEPER"]
    corrections = app.config["CORRECTIONS"]
    approvals = app.config["APPROVAL_POLICY"]

    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_cost(Cost("C-1", "Vendor", Decimal("100"), "materials", "WO-1"))
    app.config["SAVE_BOOKKEEPER"]()

    correction = corrections.propose(
        book,
        original_cost_id="C-1",
        replacement_cost_id="C-1-R1",
        vendor="Vendor",
        amount=Decimal("80"),
        work_order_id="WO-1",
        reference="corrected",
        reason="Receipt corrected",
        proposed_by="Book Keeper",
    )
    req = approvals.create_request(
        "cost_correction", correction.correction_id, "USR-P",
        {"correction_id": correction.correction_id}, Decimal("80"), required_approvals=1,
    )

    original_persist = atomic_uow._persist_book

    def fail_after_domain_mutation(conn, current_book):
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(atomic_uow, "_persist_book", fail_after_domain_mutation)
    uow = AtomicApprovalUnitOfWork(
        app.config["BOOKKEEPER_DATA_PATH"], book, app.config["PROVENANCE"], corrections
    )

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        uow.approve(req.request_id, "USR-A", "Owner [owner] (Owner)")

    assert approvals.approver_ids(req.request_id) == []
    assert approvals.get(req.request_id).status == "pending"
    assert book.job_cost("WO-1") == Decimal("100")
    assert book.costs["C-1"].superseded_by is None
    assert "C-1-R1" not in book.costs
    assert corrections.corrections[correction.correction_id].status == "pending"
    assert not any(
        e.event_type == "cost.correction.finalized"
        for e in app.config["AUDIT_LOG"].events()
    )

    # The same approver can retry because the failed approval itself rolled back.
    monkeypatch.setattr(atomic_uow, "_persist_book", original_persist)
    count, required, finalized = uow.approve(req.request_id, "USR-A", "Owner [owner] (Owner)")
    assert (count, required, finalized) == (1, 1, True)
    assert approvals.get(req.request_id).status == "approved"
    assert book.job_cost("WO-1") == Decimal("80")


def test_document_finalization_rolls_back_provenance_and_record_on_failure(tmp_path, monkeypatch):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    book = app.config["BOOKKEEPER"]
    approvals = app.config["APPROVAL_POLICY"]
    provenance = app.config["PROVENANCE"]

    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    app.config["SAVE_BOOKKEEPER"]()

    source = tmp_path / "receipt.pdf"
    source.write_bytes(b"test receipt bytes")
    doc = provenance.capture(source, "receipt.pdf")
    provenance.add_extraction(doc.evidence_id, {
        "vendor": "Vendor", "amount": 50, "reference": "WO-1", "document_id": "R-1",
        "work_order_id": "WO-1", "record_type": "cost",
    })
    payload = {
        "record_id": "C-DOC-1", "vendor": "Vendor", "amount": "50",
        "reference": "WO-1 R-1", "work_order_id": "WO-1", "record_type": "cost",
        "treatment": "ask", "linked_cost_id": None,
    }
    req = approvals.create_request(
        "document", doc.evidence_id, "USR-P", payload, Decimal("50"), required_approvals=1,
    )

    def fail_after_domain_mutation(conn, current_book):
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(atomic_uow, "_persist_book", fail_after_domain_mutation)
    uow = AtomicApprovalUnitOfWork(
        app.config["BOOKKEEPER_DATA_PATH"], book, provenance, app.config["CORRECTIONS"]
    )

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        uow.approve(req.request_id, "USR-A", "Owner [owner] (Owner)")

    assert approvals.approver_ids(req.request_id) == []
    assert approvals.get(req.request_id).status == "pending"
    assert "C-DOC-1" not in book.costs
    assert provenance.documents[doc.evidence_id].approved_record_id is None
    assert not any(
        e.event_type == "document.approved" and e.evidence_id == doc.evidence_id
        for e in app.config["AUDIT_LOG"].events()
    )
