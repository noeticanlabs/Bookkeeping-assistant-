"""Operator views for economic relationships, exceptions, and invoice readiness."""

from __future__ import annotations

from flask import flash, g, redirect, render_template, request, url_for

from business_intelligence import completed_job_readiness, exception_queue, invoice_readiness
from economic_links import RecordLinkStore, all_relationships


def install_business_intelligence(app, db_path) -> RecordLinkStore:
    links = RecordLinkStore(db_path)
    app.config["RECORD_LINKS"] = links

    def can_write() -> bool:
        permissions = app.config.get("PERMISSION_STORE")
        return bool(permissions and permissions.user_has(getattr(g, "current_user", None), "bookkeeping.write"))

    def actor() -> str:
        user = getattr(g, "current_user", None)
        if user is None:
            return "unknown"
        return f"{user.display_name} [{user.username}] ({user.role})"

    @app.get("/exceptions")
    def exception_dashboard():
        book = app.config["BOOKKEEPER"]
        return render_template(
            "exceptions.html",
            exceptions=exception_queue(book),
            readiness=completed_job_readiness(book),
        )

    @app.get("/relationships")
    def relationship_dashboard():
        return render_template(
            "relationships.html",
            links=all_relationships(app.config["BOOKKEEPER"], links),
        )

    @app.post("/relationships")
    def add_relationship():
        if not can_write():
            flash("Bookkeeping-write permission required", "error")
            return redirect(url_for("relationship_dashboard"))
        try:
            confidence_text = request.form.get("confidence", "").strip()
            link = links.add(
                from_type=request.form.get("from_type", ""),
                from_id=request.form.get("from_id", ""),
                relationship=request.form.get("relationship", ""),
                to_type=request.form.get("to_type", ""),
                to_id=request.form.get("to_id", ""),
                status="candidate",
                source="manual",
                evidence_id=request.form.get("evidence_id", "").strip() or None,
                confidence=float(confidence_text) if confidence_text else None,
            )
            app.config["AUDIT_LOG"].append(
                "relationship.candidate.created",
                link.evidence_id or link.link_id,
                {
                    "link_id": link.link_id,
                    "from": [link.from_type, link.from_id],
                    "relationship": link.relationship,
                    "to": [link.to_type, link.to_id],
                    "confidence": link.confidence,
                },
                actor=actor(),
            )
            flash("Candidate relationship recorded for review", "success")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("relationship_dashboard"))

    @app.post("/relationships/<link_id>/<decision>")
    def decide_relationship(link_id: str, decision: str):
        if not can_write():
            flash("Bookkeeping-write permission required", "error")
            return redirect(url_for("relationship_dashboard"))
        if decision not in {"verified", "rejected"}:
            flash("Invalid relationship decision", "error")
            return redirect(url_for("relationship_dashboard"))
        try:
            link = links.set_status(link_id, decision)
            app.config["AUDIT_LOG"].append(
                f"relationship.{decision}", link.evidence_id or link.link_id,
                {"link_id": link.link_id, "relationship": link.relationship},
                actor=actor(),
            )
            flash(f"Relationship marked {decision}", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("relationship_dashboard"))

    @app.get("/jobs/<work_order_id>/invoice-readiness")
    def job_invoice_readiness(work_order_id: str):
        try:
            result = invoice_readiness(app.config["BOOKKEEPER"], work_order_id)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("exception_dashboard"))
        return render_template("invoice_readiness.html", readiness=result)

    return links
