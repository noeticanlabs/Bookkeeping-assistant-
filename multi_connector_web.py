"""Web routing for capability-aware multiple live connectors."""

from __future__ import annotations

from flask import flash, g, redirect, render_template, url_for

from imports import import_deposits, import_payments, import_work_orders


def install_multi_connector_routes(app) -> None:
    hub = app.config["CONNECTOR_HUB"]
    book = app.config["BOOKKEEPER"]

    def save():
        app.config["SAVE_BOOKKEEPER"]()

    def connector_name(connector) -> str:
        return getattr(connector, "name", connector.__class__.__name__)

    def connector_id(connector) -> str:
        return getattr(connector, "_managed_connection_id", None) or f"RUNTIME:{connector.__class__.__name__}:{connector_name(connector)}"

    def report(label: str, result, source: str) -> None:
        if result.errors:
            flash(f"{source}: " + "; ".join(result.errors), "error")
        flash(f"{source} {label}: added {result.added}; skipped {result.skipped}", "success")

    @app.get("/settings/connections")
    def connection_summary():
        rows = [
            {
                "name": connector_name(connector),
                "capabilities": sorted(getattr(connector, "capabilities", frozenset())),
                "class_name": connector.__class__.__name__,
            }
            for connector in hub.connectors
        ]
        return render_template("connections.html", connectors=rows)

    @app.get("/sync-status")
    def sync_status():
        store = app.config["SYNC_RELIABILITY"]
        return render_template("sync_status.html", runs=store.list_runs(), outbox=store.list_outbox())

    @app.post("/sync-status/outbox/<item_id>/retry")
    def retry_uncertain_outbox(item_id: str):
        user = getattr(g, "current_user", None)
        permissions = app.config.get("PERMISSION_STORE")
        if not user or not permissions or not permissions.user_has(user, "company.configure"):
            return redirect(url_for("dashboard"))
        store = app.config["SYNC_RELIABILITY"]
        try:
            store.reset_uncertain(item_id)
            app.config["AUDIT_LOG"].append(
                "sync.outbox.retry_authorized", f"OUTBOX:{item_id}",
                {"item_id": item_id}, actor=user.user_id,
            )
            flash("Outbox item reset to pending. Retry only after confirming the remote system did not already create it.", "success")
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("sync_status"))

    def _pull(source, capability: str, loader, importer, label: str) -> None:
        sync = app.config["SYNC_RELIABILITY"]
        sid = connector_id(source)
        sname = connector_name(source)
        run_id = sync.start_run(sid, sname, capability, "pull")
        try:
            rows = loader()
            result = importer(book, rows)
            save()
            sync.finish_run(
                run_id, added=result.added, skipped=result.skipped,
                detail={"errors": list(result.errors)},
            )
            report(label, result, sname)
            hub.emit(f"{capability}.imported", {
                "source": sname, "connector_id": sid, "added": result.added, "skipped": result.skipped,
            })
        except Exception as exc:
            sync.fail_run(run_id, str(exc))
            flash(f"{sname} {label} sync failed: {exc}", "error")

    def sync_field_service_multi():
        sources = hub.work_order_sources()
        if not sources:
            flash("No live work-order source is configured", "error")
            return redirect(url_for("dashboard"))
        for source in sources:
            _pull(source, "work_orders.read", source.pull_work_orders, import_work_orders, "work orders")
        return redirect(url_for("dashboard"))

    def sync_accounting_multi():
        payment_sources = hub.payment_sources()
        deposit_sources = hub.deposit_sources()
        if not payment_sources and not deposit_sources:
            flash("No live financial-evidence source is configured", "error")
            return redirect(url_for("dashboard"))
        for source in payment_sources:
            _pull(source, "payments.read", source.pull_payments, import_payments, "payments")
        for source in deposit_sources:
            _pull(source, "deposits.read", source.pull_deposits, import_deposits, "deposits")
        return redirect(url_for("dashboard"))

    def issue_invoice_multi(invoice_id: str):
        sync = app.config["SYNC_RELIABILITY"]
        try:
            invoice = book.invoices[invoice_id]
            if invoice.status != "draft":
                raise ValueError("Invoice is already issued")
            sinks = hub.invoice_sinks()
            if not sinks:
                invoice.status = "issued"
                save()
                hub.emit("invoice.issued", {"invoice_id": invoice.id, "external_ids": {}})
                flash("Invoice issued locally; no external invoice destination is configured", "success")
                return redirect(url_for("dashboard"))

            results: dict[str, str] = {}
            blocked = False
            for sink in sinks:
                sid = connector_id(sink)
                sname = connector_name(sink)
                item = sync.enqueue(
                    sid, sname, "invoice.push", "invoice", invoice.id,
                    {"invoice_id": invoice.id, "work_order_id": invoice.work_order_id, "total": str(invoice.total)},
                )
                if item.status == "succeeded":
                    results[sname] = item.external_id or "sent"
                    continue
                if item.status == "uncertain":
                    blocked = True
                    flash(f"{sname}: invoice delivery is uncertain. Check the remote system before authorizing a retry.", "error")
                    continue
                try:
                    sync.begin_send(item.item_id)
                    external_id = sink.push_invoice(invoice)
                    sync.mark_sent(item.item_id, str(external_id))
                    results[sname] = str(external_id)
                except Exception as exc:
                    sync.mark_uncertain(item.item_id, str(exc))
                    blocked = True
                    flash(f"{sname}: delivery became uncertain: {exc}", "error")

            if blocked:
                flash("Invoice remains draft until all external destinations are confirmed.", "error")
                return redirect(url_for("dashboard"))

            invoice.status = "issued"
            save()
            hub.emit("invoice.issued", {"invoice_id": invoice.id, "external_ids": results})
            flash("Invoice issued and confirmed by configured invoice destination(s)", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    app.view_functions["sync_field_service"] = sync_field_service_multi
    app.view_functions["sync_accounting"] = sync_accounting_multi
    app.view_functions["issue_invoice"] = issue_invoice_multi
