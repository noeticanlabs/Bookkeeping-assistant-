from decimal import Decimal

import pytest

from approval_policy import ApprovalPolicy, ApprovalPolicyStore
from secure_web_app import create_secure_app


def setup_admin(client):
    client.post("/setup", data={
        "display_name": "Admin User", "username": "admin", "password": "administrator-pass"
    })


def login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


def logout(client):
    client.post("/logout")


def test_policy_prevents_self_approval(tmp_path):
    store = ApprovalPolicyStore(tmp_path / "approval.db")
    req = store.create_request("document", "DOC-1", "USR-A", {"x": 1}, Decimal("100"))
    with pytest.raises(ValueError, match="Proposer cannot approve"):
        store.record_approval(req.request_id, "USR-A")


def test_threshold_requires_two_distinct_approvers(tmp_path):
    store = ApprovalPolicyStore(tmp_path / "approval.db")
    store.save(ApprovalPolicy(prevent_self_approval=True, second_approval_threshold=Decimal("1000")))
    req = store.create_request("document", "DOC-2", "USR-P", {"x": 1}, Decimal("1500"))
    assert req.required_approvals == 2
    _, count, ready = store.record_approval(req.request_id, "USR-A")
    assert count == 1 and not ready
    with pytest.raises(ValueError, match="already approved"):
        store.record_approval(req.request_id, "USR-A")
    _, count, ready = store.record_approval(req.request_id, "USR-B")
    assert count == 2 and ready


def test_correction_does_not_change_books_until_independent_approval(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)
    users = app.config["USER_STORE"]
    users.create_user("books", "Book Keeper", "Bookkeeper", "bookkeeper-pass")
    users.create_user("owner", "Owner User", "Owner", "owner-password")

    # Seed a cost as admin.
    book = app.config["BOOKKEEPER"]
    from app import Cost, WorkOrder
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("500")))
    book.add_cost(Cost("C-1", "Vendor", Decimal("100"), "materials", "WO-1"))
    app.config["SAVE_BOOKKEEPER"]()

    logout(client)
    login(client, "books", "bookkeeper-pass")
    client.post("/costs/C-1/corrections", data={
        "replacement_cost_id": "C-1-R1",
        "vendor": "Vendor",
        "amount": "80",
        "work_order_id": "WO-1",
        "reference": "corrected",
        "reason": "Receipt corrected",
        "actor": "ignored",
    })
    assert book.job_cost("WO-1") == Decimal("100")
    req = app.config["APPROVAL_POLICY"].pending()[0]

    # Proposer cannot approve own correction.
    client.post(f"/approvals/{req.request_id}/approve")
    assert book.job_cost("WO-1") == Decimal("100")

    logout(client)
    login(client, "owner", "owner-password")
    client.post(f"/approvals/{req.request_id}/approve")
    assert book.job_cost("WO-1") == Decimal("80")
    assert book.costs["C-1"].superseded_by == "C-1-R1"
