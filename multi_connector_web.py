"""Web routing for capability-aware multiple live connectors."""

from __future__ import annotations

from flask import flash, redirect, url_for

from imports import import_deposits, import_payments, import_work_orders


def install_multi_connector_routes(app) -> None:
    hub = app.config["CONNECTOR_HUB"]
    book = app.config["BOOKKEEPER"]

    def save():
        app.config["SAVE_BOOKKEEPER"]()

    def connector_name(connector) -> str:
        return getattr(connector, "name", connector.__class__.__name__)

    def report(label: str, result, source: str) -> None:
        if result.errors:
            flash(f"{source}: " + "; ".join(result.errors), "error")
        flash(f"{source} {label}: added {result.added}; skipped {result.skipped}", "success")

    def sync_field_service_multi():
        sources = hub.work_order_sources()
        if not sources:
            flash("No live work-order source is configured", "error")
            return redirect(url_for("dashboard"))
        try:
            for source in sources:
                result = import_work_orders(book, source.pull_work_orders())
                report("work orders", result, connector_name(source))
                hub.emit("work_orders.imported", {
                    "source": connector_name(source), "added": result.added, "skipped": result.skipped,
                })
            save()
        except Exception as exc:
            flash(f"Live work-order sync failed: {exc}", "error")
        return redirect(url_for("dashboard"))

    def sync_accounting_multi():
        payment_sources = hub.payment_sources()
        deposit_sources = hub.deposit_sources()
        if not payment_sources and not deposit_sources:
            flash("No live financial-evidence source is configured", "error")
            return redirect(url_for("dashboard"))
        try:
            for source in payment_sources:
                result = import_payments(book, source.pull_payments())
                report("payments", result, connector_name(source))
                hub.emit("payments.imported", {
                    "source": connector_name(source), "added": result.added, "skipped": result.skipped,
                })
            for source in deposit_sources:
                result = import_deposits(book, source.pull_deposits())
                report("deposits", result, connector_name(source))
                hub.emit("deposits.imported", {
                    "source": connector_name(source), "added": result.added, "skipped": result.skipped,
                })
            save()
        except Exception as exc:
            flash(f"Live financial sync failed: {exc}", "error")
        return redirect(url_for("dashboard"))

    def issue_invoice_multi(invoice_id: str):
        try:
            invoice = book.invoices[invoice_id]
            if invoice.status != "draft":
                raise ValueError("Invoice is already issued")
            external_ids: dict[str, str] = {}
            for sink in hub.invoice_sinks():
                external_ids[connector_name(sink)] = sink.push_invoice(invoice)
            invoice.status = "issued"
            save()
            hub.emit("invoice.issued", {"invoice_id": invoice.id, "external_ids": external_ids})
            if external_ids:
                flash("Invoice issued and sent to configured invoice destination(s)", "success")
            else:
                flash("Invoice issued locally; no external invoice destination is configured", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    app.view_functions["sync_field_service"] = sync_field_service_multi
    app.view_functions["sync_accounting"] = sync_accounting_multi
    app.view_functions["issue_invoice"] = issue_invoice_multi
