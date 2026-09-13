from decimal import Decimal

from secure_web_app import create_secure_app
from workflow_policy import WorkflowPolicyStore


def bootstrap(client):
    client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def test_workflow_rule_selects_highest_applicable_band(tmp_path):
    store = WorkflowPolicyStore(tmp_path / "policy.db")
    store.set_rules("vendor_bill", [(None, 0), (Decimal("250"), 1), (Decimal("5000"), 2)])
    assert store.approvals_required("vendor_bill", Decimal("100")) == 0
    assert store.approvals_required("vendor_bill", Decimal("250")) == 1
    assert store.approvals_required("vendor_bill", Decimal("4999.99")) == 1
    assert store.approvals_required("vendor_bill", Decimal("5000")) == 2


def test_company_can_configure_workflow_rule(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)
    response = client.post("/settings/workflows", data={
        "action_type": "document_cost",
        "base_approvals": "0",
        "threshold": "1000",
        "threshold_approvals": "2",
    })
    assert response.status_code == 200
    policy = app.config["WORKFLOW_POLICY"]
    assert policy.approvals_required("document_cost", Decimal("500")) == 0
    assert policy.approvals_required("document_cost", Decimal("1000")) == 2


def test_zero_approval_document_posts_directly(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)
    app.config["WORKFLOW_POLICY"].set_rules("document_cost", [(None, 0)])

    source = tmp_path / "receipt.txt"
    source.write_text("receipt", encoding="utf-8")
    evidence = app.config["PROVENANCE"].capture(source, "receipt.txt")
    app.config["PROVENANCE"].add_extraction(evidence.evidence_id, {
        "vendor": "Vendor", "amount": "125", "reference": "R-1",
        "document_id": "COST-DIRECT", "work_order_id": None, "record_type": "cost",
    })

    response = client.post("/documents/approve", data={
        "evidence_id": evidence.evidence_id,
        "record_id": "COST-DIRECT",
        "vendor": "Vendor",
        "amount": "125",
        "reference": "R-1",
        "record_type": "cost",
        "treatment": "ask",
    })
    assert response.status_code == 302
    assert "COST-DIRECT" in app.config["BOOKKEEPER"].costs
    assert app.config["APPROVAL_POLICY"].pending() == []


def test_two_approval_rule_keeps_document_pending(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    bootstrap(client)
    app.config["WORKFLOW_POLICY"].set_rules("document_cost", [(None, 1), (Decimal("5000"), 2)])

    source = tmp_path / "large.txt"
    source.write_text("receipt", encoding="utf-8")
    evidence = app.config["PROVENANCE"].capture(source, "large.txt")
    app.config["PROVENANCE"].add_extraction(evidence.evidence_id, {
        "vendor": "Vendor", "amount": "7500", "reference": "R-2",
        "document_id": "COST-LARGE", "work_order_id": None, "record_type": "cost",
    })
    client.post("/documents/approve", data={
        "evidence_id": evidence.evidence_id,
        "record_id": "COST-LARGE",
        "vendor": "Vendor",
        "amount": "7500",
        "reference": "R-2",
        "record_type": "cost",
        "treatment": "ask",
    })
    assert "COST-LARGE" not in app.config["BOOKKEEPER"].costs
    pending = app.config["APPROVAL_POLICY"].pending()
    assert len(pending) == 1
    assert pending[0].required_approvals == 2
