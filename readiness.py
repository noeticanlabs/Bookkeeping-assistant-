"""Configuration summary and readiness checks for company go-live."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import render_template, session


@dataclass(frozen=True)
class ReadinessItem:
    area: str
    status: str  # ready | declared | missing | warning
    title: str
    detail: str
    blocking: bool = False


def build_readiness(app) -> list[ReadinessItem]:
    profile = app.config["COMPANY_CONFIG"].profile
    users = app.config["USER_STORE"]
    permissions = app.config["PERMISSION_STORE"]
    workflows = app.config["WORKFLOW_POLICY"]
    approvals = app.config["APPROVAL_POLICY"]
    hub = app.config["CONNECTOR_HUB"]
    db_path = Path(app.config["BOOKKEEPER_DATA_PATH"])
    onboarding = app.config.get("ONBOARDING_STATE")

    items: list[ReadinessItem] = []

    if onboarding is not None and onboarding.completed():
        items.append(ReadinessItem("Company", "ready", "Company setup", f"Configured for {profile.name}."))
    else:
        items.append(ReadinessItem("Company", "missing", "Company setup", "Guided onboarding has not been completed.", True))

    active_users = [u for u in users.list_users() if u.active]
    if active_users:
        items.append(ReadinessItem("Security", "ready", "Authenticated users", f"{len(active_users)} active user(s) configured."))
    else:
        items.append(ReadinessItem("Security", "missing", "Authenticated users", "No active users are configured.", True))

    if permissions.permissions_for_role("Administrator"):
        items.append(ReadinessItem("Security", "ready", "Role permissions", "Role-based permissions are configured."))
    else:
        items.append(ReadinessItem("Security", "missing", "Role permissions", "Permission bundles are missing.", True))

    try:
        workflow_count = len(workflows.rules())
    except Exception:
        workflow_count = 0
    if workflow_count:
        policy = approvals.load()
        items.append(ReadinessItem(
            "Controls", "ready", "Workflow controls",
            f"{workflow_count} approval rule(s) configured; self-approval prevention is {'on' if policy.prevent_self_approval else 'off'}."
        ))
    else:
        items.append(ReadinessItem("Controls", "missing", "Workflow controls", "No approval workflow rules are configured.", True))

    connector_specs = (
        ("Field service", profile.field_service_system, hub.field_service),
        ("Accounting", profile.accounting_system, hub.accounting),
        ("Documents", profile.document_system, hub.documents),
    )
    for label, selected, live in connector_specs:
        selected = (selected or "none").strip()
        if selected == "none":
            items.append(ReadinessItem("Connections", "warning", label, "No external system selected; manual/CSV workflow remains available."))
        elif live is not None:
            items.append(ReadinessItem("Connections", "ready", label, f"{selected} is selected and a live adapter is loaded."))
        else:
            items.append(ReadinessItem("Connections", "declared", label, f"{selected} is selected, but no live adapter is loaded yet."))

    bank_selected = (profile.bank_system or "none").strip()
    if bank_selected == "none":
        items.append(ReadinessItem("Connections", "warning", "Bank/payment feed", "No bank/payment connector selected; CSV/manual deposit entry remains available."))
    else:
        items.append(ReadinessItem("Connections", "declared", "Bank/payment feed", f"{bank_selected} is declared; no dedicated bank connector is implemented yet."))

    if db_path.exists():
        items.append(ReadinessItem("Storage", "ready", "SQLite database", f"Authoritative database is present at {db_path.name}."))
    else:
        items.append(ReadinessItem("Storage", "missing", "SQLite database", "Authoritative SQLite database is not present.", True))

    provenance = app.config["PROVENANCE"]
    vault = Path(provenance.vault_dir) if hasattr(provenance, "vault_dir") else None
    if vault is not None and vault.exists():
        items.append(ReadinessItem("Evidence", "ready", "Document evidence vault", "Source-document evidence storage is available."))
    else:
        items.append(ReadinessItem("Evidence", "warning", "Document evidence vault", "Evidence vault will be created when the first source document is captured."))

    return items


def install_readiness(app):
    @app.get("/readiness")
    def readiness_summary():
        items = build_readiness(app)
        blockers = [item for item in items if item.blocking]
        ready = len(blockers) == 0
        return render_template(
            "readiness.html",
            items=items,
            ready=ready,
            blocker_count=len(blockers),
            profile=app.config["COMPANY_CONFIG"].profile,
        )

    return build_readiness
