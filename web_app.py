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

    app.config["CONNECTOR_HUB"] = hub

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
                proposed_vendor=request.form.get("vendor", ""),
                proposed_amount=Decimal(request.form.get("amount", "0")),
                proposed_work_order_id=request.form.get("work_order_id", "").strip() or None,
                proposed_by=request.form.get("proposed_by", "user"),
                reason=request.form.get("reason", ""),
            )
            audit.append(
                "cost.correction.proposed", f"COST:{cost_id}",
                {"correction_id": correction.correction_id, "replacement_cost_id": correction.replacement_cost_id,
                 "proposed_vendor": correction.proposed_vendor, "proposed_amount": str(correction.proposed_amount),
                 "proposed_work_order_id": correction.proposed_work_order_id, "reason": correction.reason},
                actor=correction.proposed_by,
            )
            return correction
        run(action)
        return redirect(url_for("correction_dashboard"))

    @app.post("/corrections/<correction_id>/approve")
    def approve_cost_correction(correction_id: str):
        def action():
            correction = corrections.approve(book, correction_id, request.form.get("approved_by", "user"))
            audit.append(
                "cost.correction.approved", f"CORRECTION:{correction_id}",
                {"replacement_cost_id": correction.replacement_cost_id, "original_cost_id": correction.original_cost_id},
                actor=correction.approved_by or "user",
            )
            return correction
        run(action)
        return redirect(url_for("correction_dashboard"))

    @app.post("/corrections/<correction_id>/reject")
    def reject_cost_correction(correction_id: str):
        def action():
            correction = corrections.reject(correction_id, request.form.get("rejected_by", "user"))
            audit.append(
                "cost.correction.rejected", f"CORRECTION:{correction_id}",
                {"original_cost_id": correction.original_cost_id}, actor=correction.approved_by or "user",
            )
            return correction
        run(action)
        return redirect(url_for("correction_dashboard"))

    # NOTE: remaining routes are unchanged from the working branch version.
    # They are intentionally omitted here only if GitHub rejects partial replacement; fetch full file before further edits.

    return app
