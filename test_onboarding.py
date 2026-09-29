from decimal import Decimal

from secure_web_app import create_secure_app


def bootstrap(client):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def test_first_admin_is_sent_to_company_onboarding(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    response = bootstrap(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/onboarding")


def test_onboarding_compiles_business_answers_into_configuration(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)

    response = client.post("/onboarding", data={
        "name": "Wise Plumbing",
        "job_label": "Service Call",
        "customer_label": "Client",
        "field_service_system": "Jobber",
        "accounting_system": "QuickBooks",
        "bank_system": "Bank Feed",
        "document_system": "openai",
        "vendor_bill_mode": "create_cost",
        "routine_direct": "yes",
        "routine_direct_limit": "250",
        "two_approval_threshold": "5000",
        "prevent_self_approval": "yes",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")

    profile = app.config["COMPANY_CONFIG"].profile
    assert profile.name == "Wise Plumbing"
    assert profile.job_label == "Service Call"
    assert profile.customer_label == "Client"
    assert profile.vendor_bill_mode == "create_cost"
    assert profile.field_service_system == "Jobber"
    assert profile.accounting_system == "QuickBooks"
    assert app.config["BOOKKEEPER"].vendor_bill_mode == "create_cost"

    workflows = app.config["WORKFLOW_POLICY"]
    assert workflows.approvals_required("document_cost", Decimal("100")) == 0
    assert workflows.approvals_required("document_cost", Decimal("250")) == 1
    assert workflows.approvals_required("document_cost", Decimal("5000")) == 2
    assert workflows.approvals_required("vendor_bill", Decimal("5000")) == 2
    assert workflows.approvals_required("cost_correction", Decimal("100")) == 1
    assert workflows.approvals_required("cost_correction", Decimal("5000")) == 2

    policy = app.config["APPROVAL_POLICY"].load()
    assert policy.prevent_self_approval is True
    assert policy.second_approval_threshold == Decimal("5000")
    assert app.config["ONBOARDING_STATE"].completed() is True


def test_onboarding_can_make_bookkeeper_submission_only(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)
    client.post("/onboarding", data={
        "name": "Controlled Service Co",
        "job_label": "Job",
        "customer_label": "Customer",
        "field_service_system": "none",
        "accounting_system": "QuickBooks",
        "bank_system": "none",
        "document_system": "openai",
        "vendor_bill_mode": "ask",
        "routine_direct": "no",
        "routine_direct_limit": "",
        "two_approval_threshold": "10000",
        "prevent_self_approval": "yes",
    })
    bookkeeper_permissions = app.config["PERMISSION_STORE"].permissions_for_role("Bookkeeper")
    assert "documents.approve" not in bookkeeper_permissions
    assert app.config["WORKFLOW_POLICY"].approvals_required("document_cost", Decimal("100")) == 1


def test_onboarding_rejects_inverted_thresholds(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)
    response = client.post("/onboarding", data={
        "name": "Bad Policy Co",
        "job_label": "Job",
        "customer_label": "Customer",
        "field_service_system": "none",
        "accounting_system": "none",
        "bank_system": "none",
        "document_system": "openai",
        "vendor_bill_mode": "ask",
        "routine_direct": "yes",
        "routine_direct_limit": "1000",
        "two_approval_threshold": "500",
        "prevent_self_approval": "yes",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Two-approval threshold must be at or above the routine direct-post limit" in response.data
    assert app.config["ONBOARDING_STATE"].completed() is False
