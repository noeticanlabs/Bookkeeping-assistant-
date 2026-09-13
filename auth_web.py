"""Authentication and authorization layer for the existing Flask bookkeeping app."""

from __future__ import annotations

from decimal import Decimal
from functools import wraps

from flask import flash, g, redirect, render_template, request, session, url_for

from auth import UserStore, is_admin, is_approver
from document_intake import record_approved_document


PUBLIC_ENDPOINTS = {"login", "logout", "setup_admin", "static"}


def install_auth(app, db_path) -> UserStore:
    users = UserStore(db_path)
    app.config["USER_STORE"] = users

    def current_user():
        return users.get(session.get("user_id"))

    def audit_identity(user) -> str:
        return f"{user.display_name} [{user.username}] ({user.role})"

    @app.before_request
    def require_authentication():
        g.current_user = current_user()
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if users.count() == 0:
            return redirect(url_for("setup_admin"))
        if g.current_user is None:
            return redirect(url_for("login", next=request.path))
        return None

    @app.context_processor
    def auth_context():
        return {"current_user": getattr(g, "current_user", None)}

    @app.route("/setup", methods=["GET", "POST"])
    def setup_admin():
        if users.count() > 0:
            return redirect(url_for("login"))
        if request.method == "POST":
            try:
                user = users.create_user(
                    request.form.get("username", ""),
                    request.form.get("display_name", ""),
                    "Administrator",
                    request.form.get("password", ""),
                )
                session.clear()
                session["user_id"] = user.user_id
                app.config["AUDIT_LOG"].append(
                    "user.bootstrap.created", "SECURITY",
                    {"user_id": user.user_id, "username": user.username, "role": user.role},
                    actor=audit_identity(user),
                )
                return redirect(url_for("dashboard"))
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template("setup_admin.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if users.count() == 0:
            return redirect(url_for("setup_admin"))
        if request.method == "POST":
            user = users.authenticate(request.form.get("username", ""), request.form.get("password", ""))
            if user is None:
                flash("Invalid username or password", "error")
            else:
                session.clear()
                session["user_id"] = user.user_id
                app.config["AUDIT_LOG"].append(
                    "user.login", "SECURITY", {"user_id": user.user_id}, actor=audit_identity(user)
                )
                return redirect(request.args.get("next") or url_for("dashboard"))
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        user = current_user()
        if user:
            app.config["AUDIT_LOG"].append(
                "user.logout", "SECURITY", {"user_id": user.user_id}, actor=audit_identity(user)
            )
        session.clear()
        return redirect(url_for("login"))

    @app.route("/settings/users", methods=["GET", "POST"])
    def user_settings():
        user = current_user()
        if not is_admin(user):
            flash("Administrator role required", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            try:
                created = users.create_user(
                    request.form.get("username", ""),
                    request.form.get("display_name", ""),
                    request.form.get("role", ""),
                    request.form.get("password", ""),
                )
                app.config["AUDIT_LOG"].append(
                    "user.created", "SECURITY",
                    {"user_id": created.user_id, "username": created.username, "role": created.role},
                    actor=audit_identity(user),
                )
                flash("User created", "success")
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template(
            "user_settings.html",
            users=users.list_users(),
            approver_roles=app.config["COMPANY_CONFIG"].profile.approver_roles,
        )

    # Replace sensitive handlers so identity comes from the authenticated session.
    original_company_settings = app.view_functions["update_company_settings"]

    @wraps(original_company_settings)
    def secured_company_settings(*args, **kwargs):
        user = current_user()
        if not is_admin(user):
            flash("Administrator role required", "error")
            return redirect(url_for("company_settings"))
        return original_company_settings(*args, **kwargs)

    app.view_functions["update_company_settings"] = secured_company_settings

    def secured_approve_document():
        user = current_user()
        book = app.config["BOOKKEEPER"]
        company = app.config["COMPANY_CONFIG"]
        provenance = app.config["PROVENANCE"]
        audit = app.config["AUDIT_LOG"]
        amount = Decimal(request.form.get("amount", "0"))
        if company.profile.requires_extra_approval(amount) and not is_approver(user, company.profile.approver_roles):
            flash("This amount requires an authorized approver", "error")
            return redirect(url_for("dashboard"))
        try:
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
                record_id=approved["record_id"], vendor=approved["vendor"], amount=amount,
                reference=approved["reference"], work_order_id=approved["work_order_id"],
                record_type=approved["record_type"], treatment=treatment,
                linked_cost_id=approved["linked_cost_id"],
            )
            record_type = "cost" if record.id in book.costs else "vendor_bill"
            provenance.bind_record(evidence_id, record_type, record.id)
            audit.append(
                "document.approved", evidence_id,
                {"proposal": proposed, "approved": approved, "changes": changes,
                 "result": {"record_type": record_type, "record_id": record.id},
                 "extra_approval_required": company.profile.requires_extra_approval(amount),
                 "authenticated_user_id": user.user_id},
                actor=audit_identity(user),
            )
            app.config["SAVE_BOOKKEEPER"]()
            flash("Document approved with authenticated authority", "success")
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    app.view_functions["approve_document"] = secured_approve_document

    original_propose = app.view_functions["propose_cost_correction"]

    @wraps(original_propose)
    def secured_propose(cost_id, *args, **kwargs):
        # Bookkeepers may propose; actor identity is additionally captured in auth audit.
        result = original_propose(cost_id, *args, **kwargs)
        user = current_user()
        app.config["AUDIT_LOG"].append(
            "cost.correction.proposer.authenticated", f"COST:{cost_id}",
            {"user_id": user.user_id}, actor=audit_identity(user),
        )
        return result

    app.view_functions["propose_cost_correction"] = secured_propose

    original_approve = app.view_functions["approve_cost_correction"]

    @wraps(original_approve)
    def secured_correction_approval(correction_id, *args, **kwargs):
        user = current_user()
        if not is_approver(user, app.config["COMPANY_CONFIG"].profile.approver_roles):
            flash("Authorized approver role required", "error")
            return redirect(url_for("correction_dashboard"))
        result = original_approve(correction_id, *args, **kwargs)
        app.config["AUDIT_LOG"].append(
            "cost.correction.approver.authenticated", f"CORRECTION:{correction_id}",
            {"user_id": user.user_id}, actor=audit_identity(user),
        )
        return result

    app.view_functions["approve_cost_correction"] = secured_correction_approval
    return users
