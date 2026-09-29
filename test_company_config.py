from decimal import Decimal

from company_config import CompanyConfigStore, CompanyProfile
from web_app import create_app


def test_company_profile_persists(tmp_path):
    store = CompanyConfigStore(tmp_path / "company.json")
    store.update_from_strings(
        name="Wise Plumbing",
        job_label="Service Call",
        customer_label="Client",
        vendor_bill_mode="create_cost",
        approval_threshold="5000",
        approver_roles="Owner, Office Manager",
        field_service_system="Jobber",
        accounting_system="QuickBooks",
        bank_system="Bank feed",
        document_system="OpenAI",
    )

    loaded = CompanyConfigStore(tmp_path / "company.json").profile
    assert loaded.name == "Wise Plumbing"
    assert loaded.job_label == "Service Call"
    assert loaded.customer_label == "Client"
    assert loaded.vendor_bill_mode == "create_cost"
    assert loaded.approval_threshold == Decimal("5000")
    assert loaded.approver_roles == ("Owner", "Office Manager")
    assert loaded.field_service_system == "Jobber"
    assert loaded.accounting_system == "QuickBooks"


def test_threshold_policy_is_general():
    profile = CompanyProfile(approval_threshold=Decimal("1000"))
    assert not profile.requires_extra_approval(Decimal("999.99"))
    assert profile.requires_extra_approval(Decimal("1000"))
    assert profile.requires_extra_approval(Decimal("2500"))


def test_company_settings_route_updates_bookkeeper_policy(tmp_path):
    data_path = tmp_path / "bookkeeper.json"
    app = create_app(str(data_path))
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.post("/settings/company", data={
        "name": "Acme Service Co",
        "job_label": "Project",
        "customer_label": "Account",
        "vendor_bill_mode": "support_cost",
        "approval_threshold": "2500",
        "approver_roles": "Owner, Controller",
        "field_service_system": "Housecall Pro",
        "accounting_system": "Xero",
        "bank_system": "Stripe",
        "document_system": "OpenAI",
    })

    assert response.status_code == 302
    assert app.config["BOOKKEEPER"].vendor_bill_mode == "support_cost"
    profile = app.config["COMPANY_CONFIG"].profile
    assert profile.name == "Acme Service Co"
    assert profile.job_label == "Project"
    assert profile.customer_label == "Account"
    assert profile.approval_threshold == Decimal("2500")


def test_company_settings_page_is_available(tmp_path):
    app = create_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    response = app.test_client().get("/settings/company")
    assert response.status_code == 200
    assert b"Company configuration" in response.data
    assert b"Vendor bills" in response.data
