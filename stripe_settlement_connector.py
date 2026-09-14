"""Stripe adapter with processor-settlement semantics.

Successful PaymentIntents are customer-payment evidence. Stripe payouts are
processor settlement evidence, not bank deposits. The actual bank deposit must
come from an independent bank/accounting source.
"""

from __future__ import annotations

from decimal import Decimal

from connectors import PAYMENTS_READ, SETTLEMENTS_READ
from live_connectors import StripeConnector
from settlements import SettlementEvidence


class StripeSettlementConnector(StripeConnector):
    def __init__(self, secret_key: str, name: str = "Stripe",
                 base_url: str = "https://api.stripe.com/v1"):
        super().__init__(
            secret_key=secret_key,
            name=name,
            capabilities=frozenset({PAYMENTS_READ, SETTLEMENTS_READ}),
            base_url=base_url,
        )

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
                result.append(SettlementEvidence(
                    settlement_id=f"STRIPE-SET:{payout_id}",
                    provider="Stripe",
                    reported_net=amount,
                    reference=str(row.get("description") or payout_id),
                ))
            if not payload.get("has_more") or not rows:
                break
            starting_after = rows[-1].get("id")
            if not starting_after:
                break
        return result
