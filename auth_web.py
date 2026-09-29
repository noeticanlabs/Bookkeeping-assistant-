"""Authentication and fine-grained authorization for the Flask bookkeeping app."""

from __future__ import annotations

from decimal import Decimal
from functools import wraps

from flask import flash, g, redirect, render_template, request, session, url_for

from auth import UserStore
from authority import requires_action
from document_intake import record_approved_document
from permissions import PERMISSIONS, PermissionStore


PUBLIC_ENDPOINTS = {"login", "logout", "setup_admin", "static"}


def install_auth(app, db_path) -> UserStore:
    users = UserStore(db_path)
    permissions = PermissionStore(db_path)
    app.config["USER_STORE"] = users
    app.config["PERMISSION_STORE"] = permissions

    def current_user():
        return users.get(session.get("user_id"))

    def audit_identity(user) -> str:
        return f"{user.display_name} [{user.username}] ({user.role})"

    def has(permission: str) -> bool:
        return permissions.user_has(current_user(), permission)

    def deny(message: str = "You do not have permission for this action"):
        flash(message, "error")
        return redirect(url_for("dashboard"))

    @app.before_request
    def require_authentication():
        g.current_user = current_user()
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if users.count() == 0:
            return redirect(url_for("setup_admin"))
        if g.current_user is None:
            return redirect(url_for("login", next=request.path))
        if request.endpoint not in {"user_settings", "role_permissions", "update_role_permissions", "company_settings"}:
            if not permissions.user_has(g.current_user, "records.read"):
                return deny("Your role does not allow access to bookkeeping records")
        return None

    @app.context_processor
    def auth_context():
        user = getattr(g, "current_user", None)
        return {
            "current_user": user,
            "has_permission": lambda name: permissions.user_has(user, name),
        }

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
        if not permissions.user_has(user, "users.manage"):
            return deny("User-management permission required")
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
        return render_template("user_settings.html", users=users.list_users(), roles=permissions.roles())

    @app.get("/settings/permissions")
    def role_permissions():
        user = current_user()
        if not permissions.user_has(user, "users.manage"):
            return deny("User-management permission required")
        return render_template(
            "role_permissions.html",
            roles=permissions.roles(),
            all_permissions=PERMISSIONS,
        )

    @app.post("/settings/permissions/<role>")
    def update_role_permissions(role: str):
        user = current_user()
        if not permissions.user_has(user, "users.manage"):
            return deny("User-management permission required")
        try:
            selected = {p for p in request.form.getlist("permissions") if p in PERMISSIONS}
            before = sorted(permissions.permissions_for_role(role))
            permissions.set_role_permissions(role, selected)
            app.config["AUDIT_LOG"].append(
                "role.permissions.updated", "SECURITY",
                {"role": role, "before": before, "after": sorted(selected)},
                actor=audit_identity(user),
            )
            flash(f"Permissions updated for {role}", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("role_permissions"))

    # Company configuration is governed by an explicit permission.
    original_company_settings = app.view_functions["update_company_settings"]

    @wraps(original_company_settings)
    def secured_company_settings(*args, **kwargs):
        if not has("company.configure"):
            return deny("Company-configuration permission required")
        return original_company_settings(*args, **kwargs)

    app.view_functions["update_company_settings"] = secured_company_settings

    # Generic bookkeeping writes still use the legacy broad permission. Sync,
    # invoice issuance, and document submission are excluded because they carry
    # action-specific authority checks.
    write_endpoints = {
        "add_work_order", "import_work_order_csv",
        "import_payment_csv", "import_deposit_csv",
        "prepare_invoice", "add_payment", "accept_payment_suggestion",
        "add_deposit", "accept_deposit_suggestion", "seed_demo",
    }
    for endpoint in write_endpoints:
        if endpoint not in app.view_functions:
            continue
        original = app.view_functions[endpoint]

        def make_guard(view):
            @wraps(view)
            def guarded(*args, **kwargs):
                if not has("bookkeeping.write"):
                    return deny("Bookkeeping-write permission required")
                return view(*args, **kwargs)
            return guarded

        app.view_functions[endpoint] = make_guard(original)

    app.view_functions["extract_document"] = requires_action(app, "document.submit")(
        app.view_functions["extract_document"]
    )

    def secured_approve_document():
        user = current_user()
        if not permissions.user_has(user, "documents.approve"):
            return deny("Document-approval permission required")
        book = app.config["BOOKKEEPER"]
        company = app.config["COMPANY_CONFIG"]
        provenance = app.config["PROVENANCE"]
        audit = app.config["AUDIT_LOG"]
        amount = Decimal(request.form.get("amount", "0"))
        if company.profile.requires_extra_approval(amount) and not permissions.user_has(user, "corrections.approve"):
            return deny("This amount requires elevated approval permission")
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
        user = current_user()
        if not permissions.user_has(user, "corrections.propose"):
            return deny("Correction-proposal permission required")
        result = original_propose(cost_id, *args, **kwargs)
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
        if not permissions.user_has(user, "corrections.approve"):
            return deny("Correction-approval permission required")
        result = original_approve(correction_id, *args, **kwargs)
        app.config["AUDIT_LOG"].append(
            "cost.correction.approver.authenticated", f"CORRECTION:{correction_id}",
            {"user_id": user.user_id}, actor=audit_identity(user),
        )
        return result

    app.view_functions["approve_cost_correction"] = secured_correction_approval

    original_reject = app.view_functions["reject_cost_correction"]

    @wraps(original_reject)
    def secured_correction_rejection(correction_id, *args, **kwargs):
        if not has("corrections.approve"):
            return deny("Correction-approval permission required")
        return original_reject(correction_id, *args, **kwargs)

    app.view_functions["reject_cost_correction"] = secured_correction_rejection
    return users
