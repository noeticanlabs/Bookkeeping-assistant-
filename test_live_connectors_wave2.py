from decimal import Decimal

import live_connectors
from app import Invoice
from connectors import DEPOSITS_READ, INVOICES_WRITE, PAYMENTS_READ, WORK_ORDERS_READ
from live_connectors import ServiceTitanConnector, StripeConnector, XeroConnector


def test_xero_imports_authorised_receipt_payment_as_evidence(monkeypatch):
    def fake_request(url, **kwargs):
        assert "/Payments" in url
        return {
            "Payments": [{
                "PaymentID": "pay-1",
                "Amount": 125.50,
                "Reference": "INV-22",
                "PaymentType": "ACCRECPAYMENT",
                "Status": "AUTHORISED",
            }]
        }

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    connector = XeroConnector("tenant", "token")
    payments = connector.pull_payments()
    assert connector.capabilities == frozenset({PAYMENTS_READ, INVOICES_WRITE})
    assert payments[0].id == "XERO-PAY:pay-1"
    assert payments[0].amount == Decimal("125.5")
    assert payments[0].reference == "INV-22"
    assert payments[0].invoice_id is None


def test_xero_invoice_export_requires_explicit_contact_mapping(monkeypatch):
    invoice = Invoice("INV-22", "WO-1", "Smith Residence", Decimal("500"))
    connector = XeroConnector("tenant", "token", contact_ids={}, revenue_account_code="200")

    try:
        connector.push_invoice(invoice)
        assert False, "expected explicit contact mapping requirement"
    except live_connectors.ConnectorError as exc:
        assert "ContactID mapping" in str(exc)

    captured = {}

    def fake_request(url, **kwargs):
        captured.update(kwargs["body"])
        return {"Invoices": [{"InvoiceID": "xero-invoice-guid"}]}

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    connector = XeroConnector(
        "tenant", "token",
        contact_ids={"Smith Residence": "contact-guid"},
        revenue_account_code="200",
    )
    external_id = connector.push_invoice(invoice)
    assert external_id == "xero-invoice-guid"
    exported = captured["Invoices"][0]
    assert exported["Contact"]["ContactID"] == "contact-guid"
    assert exported["Reference"] == "WO-1"
    assert exported["LineItems"][0]["UnitAmount"] == 500.0


def test_stripe_normalizes_successful_payments_and_paid_payouts(monkeypatch):
    def fake_request(url, **kwargs):
        if "/payment_intents" in url:
            return {
                "has_more": False,
                "data": [
                    {"id": "pi_ok", "status": "succeeded", "amount_received": 12345,
                     "metadata": {"invoice_id": "INV-9"}},
                    {"id": "pi_wait", "status": "processing", "amount": 9999, "metadata": {}},
                ],
            }
        if "/payouts" in url:
            return {
                "has_more": False,
                "data": [{"id": "po_1", "status": "paid", "amount": 12000, "description": "Stripe payout"}],
            }
        raise AssertionError(url)

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    connector = StripeConnector("sk_test_example")
    assert connector.capabilities == frozenset({PAYMENTS_READ, DEPOSITS_READ})
    payments = connector.pull_payments()
    deposits = connector.pull_deposits()
    assert len(payments) == 1
    assert payments[0].id == "STRIPE-PAY:pi_ok"
    assert payments[0].amount == Decimal("123.45")
    assert payments[0].reference == "INV-9"
    assert payments[0].invoice_id is None
    assert deposits[0].id == "STRIPE-DEP:po_1"
    assert deposits[0].amount == Decimal("120")
    assert deposits[0].payment_id is None


def test_servicetitan_contributes_jobs_only(monkeypatch):
    def fake_request(url, **kwargs):
        assert kwargs["headers"]["ST-App-Key"] == "app-key"
        return {
            "data": [{
                "jobId": "777",
                "customerDisplay": "Jones Residence",
                "summaryText": "No cooling",
                "jobStatus": "Completed",
            }]
        }

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    connector = ServiceTitanConnector(
        jobs_url="https://example.test/jobs",
        access_token="token",
        app_key="app-key",
        field_map={
            "id": "jobId",
            "customer": "customerDisplay",
            "description": "summaryText",
            "status": "jobStatus",
        },
    )
    jobs = connector.pull_work_orders()
    assert connector.capabilities == frozenset({WORK_ORDERS_READ})
    assert jobs[0].id == "ST:777"
    assert jobs[0].customer == "Jones Residence"
    assert jobs[0].description == "No cooling"
    assert jobs[0].status == "complete"
    assert jobs[0].quoted_total is None
