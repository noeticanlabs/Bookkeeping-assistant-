from decimal import Decimal
import io

from app import WorkOrder
from connectors import ConnectorHub
from storage import load_bookkeeper
from web_app import create_app


class FakeFieldService:
    def __init__(self, work_orders=None):
        self.issued = []
        self._work_orders = work_orders or []

    def pull_work_orders(self):
        return self._work_orders

    def issue_invoice(self, invoice):
        self.issued.append(invoice.id)
        return invoice.id


class FakeEvents:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


def test_primary_workflow_persists_and_reconciles(tmp_path):
    data = tmp_path / "bookkeeper.json"
    app = create_app(str(data))
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post("/work-orders", data={
        "id": "WO-1",
        "customer": "Smith",
        "description": "Water heater",
        "quoted_total": "1000",
        "status": "complete",
    })
    client.post("/work-orders/WO-1/invoice")
    client.post("/invoices/DRAFT-WO-1/issue")
    client.post("/payments", data={"id": "PAY-1", "invoice_id": "DRAFT-WO-1", "amount": "1000"})
    client.post("/deposits", data={"id": "DEP-1", "payment_id": "PAY-1", "amount": "970", "processor_fee": "30"})

    saved = load_bookkeeper(data)
    assert saved.invoices["DRAFT-WO-1"].status == "issued"
    assert saved.invoices["DRAFT-WO-1"].payment_status == "paid"
    assert saved.deposit_status("DEP-1") == "explained"
    assert saved.attention_summary()["needs_attention"] == []


def test_issue_invoice_calls_optional_connector_and_event_hook(tmp_path):
    field = FakeFieldService()
    events = FakeEvents()
    hub = ConnectorHub(field_service=field, events=events)
    app = create_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post("/work-orders", data={
        "id": "WO-2", "customer": "Jones", "description": "Repair",
        "quoted_total": "500", "status": "complete",
    })
    client.post("/work-orders/WO-2/invoice")
    client.post("/invoices/DRAFT-WO-2/issue")

    assert field.issued == ["DRAFT-WO-2"]
    assert any(event == "invoice.issued" for event, _ in events.events)


def test_csv_import_enters_same_invoice_workflow(tmp_path):
    app = create_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    csv_data = b"id,customer,description,status,quoted_total\nWO-CSV-1,Smith,Drain repair,complete,425\n"

    response = client.post(
        "/imports/work-orders/csv",
        data={"file": (io.BytesIO(csv_data), "jobs.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    book = app.config["BOOKKEEPER"]
    assert book.work_orders["WO-CSV-1"].quoted_total == Decimal("425")

    client.post("/work-orders/WO-CSV-1/invoice")
    assert "DRAFT-WO-CSV-1" in book.invoices


def test_field_service_sync_uses_same_normalized_work_order_path(tmp_path):
    field = FakeFieldService([
        WorkOrder("WO-LIVE-1", "Jones", "Sewer repair", "complete", Decimal("1200"))
    ])
    events = FakeEvents()
    app = create_app(
        str(tmp_path / "bookkeeper.json"),
        connectors=ConnectorHub(field_service=field, events=events),
    )
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post("/sync/field-service")
    book = app.config["BOOKKEEPER"]
    assert book.work_orders["WO-LIVE-1"].customer == "Jones"
    assert any(event == "work_orders.imported" for event, _ in events.events)


def test_draft_invoice_cannot_receive_payment(tmp_path):
    app = create_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/work-orders", data={
        "id": "WO-3", "customer": "Lee", "description": "Repair",
        "quoted_total": "400", "status": "complete",
    })
    client.post("/work-orders/WO-3/invoice")
    client.post("/payments", data={"id": "PAY-3", "invoice_id": "DRAFT-WO-3", "amount": "400"})
    book = app.config["BOOKKEEPER"]
    assert "PAY-3" not in book.payments
    assert book.invoices["DRAFT-WO-3"].amount_paid == Decimal("0")
