"""Final governed web handlers for ordinary local financial mutations."""

from __future__ import annotations

from decimal import Decimal

from flask import flash, g, redirect, request, url_for

from app import BankDeposit, Payment, WorkOrder
from authority import requires_action


def _actor(user) -> str:
    return f"{user.display_name} [{user.username}] ({user.role})"


def install_financial_mutation_routes(app) -> None:
    book = app.config["BOOKKEEPER"]
    hub = app.config["CONNECTOR_HUB"]
    uow = app.config["FINANCIAL_MUTATION_UOW"]

    def emit(event: str, payload: dict[str, object]) -> None:
        try:
            hub.emit(event, payload)
        except Exception:
            # Connector events are advisory; committed bookkeeping state and the
            # durable audit receipt remain authoritative.
            pass

    @requires_action(app, "work_order.create")
    def add_work_order_atomic():
        try:
            quoted = request.form.get("quoted_total", "").strip()
            work_order = WorkOrder(
                id=request.form["id"].strip(),
                customer=request.form["customer"].strip(),
                description=request.form["description"].strip(),
                status=request.form.get("status", "complete"),
                quoted_total=Decimal(quoted) if quoted else None,
            )
            uow.commit(
                lambda: (book.add_work_order(work_order) or work_order),
                event_type="work_order.created",
                evidence_id=lambda row: f"WORK_ORDER:{row.id}",
                payload=lambda row: {
                    "work_order_id": row.id,
                    "customer": row.customer,
                    "status": row.status,
                    "quoted_total": str(row.quoted_total) if row.quoted_total is not None else None,
                    "authority_action": "work_order.create",
                    "authenticated_user_id": g.current_user.user_id,
                },
                actor=_actor(g.current_user),
            )
            emit("work_order.added", {"work_order_id": work_order.id})
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @requires_action(app, "invoice.prepare")
    def prepare_invoice_atomic(work_order_id: str):
        try:
            total = request.form.get("total", "").strip()
            invoice = uow.commit(
                lambda: book.prepare_invoice(work_order_id, Decimal(total) if total else None),
                event_type="invoice.prepared",
                evidence_id=lambda row: f"INVOICE:{row.id}",
                payload=lambda row: {
                    "invoice_id": row.id,
                    "work_order_id": row.work_order_id,
                    "customer": row.customer,
                    "total": str(row.total),
                    "authority_action": "invoice.prepare",
                    "authenticated_user_id": g.current_user.user_id,
                },
                actor=_actor(g.current_user),
            )
            emit("invoice.prepared", {"invoice_id": invoice.id, "work_order_id": work_order_id})
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @requires_action(app, "payment.record")
    def add_payment_atomic():
        try:
            invoice_id = request.form["invoice_id"].strip()
            payment = Payment(id=request.form["id"].strip(), amount=Decimal(request.form["amount"]))

            def mutate():
                invoice = book.invoices[invoice_id]
                if invoice.status == "draft":
                    raise ValueError("Issue the invoice before recording payment")
                book.add_payment(payment)
                book.match_payment(payment.id, invoice_id)
                return payment

            committed = uow.commit(
                mutate,
                event_type="payment.recorded",
                evidence_id=lambda row: f"PAYMENT:{row.id}",
                payload=lambda row: {
                    "payment_id": row.id,
                    "amount": str(row.amount),
                    "invoice_id": row.invoice_id,
                    "authority_action": "payment.record",
                    "authenticated_user_id": g.current_user.user_id,
                },
                actor=_actor(g.current_user),
            )
            emit("payment.recorded", {"payment_id": committed.id, "invoice_id": committed.invoice_id})
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @requires_action(app, "payment.match")
    def accept_payment_suggestion_atomic(payment_id: str):
        try:
            payment = uow.commit(
                lambda: book.accept_payment_match(payment_id),
                event_type="payment.matched",
                evidence_id=lambda row: f"PAYMENT:{row.id}",
                payload=lambda row: {
                    "payment_id": row.id,
                    "invoice_id": row.invoice_id,
                    "amount": str(row.amount),
                    "authority_action": "payment.match",
                    "authenticated_user_id": g.current_user.user_id,
                },
                actor=_actor(g.current_user),
            )
            emit("payment.matched", {"payment_id": payment.id, "invoice_id": payment.invoice_id})
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @requires_action(app, "deposit.record")
    def add_deposit_atomic():
        try:
            payment_id = request.form["payment_id"].strip()
            deposit = BankDeposit(
                id=request.form["id"].strip(),
                amount=Decimal(request.form["amount"]),
                processor_fee=Decimal(request.form.get("processor_fee", "0") or "0"),
            )

            def mutate():
                book.add_deposit(deposit)
                book.match_deposit(deposit.id, payment_id)
                return deposit

            committed = uow.commit(
                mutate,
                event_type="deposit.recorded",
                evidence_id=lambda row: f"DEPOSIT:{row.id}",
                payload=lambda row: {
                    "deposit_id": row.id,
                    "amount": str(row.amount),
                    "processor_fee": str(row.processor_fee),
                    "payment_id": row.payment_id,
                    "authority_action": "deposit.record",
                    "authenticated_user_id": g.current_user.user_id,
                },
                actor=_actor(g.current_user),
            )
            emit("deposit.recorded", {"deposit_id": committed.id, "payment_id": committed.payment_id})
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @requires_action(app, "deposit.match")
    def accept_deposit_suggestion_atomic(deposit_id: str):
        try:
            deposit = uow.commit(
                lambda: book.accept_deposit_match(deposit_id),
                event_type="deposit.matched",
                evidence_id=lambda row: f"DEPOSIT:{row.id}",
                payload=lambda row: {
                    "deposit_id": row.id,
                    "payment_id": row.payment_id,
                    "amount": str(row.amount),
                    "processor_fee": str(row.processor_fee),
                    "authority_action": "deposit.match",
                    "authenticated_user_id": g.current_user.user_id,
                },
                actor=_actor(g.current_user),
            )
            emit("deposit.matched", {"deposit_id": deposit.id, "payment_id": deposit.payment_id})
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    app.view_functions["add_work_order"] = add_work_order_atomic
    app.view_functions["prepare_invoice"] = prepare_invoice_atomic
    app.view_functions["add_payment"] = add_payment_atomic
    app.view_functions["accept_payment_suggestion"] = accept_payment_suggestion_atomic
    app.view_functions["add_deposit"] = add_deposit_atomic
    app.view_functions["accept_deposit_suggestion"] = accept_deposit_suggestion_atomic
