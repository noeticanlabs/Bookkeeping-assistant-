"""Small usable web app for the primary bookkeeping workflow."""

import os
from decimal import Decimal
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

from app import BankDeposit, Payment, WorkOrder
from connectors import ConnectorHub
from imports import (
    import_deposits,
    import_deposits_csv,
    import_payments,
    import_payments_csv,
    import_work_orders,
    import_work_orders_csv,
)
from storage import load_bookkeeper, save_bookkeeper


def create_app(data_path: str | None = None, connectors: ConnectorHub | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("BOOKKEEPER_SECRET", "dev-only-change-me")
    path = Path(data_path or os.environ.get("BOOKKEEPER_DATA", "bookkeeper-data.json"))
    hub = connectors or ConnectorHub()
    book = load_bookkeeper(path)

    def save() -> None:
        save_bookkeeper(book, path)

    def run(action):
        try:
            result = action()
            save()
            return result
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
            return None

    def report_import(label: str, result, event: str, source: str) -> None:
        if result.errors:
            flash("; ".join(result.errors), "error")
        flash(f"{label}: added {result.added}; skipped {result.skipped} existing", "success")
        hub.emit(event, {"source": source, "added": result.added, "skipped": result.skipped})

    @app.get("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            book=book,
            summary=book.attention_summary(),
            reviews=book.completed_job_invoice_reviews(),
            field_service_connected=hub.field_service is not None,
            accounting_connected=hub.accounting is not None,
        )

    @app.post("/work-orders")
    def add_work_order():
        def action():
            quoted = request.form.get("quoted_total", "").strip()
            wo = WorkOrder(
                id=request.form["id"].strip(),
                customer=request.form["customer"].strip(),
                description=request.form["description"].strip(),
                status=request.form.get("status", "complete"),
                quoted_total=Decimal(quoted) if quoted else None,
            )
            book.add_work_order(wo)
            hub.emit("work_order.added", {"work_order_id": wo.id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/imports/work-orders/csv")
    def import_work_order_csv():
        def action():
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                raise ValueError("Choose a CSV file")
            result = import_work_orders_csv(book, uploaded.read().decode("utf-8-sig"))
            report_import("Work orders", result, "work_orders.imported", "csv")
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/sync/field-service")
    def sync_field_service():
        def action():
            if not hub.field_service:
                raise ValueError("No field-service connector is configured")
            result = import_work_orders(book, hub.field_service.pull_work_orders())
            report_import("Work orders", result, "work_orders.imported", "field_service")
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/imports/payments/csv")
    def import_payment_csv():
        def action():
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                raise ValueError("Choose a payment CSV file")
            result = import_payments_csv(book, uploaded.read().decode("utf-8-sig"))
            report_import("Payments", result, "payments.imported", "csv")
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/imports/deposits/csv")
    def import_deposit_csv():
        def action():
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                raise ValueError("Choose a deposit CSV file")
            result = import_deposits_csv(book, uploaded.read().decode("utf-8-sig"))
            report_import("Deposits", result, "deposits.imported", "csv")
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/sync/accounting")
    def sync_accounting():
        def action():
            if not hub.accounting:
                raise ValueError("No accounting connector is configured")
            payment_result = import_payments(book, hub.accounting.pull_payments())
            deposit_result = import_deposits(book, hub.accounting.pull_deposits())
            report_import("Payments", payment_result, "payments.imported", "accounting")
            report_import("Deposits", deposit_result, "deposits.imported", "accounting")
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/work-orders/<work_order_id>/invoice")
    def prepare_invoice(work_order_id: str):
        def action():
            total = request.form.get("total", "").strip()
            invoice = book.prepare_invoice(work_order_id, Decimal(total) if total else None)
            hub.emit("invoice.prepared", {"invoice_id": invoice.id, "work_order_id": work_order_id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/invoices/<invoice_id>/issue")
    def issue_invoice(invoice_id: str):
        def action():
            invoice = book.invoices[invoice_id]
            if invoice.status != "draft":
                raise ValueError("Invoice is already issued")
            if hub.field_service:
                hub.field_service.issue_invoice(invoice)
            if hub.accounting:
                hub.accounting.push_invoice(invoice)
            invoice.status = "issued"
            hub.emit("invoice.issued", {"invoice_id": invoice.id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/payments")
    def add_payment():
        def action():
            invoice_id = request.form["invoice_id"].strip()
            invoice = book.invoices[invoice_id]
            if invoice.status == "draft":
                raise ValueError("Issue the invoice before recording payment")
            payment = Payment(id=request.form["id"].strip(), amount=Decimal(request.form["amount"]))
            book.add_payment(payment)
            book.match_payment(payment.id, invoice_id)
            hub.emit("payment.recorded", {"payment_id": payment.id, "invoice_id": invoice_id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/payments/<payment_id>/accept-suggestion")
    def accept_payment_suggestion(payment_id: str):
        def action():
            payment = book.accept_payment_match(payment_id)
            hub.emit("payment.matched", {"payment_id": payment.id, "invoice_id": payment.invoice_id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/deposits")
    def add_deposit():
        def action():
            payment_id = request.form["payment_id"].strip()
            deposit = BankDeposit(
                id=request.form["id"].strip(),
                amount=Decimal(request.form["amount"]),
                processor_fee=Decimal(request.form.get("processor_fee", "0") or "0"),
            )
            book.add_deposit(deposit)
            book.match_deposit(deposit.id, payment_id)
            hub.emit("deposit.recorded", {"deposit_id": deposit.id, "payment_id": payment_id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/deposits/<deposit_id>/accept-suggestion")
    def accept_deposit_suggestion(deposit_id: str):
        def action():
            deposit = book.accept_deposit_match(deposit_id)
            hub.emit("deposit.matched", {"deposit_id": deposit.id, "payment_id": deposit.payment_id})
        run(action)
        return redirect(url_for("dashboard"))

    @app.post("/demo")
    def seed_demo():
        def action():
            if "WO-1001" not in book.work_orders:
                book.add_work_order(WorkOrder("WO-1001", "Smith Residence", "Water heater replacement", "complete", Decimal("2450")))
        run(action)
        return redirect(url_for("dashboard"))

    app.config["BOOKKEEPER"] = book
    app.config["BOOKKEEPER_DATA_PATH"] = str(path)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
