"""Small usable web app for the primary bookkeeping workflow."""

import os
import tempfile
from decimal import Decimal
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

from app import BankDeposit, Payment, WorkOrder
from connector_factory import default_connector_hub
from connectors import ConnectorHub
from document_intake import proposal_from_extraction, record_approved_document
from imports import (
    import_deposits,
    import_deposits_csv,
    import_payments,
    import_payments_csv,
    import_work_orders,
    import_work_orders_csv,
)
from sqlite_store import (
    SQLiteAuditLog,
    SQLiteCompanyConfigStore,
    SQLiteCorrectionStore,
    SQLiteProvenanceStore,
    load_bookkeeper,
    migrate_legacy,
    save_bookkeeper,
)


def create_app(data_path: str | None = None, connectors: ConnectorHub | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("BOOKKEEPER_SECRET", "dev-only-change-me")
    legacy_path = Path(data_path or os.environ.get("BOOKKEEPER_DATA", "bookkeeper-data.json"))
    db_path = legacy_path if legacy_path.suffix in {".sqlite", ".sqlite3", ".db"} else legacy_path.with_suffix(".sqlite3")
    migrate_legacy(db_path, legacy_path)
    hub = connectors if connectors is not None else default_connector_hub()
    book = load_bookkeeper(db_path)
    company = SQLiteCompanyConfigStore(db_path)
    book.vendor_bill_mode = company.profile.vendor_bill_mode
    provenance = SQLiteProvenanceStore(
        db_path,
        legacy_path.parent / f"{legacy_path.stem}-documents",
    )
    audit = SQLiteAuditLog(db_path)
    corrections = SQLiteCorrectionStore(db_path)

    def save() -> None:
        save_bookkeeper(book, db_path)

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
            profile=company.profile,
            summary=book.attention_summary(),
            reviews=book.completed_job_invoice_reviews(),
            field_service_connected=hub.field_service is not None,
            accounting_connected=hub.accounting is not None,
            document_connected=hub.documents is not None,
            source_documents=provenance.documents.values(),
            audit_events=audit.events(),
        )

    @app.get("/settings/company")
    def company_settings():
        return render_template("company_settings.html", profile=company.profile)

    @app.post("/settings/company")
    def update_company_settings():
        try:
            profile = company.update_from_strings(
                name=request.form.get("name", ""),
                job_label=request.form.get("job_label", ""),
                customer_label=request.form.get("customer_label", ""),
                vendor_bill_mode=request.form.get("vendor_bill_mode", "ask"),
                approval_threshold=request.form.get("approval_threshold", ""),
                approver_roles=request.form.get("approver_roles", ""),
                field_service_system=request.form.get("field_service_system", "none"),
                accounting_system=request.form.get("accounting_system", "none"),
                bank_system=request.form.get("bank_system", "none"),
                document_system=request.form.get("document_system", "none"),
            )
            book.vendor_bill_mode = profile.vendor_bill_mode
            save()
            audit.append(
                "company.configuration.updated",
                "COMPANY",
                {
                    "company": profile.name,
                    "job_label": profile.job_label,
                    "customer_label": profile.customer_label,
                    "vendor_bill_mode": profile.vendor_bill_mode,
                    "approval_threshold": str(profile.approval_threshold) if profile.approval_threshold is not None else None,
                    "approver_roles": list(profile.approver_roles),
                    "field_service_system": profile.field_service_system,
                    "accounting_system": profile.accounting_system,
                    "bank_system": profile.bank_system,
                    "document_system": profile.document_system,
                },
                actor="administrator",
            )
            flash("Company configuration saved", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("company_settings"))

    @app.get("/corrections")
    def correction_dashboard():
        return render_template(
            "corrections.html",
            book=book,
            corrections=corrections.corrections.values(),
        )

    @app.post("/costs/<cost_id>/corrections")
    def propose_cost_correction(cost_id: str):
        def action():
            correction = corrections.propose(
                book,
                original_cost_id=cost_id,
                replacement_cost_id=request.form.get("replacement_cost_id", ""),
                vendor=request.form.get("vendor", ""),
                amount=Decimal(request.form["amount"]),
                work_order_id=request.form.get("work_order_id", "").strip() or None,
                reference=request.form.get("reference", "").strip() or None,
                reason=request.form.get("reason", ""),
                proposed_by=request.form.get("actor", ""),
            )
            source = provenance.source_for_record("cost", cost_id)
            audit.append(
                "cost.correction.proposed",
                source.evidence_id if source else f"COST:{cost_id}",
                {
                    "correction_id": correction.correction_id,
                    "original_cost_id": cost_id,
                    "replacement_cost_id": correction.replacement_cost_id,
                    "proposal": {
                        "vendor": correction.proposed_vendor,
                        "amount": correction.proposed_amount,
                        "work_order_id": correction.proposed_work_order_id,
                        "reference": correction.proposed_reference,
                    },
                    "reason": correction.reason,
                },
                actor=correction.proposed_by,
            )
            hub.emit("cost.correction.proposed", {"correction_id": correction.correction_id, "cost_id": cost_id})
            return correction

        correction = run(action)
        if correction is not None:
            flash("Cost correction proposed; original record is unchanged until approval", "success")
        return redirect(url_for("correction_dashboard"))

    @app.post("/corrections/<correction_id>/approve")
    def approve_cost_correction(correction_id: str):
        def action():
            correction = corrections.corrections.get(correction_id)
            if correction is None:
                raise ValueError("Unknown correction")
            original = book.costs[correction.original_cost_id]
            original_snapshot = {
                "id": original.id,
                "vendor": original.vendor,
                "amount": str(original.amount),
                "work_order_id": original.work_order_id,
                "reference": original.reference,
            }
            actor = request.form.get("actor", "").strip()
            replacement = corrections.approve(book, correction_id, actor)
            source = provenance.source_for_record("cost", original.id)
            audit.append(
                "cost.correction.approved",
                source.evidence_id if source else f"COST:{original.id}",
                {
                    "correction_id": correction_id,
                    "reason": correction.reason,
                    "original": original_snapshot,
                    "replacement": {
                        "id": replacement.id,
                        "vendor": replacement.vendor,
                        "amount": str(replacement.amount),
                        "work_order_id": replacement.work_order_id,
                        "reference": replacement.reference,
                        "correction_of": replacement.correction_of,
                    },
                    "superseded_record": original.id,
                },
                actor=actor,
            )
            hub.emit("cost.correction.approved", {"correction_id": correction_id, "replacement_cost_id": replacement.id})
            return replacement

        replacement = run(action)
        if replacement is not None:
            flash("Correction approved; original cost preserved and superseded", "success")
        return redirect(url_for("correction_dashboard"))

    @app.post("/corrections/<correction_id>/reject")
    def reject_cost_correction(correction_id: str):
        def action():
            actor = request.form.get("actor", "").strip()
            correction = corrections.reject(correction_id, actor)
            source = provenance.source_for_record("cost", correction.original_cost_id)
            audit.append(
                "cost.correction.rejected",
                source.evidence_id if source else f"COST:{correction.original_cost_id}",
                {"correction_id": correction_id, "reason": correction.reason},
                actor=actor,
            )
            return correction

        correction = run(action)
        if correction is not None:
            flash("Correction rejected; original cost remains current", "success")
        return redirect(url_for("correction_dashboard"))

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

    @app.post("/documents/extract")
    def extract_document():
        if not hub.documents:
            flash("No document extraction connector is configured", "error")
            return redirect(url_for("dashboard"))
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            flash("Choose a receipt or vendor invoice", "error")
            return redirect(url_for("dashboard"))

        suffix = Path(uploaded.filename).suffix
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
                uploaded.save(temp)
                temp_path = temp.name

            sha256 = provenance.sha256_file(temp_path)
            existing = provenance.by_sha256(sha256)
            if existing:
                bound = (
                    f" and is already bound to {existing.approved_record_type} {existing.approved_record_id}"
                    if existing.approved_record_id
                    else ""
                )
                raise ValueError(f"Duplicate document already captured as {existing.evidence_id}{bound}")

            evidence = provenance.capture(temp_path, uploaded.filename)
            extracted = hub.documents.extract(temp_path)
            provenance.add_extraction(evidence.evidence_id, extracted)
            proposal = proposal_from_extraction(book, uploaded.filename, extracted)
            audit.append(
                "document.proposed",
                evidence.evidence_id,
                {
                    "filename": uploaded.filename,
                    "sha256": evidence.sha256,
                    "vendor": proposal.vendor,
                    "amount": str(proposal.amount),
                    "reference": proposal.reference,
                    "document_id": proposal.document_id,
                    "work_order_id": proposal.work_order_id,
                    "record_type": proposal.record_type,
                },
                actor="document_reader",
            )
            hub.emit("document.extracted", {
                "evidence_id": evidence.evidence_id,
                "sha256": evidence.sha256,
                "filename": uploaded.filename,
                "vendor": proposal.vendor,
                "amount": str(proposal.amount),
                "work_order_id": proposal.work_order_id,
            })
            return render_template("document_review.html", proposal=proposal, book=book, evidence=evidence)
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    @app.post("/documents/approve")
    def approve_document():
        def action():
            evidence_id = request.form.get("evidence_id", "").strip()
            if evidence_id not in provenance.documents:
                raise ValueError("Unknown source document")
            source = provenance.documents[evidence_id]
            if source.approved_record_id:
                raise ValueError("Source document is already bound to a bookkeeping record")

            approved = {
                "record_id": request.form.get("record_id", "").strip(),
                "vendor": request.form.get("vendor", "").strip(),
                "amount": request.form.get("amount", "").strip(),
                "reference": request.form.get("reference", "").strip(),
                "work_order_id": request.form.get("work_order_id", "").strip() or None,
                "record_type": request.form.get("record_type", "vendor_bill"),
                "treatment": request.form.get("treatment", "ask"),
                "linked_cost_id": request.form.get("linked_cost_id", "").strip() or None,
            }
            proposed = {
                "vendor": source.extracted_vendor,
                "amount": source.extracted_amount,
                "reference": source.extracted_reference,
                "document_id": source.extracted_document_id,
                "work_order_id": source.extracted_work_order_id,
                "record_type": source.extracted_record_type,
            }
            changes = {
                key: {"proposed": proposed.get(key), "approved": approved.get(key)}
                for key in {"vendor", "amount", "reference", "work_order_id", "record_type"}
                if str(proposed.get(key) or "") != str(approved.get(key) or "")
            }

            treatment = approved["treatment"]
            if approved["record_type"] == "vendor_bill" and treatment == "ask" and book.vendor_bill_mode != "ask":
                treatment = book.vendor_bill_mode
                approved["treatment"] = treatment

            record = record_approved_document(
                book,
                record_id=approved["record_id"],
                vendor=approved["vendor"],
                amount=Decimal(approved["amount"]),
                reference=approved["reference"],
                work_order_id=approved["work_order_id"],
                record_type=approved["record_type"],
                treatment=treatment,
                linked_cost_id=approved["linked_cost_id"],
            )
            record_type = "cost" if record.id in book.costs else "vendor_bill"
            provenance.bind_record(evidence_id, record_type, record.id)
            audit.append(
                "document.approved",
                evidence_id,
                {
                    "proposal": proposed,
                    "approved": approved,
                    "changes": changes,
                    "result": {"record_type": record_type, "record_id": record.id},
                    "extra_approval_required": company.profile.requires_extra_approval(Decimal(approved["amount"])),
                },
                actor=request.form.get("actor", "user").strip() or "user",
            )
            hub.emit("document.approved", {
                "evidence_id": evidence_id,
                "record_type": record_type,
                "record_id": record.id,
            })
            return record

        record = run(action)
        if record is not None:
            flash("Document approved, recorded, and added to the decision audit trail", "success")
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
    app.config["BOOKKEEPER_DATA_PATH"] = str(db_path)
    app.config["BOOKKEEPER_LEGACY_PATH"] = str(legacy_path)
    app.config["COMPANY_CONFIG"] = company
    app.config["PROVENANCE"] = provenance
    app.config["AUDIT_LOG"] = audit
    app.config["CORRECTIONS"] = corrections
    app.config["CONNECTOR_HUB"] = hub
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
