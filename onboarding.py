"""Guided company onboarding that compiles simple business answers into app policy."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import flash, redirect, render_template, request, session, url_for


class OnboardingState:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS onboarding_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), completed INTEGER NOT NULL DEFAULT 0)"
            )

    def completed(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT completed FROM onboarding_state WHERE singleton=1").fetchone()
        return bool(row and row[0])

    def mark_complete(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO onboarding_state(singleton,completed) VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET completed=1"
            )


def install_onboarding(app, db_path):
    state = OnboardingState(db_path)
    app.config["ONBOARDING_STATE"] = state

    users = app.config["USER_STORE"]
    permissions = app.config["PERMISSION_STORE"]
    company = app.config["COMPANY_CONFIG"]
    workflows = app.config["WORKFLOW_POLICY"]
    approvals = app.config["APPROVAL_POLICY"]

    def current_user():
        return users.get(session.get("user_id"))

    def identity(user):
        return f"{user.display_name} [{user.username}] ({user.role})"

    @app.route("/onboarding", methods=["GET", "POST"])
    def company_onboarding():
        user = current_user()
        if user is None:
            return redirect(url_for("login", next=request.path))
        if not permissions.user_has(user, "company.configure"):
            flash("Company-configuration permission required", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            try:
                routine_limit_raw = request.form.get("routine_direct_limit", "").strip()
                high_value_raw = request.form.get("two_approval_threshold", "").strip()
                routine_limit = Decimal(routine_limit_raw) if routine_limit_raw else None
                high_value = Decimal(high_value_raw) if high_value_raw else None
                if routine_limit is not None and routine_limit < 0:
                    raise ValueError("Routine direct-post limit cannot be negative")
                if high_value is not None and high_value < 0:
                    raise ValueError("Two-approval threshold cannot be negative")
                if routine_limit is not None and high_value is not None and high_value < routine_limit:
                    raise ValueError("Two-approval threshold must be at or above the routine direct-post limit")

                company.update_from_strings(
                    name=request.form.get("name", ""),
                    job_label=request.form.get("job_label", "Work Order"),
                    customer_label=request.form.get("customer_label", "Customer"),
                    vendor_bill_mode=request.form.get("vendor_bill_mode", "ask"),
                    approval_threshold=high_value_raw,
                    approver_roles="Owner,Administrator",
                    field_service_system=request.form.get("field_service_system", "none"),
                    accounting_system=request.form.get("accounting_system", "none"),
                    bank_system=request.form.get("bank_system", "none"),
                    document_system=request.form.get("document_system", "openai"),
                )
                app.config["BOOKKEEPER"].vendor_bill_mode = company.profile.vendor_bill_mode
                app.config["SAVE_BOOKKEEPER"]()

                routine_direct = request.form.get("routine_direct") == "yes"
                base = 0 if routine_direct else 1
                for action_type in ("document_cost", "vendor_bill"):
                    bands = [(None, base)]
                    if routine_limit is not None and routine_direct:
                        bands = [(None, 0), (routine_limit, 1)]
                    if high_value is not None:
                        bands.append((high_value, 2))
                    workflows.set_rules(action_type, bands)
                correction_bands = [(None, 1)]
                if high_value is not None:
                    correction_bands.append((high_value, 2))
                workflows.set_rules("cost_correction", correction_bands)

                policy = approvals.load()
                policy.prevent_self_approval = request.form.get("prevent_self_approval") == "yes"
                policy.second_approval_threshold = high_value
                approvals.save(policy)

                # Preserve safe defaults, but tailor whether bookkeepers may directly approve documents.
                bookkeeper_permissions = permissions.permissions_for_role("Bookkeeper")
                if routine_direct:
                    bookkeeper_permissions.add("documents.approve")
                else:
                    bookkeeper_permissions.discard("documents.approve")
                permissions.set_role_permissions("Bookkeeper", bookkeeper_permissions)

                state.mark_complete()
                app.config["AUDIT_LOG"].append(
                    "company.onboarding.completed", "COMPANY",
                    {
                        "company": company.profile.name,
                        "routine_direct": routine_direct,
                        "routine_direct_limit": str(routine_limit) if routine_limit is not None else None,
                        "two_approval_threshold": str(high_value) if high_value is not None else None,
                        "prevent_self_approval": policy.prevent_self_approval,
                        "field_service_system": company.profile.field_service_system,
                        "accounting_system": company.profile.accounting_system,
                        "bank_system": company.profile.bank_system,
                        "document_system": company.profile.document_system,
                    },
                    actor=identity(user),
                )
                flash("Company setup complete", "success")
                return redirect(url_for("dashboard"))
            except (ValueError, InvalidOperation) as exc:
                flash(str(exc), "error")

        return render_template(
            "onboarding.html",
            profile=company.profile,
            completed=state.completed(),
        )

    return state
