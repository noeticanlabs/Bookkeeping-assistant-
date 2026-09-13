from decimal import Decimal

from auth import UserStore, is_approver
from secure_web_app import create_secure_app


def bootstrap(client, username="admin", password="correct-horse-123"):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": username,
        "password": password,
    })


def test_first_run_redirects_to_admin_setup(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    response = app.test_client().get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/setup")


def test_bootstrap_creates_hashed_admin_and_session(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    response = bootstrap(client)
    assert response.status_code == 302
    users = app.config["USER_STORE"]
    assert users.count() == 1
    admin = users.list_users()[0]
    assert admin.role == "Administrator"
    assert users.authenticate("admin", "correct-horse-123").user_id == admin.user_id
    assert client.get("/").status_code == 200


def test_after_setup_logged_out_user_must_login(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)
    client.post("/logout")
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_non_admin_cannot_change_company_configuration(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    admin_client = app.test_client()
    bootstrap(admin_client)
    users = app.config["USER_STORE"]
    worker = users.create_user("worker", "Worker", "Bookkeeper", "bookkeeper-123")

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = worker.user_id
    response = client.post("/settings/company", data={
        "name": "Changed Illegally",
        "job_label": "Job",
        "customer_label": "Customer",
        "vendor_bill_mode": "ask",
        "approval_threshold": "100",
        "approver_roles": "Owner",
        "field_service_system": "none",
        "accounting_system": "none",
        "bank_system": "none",
        "document_system": "none",
    })
    assert response.status_code == 302
    assert app.config["COMPANY_CONFIG"].profile.name == "My Company"


def test_threshold_blocks_authenticated_non_approver(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    admin_client = app.test_client()
    bootstrap(admin_client)

    company = app.config["COMPANY_CONFIG"]
    company.profile.approval_threshold = Decimal("100")
    company.profile.approver_roles = ("Owner",)
    company.save()

    users = app.config["USER_STORE"]
    worker = users.create_user("worker", "Worker", "Bookkeeper", "bookkeeper-123")
    assert not is_approver(worker, company.profile.approver_roles)

    provenance = app.config["PROVENANCE"]
    source_file = tmp_path / "receipt.txt"
    source_file.write_text("evidence", encoding="utf-8")
    evidence = provenance.capture(source_file, "receipt.txt")
    provenance.add_extraction(evidence.evidence_id, {
        "vendor": "Vendor",
        "amount": "500",
        "reference": "R-1",
        "document_id": "COST-1",
        "work_order_id": None,
        "record_type": "cost",
    })

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = worker.user_id
    response = client.post("/documents/approve", data={
        "evidence_id": evidence.evidence_id,
        "record_id": "COST-1",
        "vendor": "Vendor",
        "amount": "500",
        "reference": "R-1",
        "record_type": "cost",
        "treatment": "ask",
    })
    assert response.status_code == 302
    assert "COST-1" not in app.config["BOOKKEEPER"].costs


def test_authorized_user_submits_document_then_independent_approver_posts_it(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    admin_client = app.test_client()
    bootstrap(admin_client)

    users = app.config["USER_STORE"]
    owner = users.create_user("owner", "Company Owner", "Owner", "company-owner-123")
    second_owner = users.create_user("owner2", "Second Owner", "Owner", "second-owner-123")

    provenance = app.config["PROVENANCE"]
    source_file = tmp_path / "receipt.txt"
    source_file.write_text("evidence", encoding="utf-8")
    evidence = provenance.capture(source_file, "receipt.txt")
    provenance.add_extraction(evidence.evidence_id, {
        "vendor": "Vendor", "amount": "500", "reference": "R-1",
        "document_id": "COST-1", "work_order_id": None, "record_type": "cost",
    })

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = owner.user_id
    client.post("/documents/approve", data={
        "evidence_id": evidence.evidence_id, "record_id": "COST-1", "vendor": "Vendor",
        "amount": "500", "reference": "R-1", "record_type": "cost", "treatment": "ask",
    })
    assert "COST-1" not in app.config["BOOKKEEPER"].costs
    req = app.config["APPROVAL_POLICY"].pending()[0]

    with client.session_transaction() as session:
        session["user_id"] = second_owner.user_id
    client.post(f"/approvals/{req.request_id}/approve")
    assert "COST-1" in app.config["BOOKKEEPER"].costs
    event = [e for e in app.config["AUDIT_LOG"].events() if e.event_type == "document.approved"][-1]
    assert second_owner.user_id in event.payload["approver_ids"]
    assert "Second Owner" in event.actor
