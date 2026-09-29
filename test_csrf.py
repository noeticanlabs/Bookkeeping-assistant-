import re

from secure_web_app import create_secure_app


def _token_from(response):
    text = response.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', text)
    assert match, text
    return match.group(1)


def test_setup_form_receives_csrf_token(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=False, CSRF_PROTECTION_ENABLED=True)
    client = app.test_client()
    response = client.get("/setup")
    assert response.status_code == 200
    assert _token_from(response)


def test_missing_csrf_token_is_rejected(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=False, CSRF_PROTECTION_ENABLED=True)
    client = app.test_client()
    client.get("/setup")
    response = client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })
    assert response.status_code == 400
    assert app.config["USER_STORE"].count() == 0


def test_invalid_csrf_token_is_rejected(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=False, CSRF_PROTECTION_ENABLED=True)
    client = app.test_client()
    client.get("/setup")
    response = client.post("/setup", data={
        "csrf_token": "wrong-token",
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })
    assert response.status_code == 400
    assert app.config["USER_STORE"].count() == 0


def test_valid_csrf_token_allows_state_change(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=False, CSRF_PROTECTION_ENABLED=True)
    client = app.test_client()
    token = _token_from(client.get("/setup"))
    response = client.post("/setup", data={
        "csrf_token": token,
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })
    assert response.status_code in (302, 303)
    assert app.config["USER_STORE"].count() == 1


def test_csrf_header_is_accepted_for_non_form_clients(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=False, CSRF_PROTECTION_ENABLED=True)
    client = app.test_client()
    token = _token_from(client.get("/setup"))
    response = client.post("/setup", headers={"X-CSRF-Token": token}, data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })
    assert response.status_code in (302, 303)
    assert app.config["USER_STORE"].count() == 1
