import threading
from decimal import Decimal

import pytest

import financial_uow as financial_uow_module
from app import Bookkeeper, Invoice, Payment, WorkOrder
from financial_uow import FinancialMutationUnitOfWork
from secure_web_app import create_secure_app
from sqlite_store import load_bookkeeper


def test_financial_mutation_rolls_back_memory_and_sqlite_if_audit_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "bookkeeper.sqlite3"
    book = Bookkeeper()
    uow = FinancialMutationUnitOfWork(db_path, book)
    work_order = WorkOrder("WO-ROLLBACK", "Customer", "Repair", "complete", Decimal("100"))

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr(financial_uow_module, "_audit_row", fail_audit)

    with pytest.raises(RuntimeError, match="audit persistence failed"):
        uow.commit(
            lambda: (book.add_work_order(work_order) or work_order),
            event_type="work_order.created",
            evidence_id="WORK_ORDER:WO-ROLLBACK",
            payload={"work_order_id": "WO-ROLLBACK"},
            actor="tester",
        )

    assert "WO-ROLLBACK" not in book.work_orders
    reloaded = load_bookkeeper(db_path)
    assert "WO-ROLLBACK" not in reloaded.work_orders


def test_financial_uow_serializes_competing_payment_validation(tmp_path):
    db_path = tmp_path / "bookkeeper.sqlite3"
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Customer", "Repair", "complete", Decimal("1000")))
    book.add_invoice(Invoice("INV-1", "WO-1", "Customer", Decimal("1000"), status="issued"))
    uow = FinancialMutationUnitOfWork(db_path, book)

    # Establish the initial state through the same transaction boundary.
    uow.commit(
        lambda: book.invoices["INV-1"],
        event_type="test.initialized",
        evidence_id="INVOICE:INV-1",
        payload={},
        actor="tester",
    )

    barrier = threading.Barrier(2)
    successes = []
    failures = []

    def worker(payment_id):
        try:
            barrier.wait()

            def mutate():
                payment = Payment(payment_id, Decimal("700"))
                book.add_payment(payment)
                book.match_payment(payment.id, "INV-1")
                return payment

            result = uow.commit(
                mutate,
                event_type="payment.recorded",
                evidence_id=lambda row: f"PAYMENT:{row.id}",
                payload=lambda row: {"amount": str(row.amount)},
                actor=payment_id,
            )
            successes.append(result.id)
        except Exception as exc:
            failures.append(str(exc))

    threads = [threading.Thread(target=worker, args=("PAY-A",)), threading.Thread(target=worker, args=("PAY-B",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert len(failures) == 1
    assert "exceeds invoice balance" in failures[0]
    assert book.invoices["INV-1"].amount_paid == Decimal("700")
    assert len(book.payments) == 1


def setup_admin(client):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def test_ordinary_financial_routes_create_durable_audit_receipts(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)

    client.post("/work-orders", data={
        "id": "WO-AUDIT", "customer": "Customer", "description": "Repair",
        "status": "complete", "quoted_total": "100",
    })
    client.post("/work-orders/WO-AUDIT/invoice", data={"total": "100"})
    client.post("/invoices/DRAFT-WO-AUDIT/issue")
    client.post("/payments", data={
        "id": "PAY-AUDIT", "invoice_id": "DRAFT-WO-AUDIT", "amount": "100",
    })
    client.post("/deposits", data={
        "id": "DEP-AUDIT", "payment_id": "PAY-AUDIT", "amount": "97", "processor_fee": "3",
    })

    book = app.config["BOOKKEEPER"]
    assert book.work_orders["WO-AUDIT"].status == "complete"
    assert book.invoices["DRAFT-WO-AUDIT"].status == "issued"
    assert book.payments["PAY-AUDIT"].invoice_id == "DRAFT-WO-AUDIT"
    assert book.deposits["DEP-AUDIT"].payment_id == "PAY-AUDIT"

    events = app.config["AUDIT_LOG"].events()
    event_types = {event.event_type for event in events}
    assert {
        "work_order.created", "invoice.prepared", "invoice.issued",
        "payment.recorded", "deposit.recorded",
    } <= event_types

    by_type = {event.event_type: event for event in events}
    assert by_type["invoice.issued"].payload["authority_action"] == "invoice.issue"
    assert by_type["payment.recorded"].payload["invoice_id"] == "DRAFT-WO-AUDIT"
    assert by_type["deposit.recorded"].payload["payment_id"] == "PAY-AUDIT"
