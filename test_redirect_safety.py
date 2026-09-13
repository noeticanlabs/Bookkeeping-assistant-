from secure_web_app import create_secure_app


def _setup_admin(client):
    client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def test_external_login_next_is_neutralized(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    _setup_admin(client)
    client.post("/logout")

    response = client.post(
        "/login?next=https://evil.example/phish",
        data={"username": "admin", "password": "administrator-pass"},
    )
    assert response.status_code in (302, 303)
    assert response.headers["Location"] == "/"


def test_same_origin_login_next_is_allowed(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    _setup_admin(client)
    client.post("/logout")

    response = client.post(
        "/login?next=/readiness",
        data={"username": "admin", "password": "administrator-pass"},
    )
    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/readiness")
