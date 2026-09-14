"""Stripe adapter with processor-settlement semantics.

Successful PaymentIntents are customer-payment evidence. Stripe payouts are
processor settlement evidence, not bank deposits. For reconciled automatic
standard payouts, composition is reconstructed from Stripe Balance Transactions
filtered by payout. The actual bank deposit must still come from an independent
bank/accounting source.
"""

from __future__ import annotations

from decimal import Decimal

from connectors import PAYMENTS_READ, SETTLEMENTS_READ
from live_connectors import StripeConnector
from settlements import SettlementComponentEvidence, SettlementEvidence


class StripeSettlementConnector(StripeConnector):
    def __init__(self, secret_key: str, name: str = "Stripe",
                 base_url: str = "https://api.stripe.com/v1"):
        super().__init__(
            secret_key=secret_key,
            name=name,
            capabilities=frozenset({PAYMENTS_READ, SETTLEMENTS_READ}),
            base_url=base_url,
        )

    @staticmethod
    def _object_id(value) -> str | None:
        if isinstance(value, dict):
            value = value.get("id")
        text = str(value or "").strip()
        return text or None

    @classmethod
    def _payment_id_from_source(cls, source) -> str | None:
        if not isinstance(source, dict):
            return None
        object_type = str(source.get("object") or "").lower()
        if object_type == "payment_intent":
            payment_intent_id = cls._object_id(source)
        else:
            payment_intent_id = cls._object_id(source.get("payment_intent"))
        if not payment_intent_id:
            return None
        return f"STRIPE-PAY:{payment_intent_id}"

    @staticmethod
    def _component_kind(reporting_category: str, transaction_type: str, source) -> str:
        """Map only accounting classes we explicitly understand.

        Stripe's reporting_category is preferred because Stripe documents it as
        the finance/reporting-oriented grouping. Raw type/source information is
        retained and used only as a conservative fallback.
        """
        category = reporting_category.lower()
        tx_type = transaction_type.lower()
        source_type = str(source.get("object") or "").lower() if isinstance(source, dict) else ""

        if category == "refund":
            return "refund"
        if "dispute" in category or "chargeback" in category:
            return "chargeback"
        if category == "charge":
            return "payment"
        if category == "fee":
            return "fee"

        text = " ".join((tx_type, source_type))
        if "refund" in text:
            return "refund"
        if "dispute" in text or "chargeback" in text:
            return "chargeback"
        if tx_type in {"charge", "payment"} or source_type == "charge":
            return "payment"
        if tx_type in {"stripe_fee", "stripe_fx_fee", "tax_fee"}:
            return "fee"
        return "other"

    def _pull_payout_components(self, payout_id: str) -> tuple[SettlementComponentEvidence, ...]:
        result: list[SettlementComponentEvidence] = []
        starting_after = None
        while True:
            params = {
                "limit": 100,
                "payout": payout_id,
                "expand[]": "data.source",
            }
            if starting_after:
                params["starting_after"] = starting_after
            payload = self._get("balance_transactions", params)
            rows = payload.get("data") or []
            for row in rows:
                component_id = str(row.get("id") or "").strip()
                if not component_id:
                    continue
                source = row.get("source")
                reporting_category = str(row.get("reporting_category") or "").strip()
                transaction_type = str(row.get("type") or "").strip()
                amount = Decimal(str(row.get("amount") or 0)) / Decimal("100")
                fee = Decimal(str(row.get("fee") or 0)) / Decimal("100")
                net = Decimal(str(row.get("net") or 0)) / Decimal("100")
                result.append(SettlementComponentEvidence(
                    component_id=component_id,
                    kind=self._component_kind(reporting_category, transaction_type, source),
                    amount=amount,
                    fee=fee,
                    net=net,
                    currency=str(row.get("currency") or "").lower(),
                    reporting_category=reporting_category,
                    transaction_type=transaction_type,
                    source_id=self._object_id(source),
                    payment_id=self._payment_id_from_source(source),
                    description=str(row.get("description") or "").strip() or None,
                ))
            if not payload.get("has_more") or not rows:
                break
            starting_after = rows[-1].get("id")
            if not starting_after:
                break
        return tuple(result)

    def pull_settlements(self) -> list[SettlementEvidence]:
        result: list[SettlementEvidence] = []
        starting_after = None
        while True:
            params = {"limit": 100, "status": "paid"}
            if starting_after:
                params["starting_after"] = starting_after
            payload = self._get("payouts", params)
            rows = payload.get("data") or []
            for row in rows:
                payout_id = str(row.get("id") or "").strip()
                if not payout_id:
                    continue
                amount = Decimal(str(row.get("amount") or 0)) / Decimal("100")
                if amount <= 0:
                    continue
                reference = str(row.get("description") or payout_id)
                automatic = row.get("automatic") is True
                method = str(row.get("method") or "standard").lower()
                reconciliation_status = str(row.get("reconciliation_status") or "").lower()

                # Stripe explicitly declares when payout composition is queryable.
                # The fallback preserves compatibility with older/mock responses
                # that predate reconciliation_status in this adapter.
                composition_ready = reconciliation_status == "completed" or (
                    not reconciliation_status and automatic and method != "instant"
                )

                if composition_ready:
                    components = self._pull_payout_components(payout_id)
                    result.append(SettlementEvidence(
                        settlement_id=f"STRIPE-SET:{payout_id}",
                        provider="Stripe",
                        reported_net=amount,
                        reference=reference,
                        components=components,
                        composition_complete=True,
                        composition_note=(
                            f"Automatic Stripe payout reconstructed from {len(components)} balance transaction(s)"
                        ),
                    ))
                elif reconciliation_status == "in_progress":
                    result.append(SettlementEvidence(
                        settlement_id=f"STRIPE-SET:{payout_id}",
                        provider="Stripe",
                        reported_net=amount,
                        reference=reference,
                        composition_complete=False,
                        composition_note=(
                            "Stripe payout reconciliation is still in progress; component reconstruction will retry on a later sync"
                        ),
                    ))
                else:
                    result.append(SettlementEvidence(
                        settlement_id=f"STRIPE-SET:{payout_id}",
                        provider="Stripe",
                        reported_net=amount,
                        reference=reference,
                        composition_complete=False,
                        composition_note=(
                            "Stripe does not expose deterministic transaction composition for this payout"
                        ),
                    ))
            if not payload.get("has_more") or not rows:
                break
            starting_after = rows[-1].get("id")
            if not starting_after:
                break
        return result
