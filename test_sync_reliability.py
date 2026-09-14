from decimal import Decimal

from app import Invoice, Payment, WorkOrder
from connectors import ConnectorHub, INVOICES_WRITE, PAYMENTS_READ
from readiness import build_readiness
from secure_web_app import create_secure_app
from sync_reliability import SyncReliabilityStore


def setup_admin(client):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def test_sync_runs_record_success_and_failure(tmp_path):
    store = SyncReliabilityStore(tmp_path / "bookkeeper.sqlite3")
    ok = store.start_run("CONN-1", "Source", "payments.read")
    store.finish_run(ok, added=3, skipped=2)
    bad = store.start_run("CONN-1", "Source", "deposits.read")
    store.fail_run(bad, "provider unavailable")

    runs = store.list_runs()
    by_id = {run.run_id: run for run in runs}
    assert by_id[ok].status == "success"
    assert by_id[ok].added == 3
    assert by_id[ok].skipped == 2
    assert by_id[bad].status == "failed"
    assert by_id[bad].error == "provider unavailable"
    assert store.last_success("CONN-1", "payments.read").run_id == ok
    assert store.last_success("CONN-1", "deposits.read") is None


def test_outbox_idempotency_returns_same_item(tmp_path):
    store = SyncReliabilityStore(tmp_path / "bookkeeper.sqlite3")
    first = store.enqueue("CONN-1", "Xero", "invoice.push", "invoice", "INV-7", {"total": "50"})
    second = store.enqueue("CONN-1", "Xero", "invoice.push", "invoice", "INV-7", {"total": "50"})

    assert first.item_id == second.item_id
    assert len(store.list_outbox()) == 1


def test_uncertain_outbox_never_auto_retries(tmp_path):
    store = SyncReliabilityStore(tmp_path / "bookkeeper.sqlite3")
    item = store.enqueue("CONN-1", "Xero", "invoice.push", "invoice", "INV-7", {})
    store.begin_send(item.item_id)
    store.mark_uncertain(item.item_id, "connection reset after send")

    try:
        store.begin_send(item.item_id)
        assert False, "uncertain write must not auto-retry"
    except ValueError as exc:
        assert "not retryable" in str(exc)

    store.reset_uncertain(item.item_id)
    retry = store.begin_send(item.item_id)
    assert retry.attempts == 2


class UncertainInvoiceSink:
    name = "Remote Ledger"
    capabilities = frozenset({INVOICES_WRITE})

    def __init__(self):
        self.calls = 0

    def push_invoice(self, invoice):
        self.calls += 1
        raise RuntimeError("response lost after transmission")


def test_invoice_uncertain_delivery_stays_draft_and_is_not_resent(tmp_path):
    hub = ConnectorHub()
    sink = UncertainInvoiceSink()
    hub.register(sink)
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    book = app.config["BOOKKEEPER"]
    book.add_work_order(WorkOrder("WO-1", "Customer", "Repair", status="complete"))
    book.add_invoice(Invoice("INV-1", "WO-1", "Customer", Decimal("100")))

    response = client.post("/invoices/INV-1/issue")
    assert response.status_code in (302, 303)
    assert sink.calls == 1
    assert book.invoices["INV-1"].status == "draft"
    outbox = app.config["SYNC_RELIABILITY"].list_outbox()
    assert len(outbox) == 1
    assert outbox[0].status == "uncertain"

    client.post("/invoices/INV-1/issue")
    assert sink.calls == 1
    assert book.invoices["INV-1"].status == "draft"


def test_uncertain_outbound_delivery_blocks_readiness(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub())
    app.config.update(TESTING=True)
    sync = app.config["SYNC_RELIABILITY"]
    item = sync.enqueue("CONN-1", "Xero", "invoice.push", "invoice", "INV-9", {})
    sync.begin_send(item.item_id)
    sync.mark_uncertain(item.item_id, "timeout after transmission")

    items = build_readiness(app)
    blocker = next(item for item in items if item.title == "Uncertain outbound deliveries")
    assert blocker.blocking is True
    assert blocker.status == "missing"


class PaymentSource:
    name = "Payment Source"
    capabilities = frozenset({PAYMENTS_READ})

    def pull_payments(self):
        return [Payment("PAY-ROLLBACK", Decimal("25"), reference="INV-X")]


def test_pull_sync_restores_memory_if_persistence_fails(tmp_path):
    hub = ConnectorHub()
    hub.register(PaymentSource())
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    app.config["SAVE_BOOKKEEPER"] = lambda: (_ for _ in ()).throw(RuntimeError("disk write failed"))
    book = app.config["BOOKKEEPER"]

    response = client.post("/sync/accounting")
    assert response.status_code in (302, 303)

    assert "PAY-ROLLBACK" not in book.payments
    latest = app.config["SYNC_RELIABILITY"].list_runs()[0]
    assert latest.status == "failed"
    assert "disk write failed" in latest.error
