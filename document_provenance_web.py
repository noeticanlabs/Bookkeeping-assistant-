"""Final governed document-extraction routes with immutable interpretation history."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import flash, g, redirect, render_template, request, url_for

from authority import requires_action
from document_intake import proposal_from_extraction


def _actor(user) -> str:
    return f"{user.display_name} [{user.username}] ({user.role})"


def install_document_provenance_routes(app, extraction_history) -> None:
    book = app.config["BOOKKEEPER"]
    hub = app.config["CONNECTOR_HUB"]
    provenance = app.config["PROVENANCE"]
    audit = app.config["AUDIT_LOG"]

    def interpret(evidence_id: str, filename: str, path: str):
        connector = hub.documents
        if connector is None:
            raise ValueError("No document extraction connector is configured")
        extracted = connector.extract(path)
        run = extraction_history.record(
            provenance,
            evidence_id,
            extracted,
            provider=getattr(connector, "name", connector.__class__.__name__),
            model=getattr(connector, "model", None),
            schema_version=getattr(connector, "schema_version", "bookkeeping_proposal_v1"),
            prompt_version=getattr(connector, "prompt_version", "unspecified"),
        )
        evidence = provenance.documents[evidence_id]
        proposal = proposal_from_extraction(book, filename, extracted)
        audit.append(
            "document.proposed",
            evidence.evidence_id,
            {
                "filename": filename,
                "sha256": evidence.sha256,
                "extraction_id": run.extraction_id,
                "extraction_sequence": run.sequence,
                "extraction_output_hash": run.output_hash,
                "provider": run.provider,
                "model": run.model,
                "schema_version": run.schema_version,
                "prompt_version": run.prompt_version,
                "vendor": proposal.vendor,
                "amount": str(proposal.amount),
                "reference": proposal.reference,
                "document_id": proposal.document_id,
                "work_order_id": proposal.work_order_id,
                "record_type": proposal.record_type,
                "authenticated_user_id": g.current_user.user_id,
            },
            actor=_actor(g.current_user),
        )
        try:
            hub.emit("document.extracted", {
                "evidence_id": evidence.evidence_id,
                "extraction_id": run.extraction_id,
                "sha256": evidence.sha256,
                "filename": filename,
                "vendor": proposal.vendor,
                "amount": str(proposal.amount),
                "work_order_id": proposal.work_order_id,
            })
        except Exception:
            pass
        return render_template(
            "document_review.html",
            proposal=proposal,
            book=book,
            evidence=evidence,
            extraction_run=run,
        )

    @requires_action(app, "document.submit")
    def extract_document_versioned():
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
                raise ValueError(
                    f"Duplicate document already captured as {existing.evidence_id}{bound}; "
                    "use re-extraction on the preserved source instead of uploading a duplicate"
                )

            evidence = provenance.capture(temp_path, uploaded.filename)
            return interpret(evidence.evidence_id, uploaded.filename, temp_path)
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    @app.post("/documents/<evidence_id>/reextract")
    @requires_action(app, "document.submit")
    def reextract_document(evidence_id: str):
        try:
            if evidence_id not in provenance.documents:
                raise ValueError("Unknown source document")
            evidence = provenance.documents[evidence_id]
            source_path = Path(evidence.stored_path)
            if not source_path.is_file():
                raise ValueError("Preserved source document is unavailable")
            return interpret(evidence_id, evidence.original_filename, str(source_path))
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

    # Replace the compatibility upload route with the final authority-bearing
    # implementation. Re-extraction is a new governed route.
    app.view_functions["extract_document"] = extract_document_versioned
