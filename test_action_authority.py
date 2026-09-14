import sqlite3
from decimal import Decimal

import pytest

from app import WorkOrder
from connectors import ConnectorHub, INVOICES_WRITE, PAYMENTS_READ, WORK_ORDERS_READ
from permissions import PermissionStore
from secure_web_app import create_secure_app


class AuthorityProbeConnector:
    name = "Authority Probe"
    capabilities = frozenset({WORK_ORDERS_READ, PAYMENTS_READ, INVOICES_WRITE})

    def __init__(self):
        self.work_order_pulls = 0
        self.payment_pulls = 0
        self.invoice_pushes = 0

    def pull_work_orders(self):
        self.work_order_pulls += 1
        return []

    def pull_payments(self):
        self.payment_pulls += 1
        return []

    def push_invoice(self, invoice):
        self.invoice_pushes += 1
        return f"REMOTE:{invoice.id}"


def setup_admin(client):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


def logout(client):
    return client.post("/logout")


def build_app(tmp_path):
    connector = AuthorityProbeConnector()
    hub = ConnectorHub()
    hub.register(connector)
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    users = app.config["USER_STORE"]
    users.create_user("viewer", "Viewer User", "Viewer", "viewer-password")
    users.create_user("books", "Bookkeeper User", "Bookkeeper", "bookkeeper-password")
    users.create_user("owner", "Owner User", "Owner", "owner-password")
    return app, client, connector


def make_draft_invoice(app, suffix: str):
    book = app.config["BOOKKEEPER"]
    work_order_id = f"WO-AUTH-{suffix}"
    book.add_work_order(WorkOrder(work_order_id, f"Customer {suffix}", "Repair", "complete", Decimal("100")))
    return book.prepare_invoice(work_order_id)


def test_final_replacement_routes_carry_governed_action_metadata(tmp_path):
    app, _, _ = build_app(tmp_path)
    assert app.view_functions["sync_field_service"]._governed_action == "sync.run"
    assert app.view_functions["sync_accounting"]._governed_action == "sync.run"
    assert app.view_functions["issue_invoice"]._governed_action == "invoice.issue"


def test_viewer_cannot_reach_replaced_sync_or_invoice_handlers(tmp_path):
    app, client, connector = build_app(tmp_path)
    invoice = make_draft_invoice(app, "VIEWER")

    logout(client)
    login(client, "viewer", "viewer-password")

    field_response = client.post("/sync/field-service", follow_redirects=True)
    accounting_response = client.post("/sync/accounting", follow_redirects=True)
    issue_response = client.post(f"/invoices/{invoice.id}/issue", follow_redirects=True)

    assert connector.work_order_pulls == 0
    assert connector.payment_pulls == 0
    assert connector.invoice_pushes == 0
    assert invoice.status == "draft"
    assert b"sync.run permission required" in field_response.data
    assert b"sync.run permission required" in accounting_response.data
    assert b"invoice.issue permission required" in issue_response.data

    denied = [event for event in app.config["AUDIT_LOG"].events() if event.event_type == "authority.denied"]
    assert {event.payload["action"] for event in denied} >= {"sync.run", "invoice.issue"}


@pytest.mark.parametrize(
    "username,password,role",
    [
        ("books", "bookkeeper-password", "Bookkeeper"),
        ("owner", "owner-password", "Owner"),
        ("admin", "administrator-pass", "Administrator"),
    ],
)
def test_default_operational_roles_can_run_governed_actions(tmp_path, username, password, role):
    app, client, connector = build_app(tmp_path)
    invoice = make_draft_invoice(app, role.upper())

    logout(client)
    login(client, username, password)

    client.post("/sync/field-service")
    client.post("/sync/accounting")
    client.post(f"/invoices/{invoice.id}/issue")

    assert connector.work_order_pulls == 1
    assert connector.payment_pulls == 1
    assert connector.invoice_pushes == 1
    assert invoice.status == "issued"


def test_invoice_issue_permission_is_independent_from_legacy_bookkeeping_write(tmp_path):
    app, client, connector = build_app(tmp_path)
    invoice = make_draft_invoice(app, "GRANULAR")
    permissions = app.config["PERMISSION_STORE"]

    current = permissions.permissions_for_role("Bookkeeper")
    assert "bookkeeping.write" in current
    assert "invoice.issue" in current
    permissions.set_role_permissions("Bookkeeper", current - {"invoice.issue"})

    logout(client)
    login(client, "books", "bookkeeper-password")
    response = client.post(f"/invoices/{invoice.id}/issue", follow_redirects=True)

    assert connector.invoice_pushes == 0
    assert invoice.status == "draft"
    assert b"invoice.issue permission required" in response.data

    restarted = PermissionStore(app.config["BOOKKEEPER_DATA_PATH"])
    assert "invoice.issue" not in restarted.permissions_for_role("Bookkeeper")
    assert "bookkeeping.write" in restarted.permissions_for_role("Bookkeeper")


def test_sync_run_permission_is_independent_from_legacy_bookkeeping_write(tmp_path):
    app, client, connector = build_app(tmp_path)
    permissions = app.config["PERMISSION_STORE"]

    current = permissions.permissions_for_role("Bookkeeper")
    assert "bookkeeping.write" in current
    assert "sync.run" in current
    permissions.set_role_permissions("Bookkeeper", current - {"sync.run"})

    logout(client)
    login(client, "books", "bookkeeper-password")
    response = client.post("/sync/field-service", follow_redirects=True)

    assert connector.work_order_pulls == 0
    assert b"sync.run permission required" in response.data


def test_existing_legacy_bookkeeping_write_roles_migrate_to_new_action_permissions(tmp_path):
    path = tmp_path / "legacy-auth.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE role_permissions(role TEXT PRIMARY KEY, permissions TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO role_permissions(role,permissions) VALUES(?,?)",
            ("Bookkeeper", '["records.read", "bookkeeping.write"]'),
        )

    migrated = PermissionStore(path).permissions_for_role("Bookkeeper")
    assert "bookkeeping.write" in migrated
    assert "sync.run" in migrated
    assert "invoice.issue" in migrated
