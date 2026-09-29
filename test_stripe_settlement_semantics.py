from decimal import Decimal

from connectors import DEPOSITS_READ, PAYMENTS_READ, SETTLEMENTS_READ
from stripe_settlement_connector import StripeSettlementConnector


def test_stripe_connector_reconstructs_automatic_payout_from_balance_transactions(monkeypatch):
    connector = StripeSettlementConnector("sk_test")
    calls = []

    def fake_get(path, params=None):
        calls.append((path, dict(params or {})))
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
                "data": [{
                    "id": "po_1", "amount": 9700, "description": "Sep payout",
                    "automatic": True, "method": "standard",
                    "reconciliation_status": "completed",
                }],
                "has_more": False,
            }
        if path == "balance_transactions":
            assert params["payout"] == "po_1"
            assert params["expand[]"] == "data.source"
            return {
                "data": [{
                    "id": "txn_charge_1",
                    "amount": 10000,
                    "fee": 300,
                    "net": 9700,
                    "currency": "usd",
                    "reporting_category": "charge",
                    "type": "charge",
                    "description": "Customer payment",
                    "source": {"id": "ch_1", "object": "charge", "payment_intent": "pi_1"},
                }],
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
    evidence = settlements[0]
    assert evidence.settlement_id == "STRIPE-SET:po_1"
    assert evidence.reported_net == Decimal("97")
    assert evidence.provider == "Stripe"
    assert evidence.composition_complete is True
    assert len(evidence.components) == 1
    component = evidence.components[0]
    assert component.component_id == "txn_charge_1"
    assert component.kind == "payment"
    assert component.amount == Decimal("100")
    assert component.fee == Decimal("3")
    assert component.net == Decimal("97")
    assert component.payment_id == "STRIPE-PAY:pi_1"
    assert any(path == "balance_transactions" for path, _ in calls)


def test_stripe_payout_component_pull_paginates_and_keeps_exact_categories(monkeypatch):
    connector = StripeSettlementConnector("sk_test")

    def fake_get(path, params=None):
        if path == "payouts":
            return {
                "data": [{
                    "id": "po_2", "amount": 8700, "description": "Batch",
                    "automatic": True, "method": "standard",
                }],
                "has_more": False,
            }
        if path == "balance_transactions":
            if not params.get("starting_after"):
                return {
                    "data": [{
                        "id": "txn_1", "amount": 10000, "fee": 300, "net": 9700,
                        "currency": "usd", "reporting_category": "charge", "type": "charge",
                        "source": {"id": "ch_1", "object": "charge", "payment_intent": "pi_1"},
                    }],
                    "has_more": True,
                }
            assert params["starting_after"] == "txn_1"
            return {
                "data": [{
                    "id": "txn_2", "amount": -1000, "fee": 0, "net": -1000,
                    "currency": "usd", "reporting_category": "refund", "type": "refund",
                    "source": {"id": "re_1", "object": "refund", "payment_intent": "pi_1"},
                }],
                "has_more": False,
            }
        raise AssertionError(path)

    monkeypatch.setattr(connector, "_get", fake_get)
    evidence = connector.pull_settlements()[0]

    assert [c.component_id for c in evidence.components] == ["txn_1", "txn_2"]
    assert [c.kind for c in evidence.components] == ["payment", "refund"]
    assert sum((c.net for c in evidence.components), Decimal("0")) == Decimal("87")


def test_stripe_reconciliation_in_progress_waits_instead_of_guessing(monkeypatch):
    connector = StripeSettlementConnector("sk_test")

    def fake_get(path, params=None):
        if path == "payouts":
            return {
                "data": [{
                    "id": "po_pending", "amount": 9700, "automatic": True,
                    "method": "standard", "reconciliation_status": "in_progress",
                }],
                "has_more": False,
            }
        if path == "balance_transactions":
            raise AssertionError("in-progress reconciliation must wait for Stripe")
        raise AssertionError(path)

    monkeypatch.setattr(connector, "_get", fake_get)
    evidence = connector.pull_settlements()[0]

    assert evidence.components == ()
    assert evidence.composition_complete is False
    assert "still in progress" in evidence.composition_note


def test_stripe_manual_or_instant_payout_is_not_guessed(monkeypatch):
    connector = StripeSettlementConnector("sk_test")

    def fake_get(path, params=None):
        if path == "payouts":
            return {
                "data": [{
                    "id": "po_manual", "amount": 5000, "automatic": False,
                    "method": "instant", "description": "Instant payout",
                    "reconciliation_status": "not_applicable",
                }],
                "has_more": False,
            }
        if path == "balance_transactions":
            raise AssertionError("manual/instant payout composition must not be guessed")
        raise AssertionError(path)

    monkeypatch.setattr(connector, "_get", fake_get)
    evidence = connector.pull_settlements()[0]

    assert evidence.reported_net == Decimal("50")
    assert evidence.components == ()
    assert evidence.composition_complete is False
    assert "does not expose deterministic" in evidence.composition_note


def test_reporting_category_prevents_partial_capture_reversal_from_being_mislabeled_refund():
    connector = StripeSettlementConnector("sk_test")

    kind = connector._component_kind(
        "partial_capture_reversal",
        "refund",
        {"id": "ch_1", "object": "charge"},
    )

    assert kind == "other"


def test_unrecognized_stripe_balance_category_stays_explicit_other(monkeypatch):
    connector = StripeSettlementConnector("sk_test")

    def fake_get(path, params=None):
        if path == "payouts":
            return {
                "data": [{"id": "po_other", "amount": 1000, "automatic": True, "method": "standard"}],
                "has_more": False,
            }
        if path == "balance_transactions":
            return {
                "data": [{
                    "id": "txn_other", "amount": 1000, "fee": 0, "net": 1000,
                    "currency": "usd", "reporting_category": "reserve_release", "type": "reserve_release",
                    "source": None,
                }],
                "has_more": False,
            }
        raise AssertionError(path)

    monkeypatch.setattr(connector, "_get", fake_get)
    component = connector.pull_settlements()[0].components[0]

    assert component.kind == "other"
    assert component.reporting_category == "reserve_release"


def test_stripe_payout_is_not_exposed_as_bank_deposit_capability():
    connector = StripeSettlementConnector("sk_test")
    assert connector not in []  # explicit construction smoke check
    assert "deposits.read" not in connector.capabilities
