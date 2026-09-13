from auth import UserStore
from permissions import PERMISSIONS, PermissionStore
from secure_web_app import create_secure_app


def setup_admin(client):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


def logout(client):
    client.post("/logout")


def test_default_permission_bundles(tmp_path):
    store = PermissionStore(tmp_path / "auth.db")
    assert "bookkeeping.write" in store.permissions_for_role("Bookkeeper")
    assert "corrections.approve" not in store.permissions_for_role("Bookkeeper")
    assert store.permissions_for_role("Viewer") == {"records.read"}
    assert store.permissions_for_role("Administrator") == set(PERMISSIONS)


def test_administrator_permissions_cannot_be_reduced(tmp_path):
    store = PermissionStore(tmp_path / "auth.db")
    try:
        store.set_role_permissions("Administrator", {"records.read"})
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert store.permissions_for_role("Administrator") == set(PERMISSIONS)


def test_viewer_cannot_write_bookkeeping(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    users = app.config["USER_STORE"]
    users.create_user("viewer", "View User", "Viewer", "viewer-password")
    logout(client)
    login(client, "viewer", "viewer-password")

    response = client.post("/work-orders", data={
        "id": "WO-DENIED", "customer": "Smith", "description": "Repair",
        "quoted_total": "100", "status": "complete",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "WO-DENIED" not in app.config["BOOKKEEPER"].work_orders
    assert b"Bookkeeping-write permission required" in response.data


def test_bookkeeper_can_write_until_company_removes_permission(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    users = app.config["USER_STORE"]
    users.create_user("books", "Book Keeper", "Bookkeeper", "bookkeeper-pass")

    logout(client)
    login(client, "books", "bookkeeper-pass")
    client.post("/work-orders", data={
        "id": "WO-OK", "customer": "Smith", "description": "Repair",
        "quoted_total": "100", "status": "complete",
    })
    assert "WO-OK" in app.config["BOOKKEEPER"].work_orders

    logout(client)
    login(client, "admin", "administrator-pass")
    remaining = sorted(PermissionStore(app.config["BOOKKEEPER_DATA_PATH"]).permissions_for_role("Bookkeeper") - {"bookkeeping.write"})
    response = client.post("/settings/permissions/Bookkeeper", data={"permissions": remaining})
    assert response.status_code == 302

    logout(client)
    login(client, "books", "bookkeeper-pass")
    client.post("/work-orders", data={
        "id": "WO-BLOCKED", "customer": "Jones", "description": "Repair",
        "quoted_total": "200", "status": "complete",
    })
    assert "WO-BLOCKED" not in app.config["BOOKKEEPER"].work_orders


def test_only_users_manage_permission_can_edit_role_permissions(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    app.config["USER_STORE"].create_user("owner", "Owner User", "Owner", "owner-password")
    logout(client)
    login(client, "owner", "owner-password")
    response = client.get("/settings/permissions", follow_redirects=True)
    assert response.status_code == 200
    assert b"User-management permission required" in response.data
