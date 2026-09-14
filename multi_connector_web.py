"""Web routing for capability-aware multiple live connectors."""

from __future__ import annotations

from copy import deepcopy

from flask import flash, g, redirect, render_template, url_for

from authority import requires_action
from imports import import_deposits, import_payments, import_work_orders
from verifier_registry import blocking_invoice_failures


def install_multi_connector_routes(app) -> None:
    hub = app.config["CONNECTOR_HUB"]
    book = app.config["BOOKKEEPER"]

    def save():
        app.config["SAVE_BOOKKEEPER"]()

    def restore_book(snapshot) -> None:
        book.vendor_bill_mode = snapshot.vendor_bill_mode
        book.work_orders = snapshot.work_orders
        book.costs = snapshot.costs
        book.vendor_bills = snapshot.vendor_bills
        book.invoices = snapshot.invoices
        book.payments = snapshot.payments
        book.deposits = snapshot.deposits

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
        schedules = app.config.get("PULL_SCHEDULES")
        return render_template(
            "sync_status.html",
            runs=store.list_runs(),
            outbox=store.list_outbox(),
            schedules=schedules.list() if schedules else [],
            auto_sync_enabled=bool(app.config.get("AUTO_SYNC_ENABLED")),
            auto_sync_interval_seconds=int(app.config.get("AUTO_SYNC_INTERVAL_SECONDS") or 0),
        )

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
        snapshot = deepcopy(book)
        try:
            rows = loader()
            result = importer(book, rows)
            save()
            sync.finish_run(
                run_id, added=result.added, skipped=result.skipped,
                detail={"errors": list(result.errors)},
            )
            report(label, result, sname)
            try:
                hub.emit(f"{capability}.imported", {
                    "source": sname, "connector_id": sid, "added": result.added, "skipped": result.skipped,
                })
            except Exception:
                pass
        except Exception as exc:
            restore_book(snapshot)
            try:
                sync.fail_run(run_id, str(exc))
            except ValueError:
                pass
            flash(f"{sname} {label} sync failed: {exc}", "error")

    def _pull_settlements(source) -> None:
        sync = app.config["SYNC_RELIABILITY"]
        settlement_store = app.config.get("SETTLEMENT_STORE")
        sid = connector_id(source)
        sname = connector_name(source)
        run_id = sync.start_run(sid, sname, "settlements.read", "pull")
        try:
            if settlement_store is None:
                raise RuntimeError("Settlement store is not configured")
            rows = source.pull_settlements()
            result = settlement_store.import_evidence(rows, book=book)
            sync.finish_run(
                run_id, added=result.added, skipped=result.skipped,
                detail={"errors": list(result.errors)},
            )
            report("settlements", result, sname)
            try:
                hub.emit("settlements.read.imported", {
                    "source": sname, "connector_id": sid, "added": result.added, "skipped": result.skipped,
                })
            except Exception:
                pass
        except Exception as exc:
            try:
                sync.fail_run(run_id, str(exc))
            except ValueError:
                pass
            flash(f"{sname} settlement sync failed: {exc}", "error")

    @requires_action(app, "sync.run")
    def sync_field_service_multi():
        sources = hub.work_order_sources()
        if not sources:
            flash("No live work-order source is configured", "error")
            return redirect(url_for("dashboard"))
        for source in sources:
            _pull(source, "work_orders.read", source.pull_work_orders, import_work_orders, "work orders")
        return redirect(url_for("dashboard"))

    @requires_action(app, "sync.run")
    def sync_accounting_multi():
        payment_sources = hub.payment_sources()
        deposit_sources = hub.deposit_sources()
        settlement_sources = hub.settlement_sources()
        if not payment_sources and not deposit_sources and not settlement_sources:
            flash("No live financial-evidence source is configured", "error")
            return redirect(url_for("dashboard"))
        for source in payment_sources:
            _pull(source, "payments.read", source.pull_payments, import_payments, "payments")
        for source in settlement_sources:
            _pull_settlements(source)
        for source in deposit_sources:
            _pull(source, "deposits.read", source.pull_deposits, import_deposits, "bank deposits")
        return redirect(url_for("dashboard"))

    @requires_action(app, "invoice.issue")
    def issue_invoice_multi(invoice_id: str):
        sync = app.config["SYNC_RELIABILITY"]
        try:
            invoice = book.invoices[invoice_id]
            if invoice.status != "draft":
                raise ValueError("Invoice is already issued")

            registry = app.config.get("VERIFIER_REGISTRY")
            if registry is not None:
                failures = blocking_invoice_failures(book, invoice.id, registry)
                if failures:
                    verifier_ids = ", ".join(sorted({result.verifier_id for result in failures}))
                    raise ValueError(
                        f"Invoice blocked by deterministic verification failure(s): {verifier_ids}. "
                        "Review /verifications before issuing."
                    )

            sinks = hub.invoice_sinks()
            if not sinks:
                old_status = invoice.status
                invoice.status = "issued"
                try:
                    save()
                except Exception:
                    invoice.status = old_status
                    raise
                try:
                    hub.emit("invoice.issued", {"invoice_id": invoice.id, "external_ids": {}})
                except Exception:
                    pass
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
                    try:
                        sync.mark_uncertain(item.item_id, str(exc))
                    except ValueError:
                        pass
                    blocked = True
                    flash(f"{sname}: delivery became uncertain: {exc}", "error")

            if blocked:
                flash("Invoice remains draft until all external destinations are confirmed.", "error")
                return redirect(url_for("dashboard"))

            old_status = invoice.status
            invoice.status = "issued"
            try:
                save()
            except Exception:
                invoice.status = old_status
                raise
            try:
                hub.emit("invoice.issued", {"invoice_id": invoice.id, "external_ids": results})
            except Exception:
                pass
            flash("Invoice issued and confirmed by configured invoice destination(s)", "success")
        except Exception as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    # Replace the compatibility routes with the final governed implementations.
    # Their authority checks are attached to these final functions, so later
    # view-function assignment cannot strip an earlier wrapper.
    app.view_functions["sync_field_service"] = sync_field_service_multi
    app.view_functions["sync_accounting"] = sync_accounting_multi
    app.view_functions["issue_invoice"] = issue_invoice_multi
