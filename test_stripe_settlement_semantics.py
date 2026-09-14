from decimal import Decimal

from connectors import DEPOSITS_READ, PAYMENTS_READ, SETTLEMENTS_READ
from stripe_settlement_connector import StripeSettlementConnector


def test_stripe_connector_separates_processor_settlements_from_bank_deposits(monkeypatch):
    connector = StripeSettlementConnector("sk_test")

    def fake_get(path, params=None):
        if path == "payment_intents":
            return {
                "data": [{
                    "id": "pi_1",
                    "status": "succeeded",
                    "amount_received": 10000,
                    "metadata": {"invoice_id": "INV-1"},
                }],
                "has_more": False,
            }
        if path == "payouts":
            return {
                "data": [{"id": "po_1", "amount": 9700, "description": "Sep payout"}],
                "has_more": False,
            }
        raise AssertionError(path)

    monkeypatch.setattr(connector, "_get", fake_get)

    assert PAYMENTS_READ in connector.capabilities
    assert SETTLEMENTS_READ in connector.capabilities
    assert DEPOSITS_READ not in connector.capabilities

    payments = connector.pull_payments()
    settlements = connector.pull_settlements()

    assert payments[0].id == "STRIPE-PAY:pi_1"
    assert payments[0].amount == Decimal("100")
    assert settlements[0].settlement_id == "STRIPE-SET:po_1"
    assert settlements[0].reported_net == Decimal("97")
    assert settlements[0].provider == "Stripe"


def test_stripe_payout_is_not_exposed_as_bank_deposit_capability():
    connector = StripeSettlementConnector("sk_test")
    assert connector not in []  # explicit construction smoke check
    assert "deposits.read" not in connector.capabilities
