"""Versioned correction proposals for approved direct costs."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app import Bookkeeper, Cost


@dataclass
class CostCorrection:
    correction_id: str
    original_cost_id: str
    replacement_cost_id: str
    proposed_vendor: str
    proposed_amount: str
    proposed_work_order_id: str | None
    proposed_reference: str | None
    reason: str
    proposed_by: str
    proposed_at: str
    status: str = "pending"
    approved_by: str | None = None
    approved_at: str | None = None


class CorrectionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.corrections: dict[str, CostCorrection] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for row in data.get("corrections", []):
            correction = CostCorrection(**row)
            self.corrections[correction.correction_id] = correction

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"corrections": [asdict(c) for c in self.corrections.values()]}, indent=2),
            encoding="utf-8",
        )

    def propose(
        self,
        book: Bookkeeper,
        *,
        original_cost_id: str,
        replacement_cost_id: str,
        vendor: str,
        amount: Decimal,
        work_order_id: str | None,
        reference: str | None,
        reason: str,
        proposed_by: str,
    ) -> CostCorrection:
        if original_cost_id not in book.costs:
            raise ValueError("Unknown original cost")
        original = book.costs[original_cost_id]
        if not original.is_current:
            raise ValueError("Only the current cost version can be corrected")
        if not replacement_cost_id.strip():
            raise ValueError("Replacement cost ID is required")
        if replacement_cost_id in book.costs:
            raise ValueError("Replacement cost ID already exists")
        if amount <= 0:
            raise ValueError("Corrected amount must be greater than zero")
        if work_order_id and work_order_id not in book.work_orders:
            raise ValueError("Unknown work order")
        if not reason.strip():
            raise ValueError("Correction reason is required")
        if not proposed_by.strip():
            raise ValueError("Proposed by is required")
        if any(c.original_cost_id == original_cost_id and c.status == "pending" for c in self.corrections.values()):
            raise ValueError("This cost already has a pending correction")

        correction = CostCorrection(
            correction_id=f"CORR-{uuid.uuid4().hex[:12]}",
            original_cost_id=original_cost_id,
            replacement_cost_id=replacement_cost_id.strip(),
            proposed_vendor=vendor.strip(),
            proposed_amount=str(amount),
            proposed_work_order_id=work_order_id,
            proposed_reference=reference.strip() if reference else None,
            reason=reason.strip(),
            proposed_by=proposed_by.strip(),
            proposed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.corrections[correction.correction_id] = correction
        self.save()
        return correction

    def approve(self, book: Bookkeeper, correction_id: str, approved_by: str) -> Cost:
        if correction_id not in self.corrections:
            raise ValueError("Unknown correction")
        correction = self.corrections[correction_id]
        if correction.status != "pending":
            raise ValueError("Correction is no longer pending")
        if not approved_by.strip():
            raise ValueError("Approved by is required")

        original = book.costs[correction.original_cost_id]
        replacement = Cost(
            id=correction.replacement_cost_id,
            vendor=correction.proposed_vendor,
            amount=Decimal(correction.proposed_amount),
            kind=original.kind,
            work_order_id=correction.proposed_work_order_id,
            reference=correction.proposed_reference,
            correction_of=original.id,
        )
        book.supersede_cost(original.id, replacement)
        correction.status = "approved"
        correction.approved_by = approved_by.strip()
        correction.approved_at = datetime.now(timezone.utc).isoformat()
        self.save()
        return replacement

    def reject(self, correction_id: str, rejected_by: str) -> CostCorrection:
        if correction_id not in self.corrections:
            raise ValueError("Unknown correction")
        correction = self.corrections[correction_id]
        if correction.status != "pending":
            raise ValueError("Correction is no longer pending")
        if not rejected_by.strip():
            raise ValueError("Rejected by is required")
        correction.status = "rejected"
        correction.approved_by = rejected_by.strip()
        correction.approved_at = datetime.now(timezone.utc).isoformat()
        self.save()
        return correction
