from cryptography.fernet import Fernet

from connectors import ConnectorHub
from secure_web_app import create_secure_app
from readiness import build_readiness


def bootstrap_and_onboard(app, client):
    client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })
    client.post("/onboarding", data={
        "name": "Test Service Co",
        "job_label": "Job",
        "customer_label": "Customer",
        "field_service_system": "Jobber",
        "accounting_system": "QuickBooks",
        "bank_system": "Bank Feed",
        "document_system": "OpenAI",
        "vendor_bill_mode": "ask",
        "routine_direct": "yes",
        "routine_direct_limit": "250",
        "two_approval_threshold": "5000",
        "prevent_self_approval": "yes",
    })


def test_readiness_distinguishes_declared_from_connected(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub())
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap_and_onboard(app, client)

    items = build_readiness(app)
    by_title = {item.title: item for item in items}
    assert by_title["Company setup"].status == "ready"
    assert by_title["SQLite database"].status == "ready"
    assert by_title["Field service"].status == "declared"
    assert by_title["Accounting"].status == "declared"
    assert by_title["Documents"].status == "declared"
    assert by_title["Bank/payment feed"].status == "declared"


def test_readiness_page_renders_blocking_state_before_onboarding(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub())
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })
    response = client.get("/readiness")
    assert response.status_code == 200
    assert b"Not ready for go-live" in response.data
    assert b"Guided onboarding has not been completed" in response.data


def test_readiness_page_reports_core_ready_after_onboarding(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub())
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap_and_onboard(app, client)
    response = client.get("/readiness")
    assert response.status_code == 200
    assert b"Core configuration is ready" in response.data
    assert b"selected, but no live adapter is loaded yet" in response.data


def test_managed_oauth_waiting_for_authorization_is_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKKEEPER_CREDENTIAL_KEY", Fernet.generate_key().decode("ascii"))
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub())
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap_and_onboard(app, client)

    store = app.config["CONNECTION_STORE"]
    store.save(
        "quickbooks", "QuickBooks Online", ["payments.read", "deposits.read"],
        {
            "client_id": "client",
            "client_secret": "secret",
            "redirect_uri": "http://localhost/oauth/quickbooks/callback",
            "scope": "com.intuit.quickbooks.accounting",
        },
        status="authorization_required",
    )

    items = build_readiness(app)
    qbo = next(item for item in items if item.title.startswith("QuickBooks Online connection"))
    assert qbo.blocking is True
    assert qbo.status == "missing"
    assert "Authorization has not been completed" in qbo.detail
