from decimal import Decimal

import pytest

from app import Bookkeeper, Cost, WorkOrder
from corrections import CorrectionStore
from web_app import create_app


def test_approved_correction_preserves_original_and_updates_job_cost(tmp_path):
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("500"), "materials", "WO-1", "receipt-1"))
    store = CorrectionStore(tmp_path / "corrections.json")

    proposal = store.propose(
        book,
        original_cost_id="COST-1",
        replacement_cost_id="COST-1-R1",
        vendor="Ferguson Plumbing Supply",
        amount=Decimal("450"),
        work_order_id="WO-1",
        reference="receipt-1 corrected",
        reason="Vendor credit reduced material cost",
        proposed_by="Mikey",
    )

    assert book.job_cost("WO-1") == Decimal("500")
    assert book.costs["COST-1"].superseded_by is None

    replacement = store.approve(book, proposal.correction_id, "Matt")
    assert replacement.correction_of == "COST-1"
    assert book.costs["COST-1"].superseded_by == "COST-1-R1"
    assert book.costs["COST-1"].amount == Decimal("500")
    assert book.costs["COST-1-R1"].amount == Decimal("450")
    assert book.job_cost("WO-1") == Decimal("450")


def test_correction_requires_separate_approval(tmp_path):
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("500"), "materials", "WO-1"))
    store = CorrectionStore(tmp_path / "corrections.json")

    correction = store.propose(
        book,
        original_cost_id="COST-1",
        replacement_cost_id="COST-1-R1",
        vendor="Ferguson",
        amount=Decimal("475"),
        work_order_id="WO-1",
        reference=None,
        reason="Corrected receipt total",
        proposed_by="Mikey",
    )

    assert correction.status == "pending"
    assert "COST-1-R1" not in book.costs
    assert book.costs["COST-1"].is_current


def test_superseded_cost_cannot_be_corrected_again(tmp_path):
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("500"), "materials", "WO-1"))
    store = CorrectionStore(tmp_path / "corrections.json")
    first = store.propose(
        book,
        original_cost_id="COST-1",
        replacement_cost_id="COST-1-R1",
        vendor="Ferguson",
        amount=Decimal("450"),
        work_order_id="WO-1",
        reference=None,
        reason="First correction",
        proposed_by="Mikey",
    )
    store.approve(book, first.correction_id, "Matt")

    with pytest.raises(ValueError):
        store.propose(
            book,
            original_cost_id="COST-1",
            replacement_cost_id="COST-1-R2",
            vendor="Ferguson",
            amount=Decimal("425"),
            work_order_id="WO-1",
            reference=None,
            reason="Wrong version",
            proposed_by="Mikey",
        )


def test_rejected_correction_leaves_original_current(tmp_path):
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("500"), "materials", "WO-1"))
    store = CorrectionStore(tmp_path / "corrections.json")
    correction = store.propose(
        book,
        original_cost_id="COST-1",
        replacement_cost_id="COST-1-R1",
        vendor="Ferguson",
        amount=Decimal("450"),
        work_order_id="WO-1",
        reference=None,
        reason="Questionable correction",
        proposed_by="Mikey",
    )
    store.reject(correction.correction_id, "Matt")

    assert book.costs["COST-1"].is_current
    assert "COST-1-R1" not in book.costs
    assert book.job_cost("WO-1") == Decimal("500")


def test_web_correction_flow_audits_proposal_and_approval(tmp_path):
    data = tmp_path / "bookkeeper.json"
    app = create_app(str(data))
    app.config.update(TESTING=True)
    client = app.test_client()
    book = app.config["BOOKKEEPER"]

    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("500"), "document_cost", "WO-1"))

    response = client.post("/costs/COST-1/corrections", data={
        "replacement_cost_id": "COST-1-R1",
        "vendor": "Ferguson",
        "amount": "450",
        "work_order_id": "WO-1",
        "reference": "credit memo",
        "reason": "Vendor credit",
        "actor": "Mikey",
    })
    assert response.status_code == 302
    correction = next(iter(app.config["CORRECTIONS"].corrections.values()))
    assert correction.status == "pending"
    assert book.job_cost("WO-1") == Decimal("500")

    client.post(f"/corrections/{correction.correction_id}/approve", data={"actor": "Matt"})
    assert correction.status == "approved"
    assert book.job_cost("WO-1") == Decimal("450")

    event_types = [event.event_type for event in app.config["AUDIT_LOG"].events()]
    assert "cost.correction.proposed" in event_types
    assert "cost.correction.approved" in event_types
