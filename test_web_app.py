from decimal import Decimal
import io

from app import BankDeposit, Payment, WorkOrder
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


class FakeAccounting:
    def __init__(self, payments=None, deposits=None):
        self._payments = payments or []
        self._deposits = deposits or []
        self.pushed = []

    def push_invoice(self, invoice):
        self.pushed.append(invoice.id)
        return invoice.id

    def pull_payments(self):
        return self._payments

    def pull_deposits(self):
        return self._deposits


class FakeEvents:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


def issue_test_invoice(client, wo_id="WO-1", total="1000"):
    client.post("/work-orders", data={
        "id": wo_id,
        "customer": "Smith",
        "description": "Water heater",
        "quoted_total": total,
        "status": "complete",
    })
    client.post(f"/work-orders/{wo_id}/invoice")
    client.post(f"/invoices/DRAFT-{wo_id}/issue")


def test_primary_workflow_persists_and_reconciles(tmp_path):
    data = tmp_path / "bookkeeper.json"
    app = create_app(str(data))
    app.config.update(TESTING=True)
    client = app.test_client()

    issue_test_invoice(client)
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

    issue_test_invoice(client, "WO-2", "500")
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
    app = create_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub(field_service=field, events=events))
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post("/sync/field-service")
    book = app.config["BOOKKEEPER"]
    assert book.work_orders["WO-LIVE-1"].customer == "Jones"
    assert any(event == "work_orders.imported" for event, _ in events.events)


def test_payment_csv_import_suggests_but_does_not_auto_apply(tmp_path):
    app = create_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    issue_test_invoice(client)

    csv_data = b"id,amount,reference\nPAY-CSV-1,1000,DRAFT-WO-1\n"
    client.post(
        "/imports/payments/csv",
        data={"file": (io.BytesIO(csv_data), "payments.csv")},
        content_type="multipart/form-data",
    )
    book = app.config["BOOKKEEPER"]
    assert book.payments["PAY-CSV-1"].invoice_id is None
    assert book.invoices["DRAFT-WO-1"].amount_paid == Decimal("0")
    assert book.suggest_payment_match("PAY-CSV-1") == "DRAFT-WO-1"

    client.post("/payments/PAY-CSV-1/accept-suggestion")
    assert book.payments["PAY-CSV-1"].invoice_id == "DRAFT-WO-1"
    assert book.invoices["DRAFT-WO-1"].payment_status == "paid"


def test_deposit_csv_import_suggests_and_reconciles_after_approval(tmp_path):
    app = create_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    issue_test_invoice(client)
    client.post("/payments", data={"id": "PAY-1", "invoice_id": "DRAFT-WO-1", "amount": "1000"})

    csv_data = b"id,amount,reference,processor_fee\nDEP-CSV-1,970,PAY-1,30\n"
    client.post(
        "/imports/deposits/csv",
        data={"file": (io.BytesIO(csv_data), "deposits.csv")},
        content_type="multipart/form-data",
    )
    book = app.config["BOOKKEEPER"]
    assert book.deposits["DEP-CSV-1"].payment_id is None
    assert book.suggest_deposit_match("DEP-CSV-1") == "PAY-1"

    client.post("/deposits/DEP-CSV-1/accept-suggestion")
    assert book.deposit_status("DEP-CSV-1") == "explained"


def test_accounting_sync_imports_evidence_without_auto_posting(tmp_path):
    accounting = FakeAccounting(
        payments=[Payment("PAY-LIVE-1", Decimal("1000"), invoice_id="DRAFT-WO-1")],
        deposits=[BankDeposit("DEP-LIVE-1", Decimal("970"), payment_id="PAY-LIVE-1", processor_fee=Decimal("30"))],
    )
    app = create_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub(accounting=accounting))
    app.config.update(TESTING=True)
    client = app.test_client()
    issue_test_invoice(client)

    client.post("/sync/accounting")
    book = app.config["BOOKKEEPER"]
    assert book.payments["PAY-LIVE-1"].invoice_id is None
    assert book.payments["PAY-LIVE-1"].reference == "DRAFT-WO-1"
    assert book.deposits["DEP-LIVE-1"].payment_id is None
    assert book.deposits["DEP-LIVE-1"].reference == "PAY-LIVE-1"
    assert book.invoices["DRAFT-WO-1"].amount_paid == Decimal("0")


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
