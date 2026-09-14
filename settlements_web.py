"""Web workflow for aggregate processor settlement reconciliation."""

from __future__ import annotations

from decimal import Decimal

from flask import flash, g, redirect, render_template, request, url_for



def install_settlements(app, store) -> None:
    app.config["SETTLEMENT_STORE"] = store

    def book():
        return app.config["BOOKKEEPER"]

    def can_write() -> bool:
        permissions = app.config.get("PERMISSION_STORE")
        return bool(permissions and permissions.user_has(getattr(g, "current_user", None), "bookkeeping.write"))

    def actor() -> str:
        user = getattr(g, "current_user", None)
        if user is None:
            return "unknown"
        return f"{user.display_name} [{user.username}] ({user.role})"

    def deny_write():
        flash("Bookkeeping-write permission required", "error")
        return redirect(url_for("settlement_dashboard"))

    def audit(event: str, settlement_id: str, detail: dict) -> None:
        app.config["AUDIT_LOG"].append(event, f"SETTLEMENT:{settlement_id}", detail, actor=actor())

    @app.get("/settlements")
    def settlement_dashboard():
        current_book = book()
        rows = [
            {
                "settlement": settlement,
                "reconciliation": store.reconcile(settlement.settlement_id, current_book),
                "adjustments": store.adjustments(settlement.settlement_id),
                "components": store.components(settlement.settlement_id),
            }
            for settlement in store.list()
        ]
        return render_template(
            "settlements.html",
            rows=rows,
            payments=sorted(current_book.payments.values(), key=lambda p: p.id),
            deposits=sorted(current_book.deposits.values(), key=lambda d: d.id),
        )

    @app.post("/settlements")
    def create_settlement():
        if not can_write():
            return deny_write()
        try:
            settlement = store.create(
                request.form.get("settlement_id", ""),
                request.form.get("provider", ""),
                request.form.get("reference", "") or None,
            )
            audit("settlement.created", settlement.settlement_id, {
                "provider": settlement.provider,
                "reference": settlement.reference,
            })
            flash("Settlement created", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("settlement_dashboard"))

    @app.post("/settlements/<settlement_id>/payments")
    def add_settlement_payment(settlement_id: str):
        if not can_write():
            return deny_write()
        payment_id = request.form.get("payment_id", "").strip()
        try:
            if store.components(settlement_id):
                raise ValueError("Imported processor composition is read-only; payment membership comes from processor evidence")
            store.add_payment(settlement_id, payment_id, book())
            audit("settlement.payment.added", settlement_id, {"payment_id": payment_id})
            flash("Payment added to settlement", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("settlement_dashboard"))

    @app.post("/settlements/<settlement_id>/adjustments")
    def add_settlement_adjustment(settlement_id: str):
        if not can_write():
            return deny_write()
        try:
            if store.components(settlement_id):
                raise ValueError("Imported processor composition is read-only; deductions come from processor evidence")
            adjustment = store.add_adjustment(
                request.form.get("adjustment_id", ""),
                settlement_id,
                request.form.get("kind", ""),
                Decimal(request.form.get("amount", "0")),
                request.form.get("reference", "") or None,
            )
            audit("settlement.adjustment.added", settlement_id, {
                "adjustment_id": adjustment.adjustment_id,
                "kind": adjustment.kind,
                "amount": str(adjustment.amount),
                "reference": adjustment.reference,
            })
            flash(f"{adjustment.kind.title()} added to settlement", "success")
        except (ValueError, ArithmeticError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("settlement_dashboard"))

    @app.post("/settlements/<settlement_id>/deposit")
    def link_settlement_deposit(settlement_id: str):
        if not can_write():
            return deny_write()
        deposit_id = request.form.get("deposit_id", "").strip()
        try:
            store.link_deposit(settlement_id, deposit_id, book())
            reconciliation = store.reconcile(settlement_id, book())
            audit("settlement.deposit.linked", settlement_id, {
                "deposit_id": deposit_id,
                "status": reconciliation.status,
                "expected_net": str(reconciliation.expected_net),
                "actual_deposit": str(reconciliation.actual_deposit) if reconciliation.actual_deposit is not None else None,
                "difference": str(reconciliation.difference) if reconciliation.difference is not None else None,
                "processor_component_count": reconciliation.processor_component_count,
            })
            if reconciliation.status == "reconciled":
                flash("Settlement reconciles exactly to the bank deposit", "success")
            elif reconciliation.status == "difference":
                flash(f"Settlement differs from the bank deposit by {reconciliation.difference}", "error")
            else:
                flash("Deposit linked; additional settlement evidence or classification is still required", "error")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("settlement_dashboard"))
