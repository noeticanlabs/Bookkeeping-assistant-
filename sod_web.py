"""Separation-of-duties layer for the authenticated bookkeeping app."""

from __future__ import annotations

from decimal import Decimal
from flask import flash, redirect, render_template, request, session, url_for

from approval_policy import ApprovalPolicy, ApprovalPolicyStore
from document_intake import record_approved_document


def install_separation_of_duties(app, db_path):
    approvals = ApprovalPolicyStore(db_path)
    app.config["APPROVAL_POLICY"] = approvals

    users = app.config["USER_STORE"]
    permissions = app.config["PERMISSION_STORE"]

    def user():
        return users.get(session.get("user_id"))

    def ident(u):
        return f"{u.display_name} [{u.username}] ({u.role})"

    def deny(message):
        flash(message, "error")
        return redirect(url_for("dashboard"))

    @app.route("/settings/approvals", methods=["GET", "POST"])
    def approval_settings():
        u = user()
        if not permissions.user_has(u, "company.configure"):
            return deny("Company-configuration permission required")
        if request.method == "POST":
            try:
                raw = request.form.get("second_approval_threshold", "").strip()
                policy = ApprovalPolicy(
                    prevent_self_approval=request.form.get("prevent_self_approval") == "on",
                    second_approval_threshold=Decimal(raw) if raw else None,
                )
                approvals.save(policy)
                app.config["AUDIT_LOG"].append(
                    "approval.policy.updated", "SECURITY",
                    {"prevent_self_approval": policy.prevent_self_approval,
                     "second_approval_threshold": str(policy.second_approval_threshold) if policy.second_approval_threshold is not None else None},
                    actor=ident(u),
                )
                flash("Approval policy saved", "success")
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template("approval_settings.html", policy=approvals.load())

    @app.get("/approvals")
    def approval_queue():
        u = user()
        if not permissions.user_has(u, "records.read"):
            return deny("Records-read permission required")
        rows = []
        for req in approvals.pending():
            rows.append((req, approvals.approver_ids(req.request_id)))
        return render_template("approvals.html", rows=rows, current_user=u)

    # Replace document posting: submission creates a governed request instead of mutating books.
    def submit_document_for_approval():
        u = user()
        if not permissions.user_has(u, "documents.approve"):
            return deny("Document-approval permission required")
        try:
            evidence_id = request.form.get("evidence_id", "").strip()
            provenance = app.config["PROVENANCE"]
            if evidence_id not in provenance.documents:
                raise ValueError("Unknown source document")
            if provenance.documents[evidence_id].approved_record_id:
                raise ValueError("Source document is already bound to a bookkeeping record")
            amount = Decimal(request.form.get("amount", "0"))
            payload = {
                "record_id": request.form.get("record_id", "").strip(),
                "vendor": request.form.get("vendor", "").strip(),
                "amount": str(amount),
                "reference": request.form.get("reference", "").strip(),
                "work_order_id": request.form.get("work_order_id", "").strip() or None,
                "record_type": request.form.get("record_type", "vendor_bill"),
                "treatment": request.form.get("treatment", "ask"),
                "linked_cost_id": request.form.get("linked_cost_id", "").strip() or None,
            }
            req = approvals.create_request("document", evidence_id, u.user_id, payload, amount)
            app.config["AUDIT_LOG"].append(
                "document.approval.requested", evidence_id,
                {"request_id": req.request_id, "required_approvals": req.required_approvals,
                 "authenticated_user_id": u.user_id, "payload": payload}, actor=ident(u),
            )
            flash(f"Submitted for approval; {req.required_approvals} independent approval(s) required", "success")
            return redirect(url_for("approval_queue"))
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

    app.view_functions["approve_document"] = submit_document_for_approval

    # Add approval request after a correction proposal is successfully created.
    prior_propose = app.view_functions["propose_cost_correction"]
    def propose_correction_governed(cost_id, *args, **kwargs):
        u = user()
        before = set(app.config["CORRECTIONS"].corrections)
        result = prior_propose(cost_id, *args, **kwargs)
        after = set(app.config["CORRECTIONS"].corrections)
        new_ids = after - before
        if new_ids:
            correction_id = next(iter(new_ids))
            correction = app.config["CORRECTIONS"].corrections[correction_id]
            amount = Decimal(correction.proposed_amount)
            try:
                req = approvals.create_request("cost_correction", correction_id, u.user_id,
                                               {"correction_id": correction_id}, amount)
                app.config["AUDIT_LOG"].append(
                    "cost.correction.approval.requested", f"CORRECTION:{correction_id}",
                    {"request_id": req.request_id, "required_approvals": req.required_approvals,
                     "authenticated_user_id": u.user_id}, actor=ident(u),
                )
            except ValueError:
                pass
        return result
    app.view_functions["propose_cost_correction"] = propose_correction_governed

    @app.post("/approvals/<request_id>/approve")
    def approve_request(request_id: str):
        u = user()
        if not permissions.user_has(u, "corrections.approve"):
            return deny("Approval permission required")
        try:
            req, count, ready = approvals.record_approval(request_id, u.user_id)
            app.config["AUDIT_LOG"].append(
                "approval.recorded", f"APPROVAL:{request_id}",
                {"request_id": request_id, "subject_type": req.subject_type, "subject_id": req.subject_id,
                 "approval_number": count, "required_approvals": req.required_approvals,
                 "authenticated_user_id": u.user_id}, actor=ident(u),
            )
            if not ready:
                flash(f"Approval recorded; {req.required_approvals - count} more approval(s) required", "success")
                return redirect(url_for("approval_queue"))

            if req.subject_type == "cost_correction":
                correction = app.config["CORRECTIONS"].approve(
                    app.config["BOOKKEEPER"], req.subject_id, ident(u)
                )
                app.config["SAVE_BOOKKEEPER"]()
                app.config["AUDIT_LOG"].append(
                    "cost.correction.finalized", f"CORRECTION:{req.subject_id}",
                    {"replacement_cost_id": correction.id, "approver_ids": approvals.approver_ids(request_id)},
                    actor=ident(u),
                )
            elif req.subject_type == "document":
                p = req.payload
                book = app.config["BOOKKEEPER"]
                treatment = p["treatment"]
                if p["record_type"] == "vendor_bill" and treatment == "ask" and book.vendor_bill_mode != "ask":
                    treatment = book.vendor_bill_mode
                record = record_approved_document(
                    book, record_id=p["record_id"], vendor=p["vendor"], amount=Decimal(p["amount"]),
                    reference=p["reference"], work_order_id=p["work_order_id"], record_type=p["record_type"],
                    treatment=treatment, linked_cost_id=p["linked_cost_id"],
                )
                record_type = "cost" if record.id in book.costs else "vendor_bill"
                app.config["PROVENANCE"].bind_record(req.subject_id, record_type, record.id)
                app.config["SAVE_BOOKKEEPER"]()
                app.config["AUDIT_LOG"].append(
                    "document.approved", req.subject_id,
                    {"result": {"record_type": record_type, "record_id": record.id},
                     "approver_ids": approvals.approver_ids(request_id), "request_id": request_id}, actor=ident(u),
                )
            else:
                raise ValueError("Unsupported approval subject")
            approvals.complete(request_id)
            flash("Required independent approvals satisfied; financial state updated", "success")
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("approval_queue"))

    @app.post("/approvals/<request_id>/reject")
    def reject_request(request_id: str):
        u = user()
        if not permissions.user_has(u, "corrections.approve"):
            return deny("Approval permission required")
        try:
            req = approvals.get(request_id)
            if req is None:
                raise ValueError("Unknown approval request")
            if approvals.load().prevent_self_approval and req.proposer_user_id == u.user_id:
                raise ValueError("Proposer cannot resolve their own request")
            approvals.reject(request_id)
            if req.subject_type == "cost_correction":
                app.config["CORRECTIONS"].reject(req.subject_id, ident(u))
            app.config["AUDIT_LOG"].append(
                "approval.rejected", f"APPROVAL:{request_id}",
                {"subject_type": req.subject_type, "subject_id": req.subject_id,
                 "authenticated_user_id": u.user_id}, actor=ident(u),
            )
            flash("Approval request rejected", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("approval_queue"))

    # Old correction approval/rejection routes are disabled; requests go through /approvals.
    app.view_functions["approve_cost_correction"] = lambda correction_id: redirect(url_for("approval_queue"))
    app.view_functions["reject_cost_correction"] = lambda correction_id: redirect(url_for("approval_queue"))
    return approvals
