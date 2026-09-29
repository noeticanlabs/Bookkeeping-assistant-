"""Configuration summary and readiness checks for company go-live."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import render_template

from connection_health import connection_health


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
    connection_store = app.config.get("CONNECTION_STORE")

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

    encryption_ready = bool(app.config.get("CREDENTIAL_ENCRYPTION_CONFIGURED"))
    if encryption_ready:
        items.append(ReadinessItem("Security", "ready", "Connector credential encryption", "Encrypted managed-connection storage is enabled."))
    else:
        production = bool(app.config.get("BOOKKEEPER_PRODUCTION"))
        items.append(ReadinessItem(
            "Security", "missing" if production else "warning", "Connector credential encryption",
            "BOOKKEEPER_CREDENTIAL_KEY is not configured; in-app managed connections are disabled.", production,
        ))

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
        ("Field service", profile.field_service_system, bool(hub.work_order_sources())),
        ("Accounting", profile.accounting_system, bool(hub.payment_sources() or hub.deposit_sources() or hub.invoice_sinks())),
        ("Documents", profile.document_system, hub.documents is not None),
    )
    for label, selected, live in connector_specs:
        selected = (selected or "none").strip()
        if selected == "none":
            items.append(ReadinessItem("Connections", "warning", label, "No external system selected; manual/CSV workflow remains available."))
        elif live:
            items.append(ReadinessItem("Connections", "ready", label, f"{selected} is selected and a live capability adapter is loaded."))
        else:
            items.append(ReadinessItem("Connections", "declared", label, f"{selected} is selected, but no live adapter is loaded yet."))

    bank_selected = (profile.bank_system or "none").strip()
    bank_live = bool(hub.deposit_sources())
    if bank_selected == "none" and not bank_live:
        items.append(ReadinessItem("Connections", "warning", "Bank/payment feed", "No bank/payment connector selected; CSV/manual deposit entry remains available."))
    elif bank_live:
        items.append(ReadinessItem("Connections", "ready", "Bank/payment feed", "A live deposit/payment-source capability is loaded."))
    else:
        items.append(ReadinessItem("Connections", "declared", "Bank/payment feed", f"{bank_selected} is declared, but no live deposit-source adapter is loaded."))

    if connection_store is not None:
        for record in connection_store.list():
            health = connection_health(connection_store, record.connection_id)
            state = str(health.get("state") or "unknown")
            detail = str(health.get("detail") or "")
            identity = health.get("identity")
            suffix = f" ({identity})" if identity else ""
            title = f"{record.label} connection{suffix}"
            if state in {"connected", "configured", "refresh_due"}:
                status = "ready" if state != "refresh_due" else "warning"
                items.append(ReadinessItem("Connections", status, title, detail))
            elif state in {"authorization_required", "reauthorize_required", "expired", "error"}:
                items.append(ReadinessItem("Connections", "missing", title, detail, True))
            else:
                items.append(ReadinessItem("Connections", "warning", title, detail))

    sync = app.config.get("SYNC_RELIABILITY")
    if sync is not None:
        uncertain = [item for item in sync.list_outbox() if item.status == "uncertain"]
        if uncertain:
            items.append(ReadinessItem(
                "Synchronization", "missing", "Uncertain outbound deliveries",
                f"{len(uncertain)} outbound item(s) may already exist in a remote system. Verify them before retrying.", True,
            ))
        else:
            items.append(ReadinessItem(
                "Synchronization", "ready", "Outbound delivery state",
                "No uncertain external writes are waiting for operator verification.",
            ))

        latest: dict[tuple[str, str], object] = {}
        for run in sync.list_runs():
            latest.setdefault((run.connector_id, run.capability), run)
        failed_latest = [run for run in latest.values() if run.status == "failed"]
        if failed_latest:
            items.append(ReadinessItem(
                "Synchronization", "warning", "Latest connector sync",
                f"{len(failed_latest)} connector/capability sync(s) most recently failed. Review /sync-status before relying on automated data.",
            ))
        elif latest:
            items.append(ReadinessItem(
                "Synchronization", "ready", "Latest connector sync",
                "The latest recorded sync for each exercised connector capability succeeded.",
            ))

    if db_path.exists():
        items.append(ReadinessItem("Storage", "ready", "SQLite database", f"Authoritative database is present at {db_path.name}."))
    else:
        items.append(ReadinessItem("Storage", "missing", "SQLite database", "Authoritative SQLite database is not present.", True))

    provenance = app.config["PROVENANCE"]
    vault = Path(provenance.document_dir)
    if vault.exists():
        items.append(ReadinessItem("Evidence", "ready", "Document evidence vault", "Source-document evidence storage is available."))
    else:
        items.append(ReadinessItem("Evidence", "warning", "Document evidence vault", "Evidence vault will be created when the first source document is captured."))

    return items


def install_readiness(app):
    @app.get("/readiness")
    def readiness_summary():
        items = build_readiness(app)
        blockers = [item for item in items if item.blocking]
        return render_template(
            "readiness.html",
            items=items,
            ready=len(blockers) == 0,
            blocker_count=len(blockers),
            profile=app.config["COMPANY_CONFIG"].profile,
        )

    return build_readiness
