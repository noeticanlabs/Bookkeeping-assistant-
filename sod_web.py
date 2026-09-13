"""Separation-of-duties layer for the authenticated bookkeeping app."""

from __future__ import annotations

from decimal import Decimal
from flask import flash, redirect, render_template, request, session, url_for

from approval_policy import ApprovalPolicy, ApprovalPolicyStore
from atomic_uow import AtomicApprovalUnitOfWork
from workflow_policy import ACTION_TYPES, WorkflowPolicyStore


def install_separation_of_duties(app, db_path):
    approvals = ApprovalPolicyStore(db_path)
    workflows = WorkflowPolicyStore(db_path)
    app.config["APPROVAL_POLICY"] = approvals
    app.config["WORKFLOW_POLICY"] = workflows

    users = app.config["USER_STORE"]
    permissions = app.config["PERMISSION_STORE"]

    def user():
        return users.get(session.get("user_id"))

    def ident(u):
        return f"{u.display_name} [{u.username}] ({u.role})"

    def deny(message):
        flash(message, "error")
        return redirect(url_for("dashboard"))

    def atomic_uow():
        return AtomicApprovalUnitOfWork(
            db_path,
            app.config["BOOKKEEPER"],
            app.config["PROVENANCE"],
            app.config["CORRECTIONS"],
        )

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

    @app.route("/settings/workflows", methods=["GET", "POST"])
    def workflow_settings():
        u = user()
        if not permissions.user_has(u, "company.configure"):
            return deny("Company-configuration permission required")
        if request.method == "POST":
            try:
                action_type = request.form.get("action_type", "")
                base = int(request.form.get("base_approvals", "1"))
                raw_threshold = request.form.get("threshold", "").strip()
                high = int(request.form.get("threshold_approvals", str(base)))
                bands = [(None, base)]
                if raw_threshold:
                    bands.append((Decimal(raw_threshold), high))
                workflows.set_rules(action_type, bands)
                app.config["AUDIT_LOG"].append(
                    "workflow.policy.updated", "SECURITY",
                    {"action_type": action_type,
                     "rules": [{"min_amount": str(r.min_amount) if r.min_amount is not None else None,
                                "approvals_required": r.approvals_required} for r in workflows.rules(action_type)]},
                    actor=ident(u),
                )
                flash("Workflow policy saved", "success")
            except (ValueError, TypeError) as exc:
                flash(str(exc), "error")
        return render_template("workflow_settings.html", action_types=ACTION_TYPES, rules=workflows.rules())

    @app.get("/approvals")
    def approval_queue():
        u = user()
        if not permissions.user_has(u, "records.read"):
            return deny("Records-read permission required")
        rows = [(req, approvals.approver_ids(req.request_id)) for req in approvals.pending()]
        return render_template("approvals.html", rows=rows, current_user=u)

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
            action_type = "vendor_bill" if payload["record_type"] == "vendor_bill" else "document_cost"
            required = workflows.approvals_required(action_type, amount)
            if required == 0:
                atomic_uow().finalize_direct_document(payload, evidence_id, ident(u))
                flash("Recorded directly under company workflow policy with atomic persistence", "success")
                return redirect(url_for("dashboard"))
            req = approvals.create_request("document", evidence_id, u.user_id, payload, amount,
                                           required_approvals=required)
            app.config["AUDIT_LOG"].append(
                "document.approval.requested", evidence_id,
                {"request_id": req.request_id, "required_approvals": req.required_approvals,
                 "authenticated_user_id": u.user_id, "payload": payload,
                 "workflow_action_type": action_type}, actor=ident(u),
            )
            flash(f"Submitted for approval; {req.required_approvals} independent approval(s) required", "success")
            return redirect(url_for("approval_queue"))
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

    app.view_functions["approve_document"] = submit_document_for_approval

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
                required = workflows.approvals_required("cost_correction", amount)
                if required == 0:
                    atomic_uow().finalize_direct_correction(correction_id, ident(u))
                    return result
                req = approvals.create_request("cost_correction", correction_id, u.user_id,
                                               {"correction_id": correction_id}, amount,
                                               required_approvals=required)
                app.config["AUDIT_LOG"].append(
                    "cost.correction.approval.requested", f"CORRECTION:{correction_id}",
                    {"request_id": req.request_id, "required_approvals": req.required_approvals,
                     "authenticated_user_id": u.user_id}, actor=ident(u),
                )
            except ValueError as exc:
                flash(str(exc), "error")
        return result
    app.view_functions["propose_cost_correction"] = propose_correction_governed

    @app.post("/approvals/<request_id>/approve")
    def approve_request(request_id: str):
        u = user()
        if not permissions.user_has(u, "corrections.approve"):
            return deny("Approval permission required")
        try:
            count, required, finalized = atomic_uow().approve(request_id, u.user_id, ident(u))
            if finalized:
                flash("Required independent approvals satisfied; financial state updated atomically", "success")
            else:
                flash(f"Approval recorded; {required - count} more approval(s) required", "success")
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

    app.view_functions["approve_cost_correction"] = lambda correction_id: redirect(url_for("approval_queue"))
    app.view_functions["reject_cost_correction"] = lambda correction_id: redirect(url_for("approval_queue"))
    return approvals
