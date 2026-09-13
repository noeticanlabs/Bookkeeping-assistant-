from decimal import Decimal
import io

import pytest

from app import Bookkeeper, Cost, WorkOrder
from connectors import ConnectorHub
from document_intake import proposal_from_extraction, record_approved_document
from web_app import create_app


class FakeDocuments:
    def __init__(self, result):
        self.result = result
        self.paths = []

    def extract(self, document_path):
        self.paths.append(document_path)
        return self.result


def test_document_proposal_uses_exact_work_order_reference():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_work_order(WorkOrder("WO-10", "Jones", "Install"))

    proposal = proposal_from_extraction(book, "invoice.pdf", {
        "vendor": "Ferguson",
        "amount": "742.16",
        "reference": "Supplier invoice for WO-10",
        "document_id": "F-99",
    })

    assert proposal.work_order_id == "WO-10"
    assert proposal.vendor == "Ferguson"
    assert proposal.amount == Decimal("742.16")
    assert "F-99" in proposal.reference


def test_document_extraction_only_returns_review_and_does_not_post(tmp_path):
    documents = FakeDocuments({
        "vendor": "Ferguson",
        "amount": "742.16",
        "reference": "WO-1",
        "document_id": "BILL-99",
    })
    app = create_app(
        str(tmp_path / "bookkeeper.json"),
        connectors=ConnectorHub(documents=documents),
    )
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/work-orders", data={
        "id": "WO-1", "customer": "Smith", "description": "Repair",
        "quoted_total": "1000", "status": "complete",
    })

    response = client.post(
        "/documents/extract",
        data={"file": (io.BytesIO(b"fake document bytes"), "ferguson.pdf")},
        content_type="multipart/form-data",
    )

    book = app.config["BOOKKEEPER"]
    assert response.status_code == 200
    assert b"Nothing has been posted yet" in response.data
    assert b"Ferguson" in response.data
    assert b"742.16" in response.data
    assert book.costs == {}
    assert book.vendor_bills == {}


def test_user_can_approve_document_as_job_cost(tmp_path):
    app = create_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/work-orders", data={
        "id": "WO-1", "customer": "Smith", "description": "Repair",
        "quoted_total": "1000", "status": "complete",
    })

    client.post("/documents/approve", data={
        "record_id": "COST-99",
        "vendor": "Ferguson",
        "amount": "742.16",
        "reference": "WO-1 F-99",
        "work_order_id": "WO-1",
        "record_type": "cost",
        "treatment": "ask",
    })

    book = app.config["BOOKKEEPER"]
    assert book.costs["COST-99"].work_order_id == "WO-1"
    assert book.job_cost("WO-1") == Decimal("742.16")


def test_vendor_bill_create_cost_is_one_economic_cost():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))

    bill = record_approved_document(
        book,
        record_id="BILL-1",
        vendor="Ferguson",
        amount=Decimal("500"),
        work_order_id="WO-1",
        record_type="vendor_bill",
        treatment="create_cost",
    )

    assert bill.linked_cost_id == "BILL-COST-BILL-1"
    assert book.job_cost("WO-1") == Decimal("500")
    assert len(book.costs) == 1


def test_invalid_support_decision_does_not_create_partial_bill():
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair"))
    book.add_cost(Cost("COST-1", "Ferguson", Decimal("200"), "materials", "WO-1"))

    with pytest.raises(ValueError):
        record_approved_document(
            book,
            record_id="BILL-1",
            vendor="Ferguson",
            amount=Decimal("200"),
            work_order_id="WO-1",
            record_type="vendor_bill",
            treatment="support_cost",
            linked_cost_id="MISSING",
        )

    assert "BILL-1" not in book.vendor_bills
    assert book.job_cost("WO-1") == Decimal("200")
