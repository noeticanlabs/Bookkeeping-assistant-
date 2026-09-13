"""Deterministic bookkeeping verifier registry.

Verifiers inspect bookkeeping state and return PASS / FAIL / UNKNOWN with
supporting evidence. A verification result never grants posting authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable


PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class VerificationResult:
    verifier_id: str
    title: str
    status: str
    severity: str
    object_type: str
    object_id: str
    summary: str
    evidence: dict[str, str]
    review_required: bool = False


Verifier = Callable[[object], list[VerificationResult]]


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: list[tuple[str, Verifier]] = []

    def register(self, verifier_id: str, verifier: Verifier) -> None:
        if not verifier_id.strip():
            raise ValueError("Verifier ID is required")
        if any(existing == verifier_id for existing, _ in self._verifiers):
            raise ValueError(f"Duplicate verifier ID: {verifier_id}")
        self._verifiers.append((verifier_id, verifier))

    def run(self, book) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        for verifier_id, verifier in self._verifiers:
            for result in verifier(book):
                if result.verifier_id != verifier_id:
                    raise ValueError(f"Verifier {verifier_id} returned mismatched ID {result.verifier_id}")
                if result.status not in {PASS, FAIL, UNKNOWN}:
                    raise ValueError(f"Verifier {verifier_id} returned invalid status")
                results.append(result)
        rank = {FAIL: 0, UNKNOWN: 1, PASS: 2}
        severity_rank = {"S5": 5, "S4": 4, "S3": 3, "S2": 2, "S1": 1, "S0": 0}
        return sorted(
            results,
            key=lambda r: (rank[r.status], -severity_rank.get(r.severity, 0), r.verifier_id, r.object_id),
        )

    def summary(self, book) -> dict[str, int]:
        results = self.run(book)
        return {
            "pass": sum(1 for r in results if r.status == PASS),
            "fail": sum(1 for r in results if r.status == FAIL),
            "unknown": sum(1 for r in results if r.status == UNKNOWN),
            "review_required": sum(1 for r in results if r.review_required),
            "total": len(results),
        }


def _money(value: Decimal) -> str:
    return format(value, "f")


def verify_invoice_balances(book) -> list[VerificationResult]:
    """V-INVOICE-BALANCE: linked payments must agree with invoice paid state."""
    results: list[VerificationResult] = []
    for invoice in book.invoices.values():
        linked = [p for p in book.payments.values() if p.invoice_id == invoice.id]
        linked_total = sum((p.amount for p in linked), Decimal("0"))
        computed_remaining = invoice.total - linked_total
        recorded_remaining = invoice.balance_due
        ok = linked_total == invoice.amount_paid and computed_remaining == recorded_remaining
        results.append(VerificationResult(
            verifier_id="V-INVOICE-BALANCE",
            title="Invoice / payment balance identity",
            status=PASS if ok else FAIL,
            severity="S3" if not ok else "S0",
            object_type="invoice",
            object_id=invoice.id,
            summary=(
                "Linked payments agree with invoice paid and receivable state"
                if ok else
                "Invoice paid state does not agree with linked payment evidence"
            ),
            evidence={
                "invoice_total": _money(invoice.total),
                "linked_payment_total": _money(linked_total),
                "recorded_amount_paid": _money(invoice.amount_paid),
                "computed_remaining_receivable": _money(computed_remaining),
                "recorded_remaining_receivable": _money(recorded_remaining),
                "linked_payment_ids": ", ".join(sorted(p.id for p in linked)) or "none",
            },
            review_required=not ok,
        ))
    return results


def verify_processor_settlements(book) -> list[VerificationResult]:
    """V-PROCESSOR-SETTLEMENT: payment - fee must equal linked net deposit."""
    results: list[VerificationResult] = []
    for deposit in book.deposits.values():
        if not deposit.payment_id:
            results.append(VerificationResult(
                verifier_id="V-PROCESSOR-SETTLEMENT",
                title="Processor settlement identity",
                status=UNKNOWN,
                severity="S2",
                object_type="deposit",
                object_id=deposit.id,
                summary="Deposit has no linked customer payment, so settlement cannot be verified",
                evidence={
                    "deposit_amount": _money(deposit.amount),
                    "processor_fee": _money(deposit.processor_fee),
                    "payment_id": "none",
                },
                review_required=True,
            ))
            continue
        payment = book.payments.get(deposit.payment_id)
        if payment is None:
            results.append(VerificationResult(
                verifier_id="V-PROCESSOR-SETTLEMENT",
                title="Processor settlement identity",
                status=FAIL,
                severity="S3",
                object_type="deposit",
                object_id=deposit.id,
                summary="Deposit references a payment that does not exist",
                evidence={
                    "deposit_amount": _money(deposit.amount),
                    "processor_fee": _money(deposit.processor_fee),
                    "payment_id": deposit.payment_id,
                },
                review_required=True,
            ))
            continue
        difference = deposit.amount + deposit.processor_fee - payment.amount
        ok = difference == 0
        results.append(VerificationResult(
            verifier_id="V-PROCESSOR-SETTLEMENT",
            title="Processor settlement identity",
            status=PASS if ok else FAIL,
            severity="S3" if not ok else "S0",
            object_type="deposit",
            object_id=deposit.id,
            summary=(
                "Gross payment less processor fee agrees with net deposit"
                if ok else
                "Payment, fee, and deposit do not reconcile under the current settlement model"
            ),
            evidence={
                "payment_id": payment.id,
                "gross_payment": _money(payment.amount),
                "processor_fee": _money(deposit.processor_fee),
                "net_deposit": _money(deposit.amount),
                "difference": _money(difference),
            },
            review_required=not ok,
        ))
    return results


def verify_work_order_links(book) -> list[VerificationResult]:
    """V-WORK-ORDER-LINK: operational-to-financial references must resolve."""
    results: list[VerificationResult] = []

    for cost in book.current_costs():
        if not cost.work_order_id:
            status, severity, summary, review = UNKNOWN, "S2", "Cost is not linked to a work order", True
        elif cost.work_order_id not in book.work_orders:
            status, severity, summary, review = FAIL, "S3", "Cost references an unknown work order", True
        else:
            status, severity, summary, review = PASS, "S0", "Cost work-order relationship resolves", False
        results.append(VerificationResult(
            verifier_id="V-WORK-ORDER-LINK", title="Work-order relationship integrity",
            status=status, severity=severity, object_type="cost", object_id=cost.id,
            summary=summary,
            evidence={"work_order_id": cost.work_order_id or "none"}, review_required=review,
        ))

    for invoice in book.invoices.values():
        ok = invoice.work_order_id in book.work_orders
        results.append(VerificationResult(
            verifier_id="V-WORK-ORDER-LINK", title="Work-order relationship integrity",
            status=PASS if ok else FAIL, severity="S3" if not ok else "S0",
            object_type="invoice", object_id=invoice.id,
            summary="Invoice work-order relationship resolves" if ok else "Invoice references an unknown work order",
            evidence={"work_order_id": invoice.work_order_id}, review_required=not ok,
        ))

    for bill in book.vendor_bills.values():
        if bill.work_order_id is None:
            continue
        ok = bill.work_order_id in book.work_orders
        results.append(VerificationResult(
            verifier_id="V-WORK-ORDER-LINK", title="Work-order relationship integrity",
            status=PASS if ok else FAIL, severity="S3" if not ok else "S0",
            object_type="vendor_bill", object_id=bill.id,
            summary="Vendor bill work-order relationship resolves" if ok else "Vendor bill references an unknown work order",
            evidence={"work_order_id": bill.work_order_id}, review_required=not ok,
        ))
    return results


def verify_vendor_bill_treatment(book) -> list[VerificationResult]:
    """V-VENDOR-BILL-TREATMENT: one bill treatment must resolve to valid cost semantics."""
    results: list[VerificationResult] = []
    for bill in book.vendor_bills.values():
        evidence = {
            "treatment": bill.treatment or "none",
            "linked_cost_id": bill.linked_cost_id or "none",
            "bill_amount": _money(bill.amount),
            "work_order_id": bill.work_order_id or "none",
        }
        if bill.treatment is None:
            status, severity, summary, review = UNKNOWN, "S2", "Vendor bill treatment has not been decided", True
        elif bill.treatment == "overhead":
            ok = bill.linked_cost_id is None
            status, severity, summary, review = (
                (PASS, "S0", "Overhead bill is not linked into job cost", False)
                if ok else
                (FAIL, "S3", "Overhead bill is also linked to a job cost", True)
            )
        elif bill.treatment in {"create_cost", "support_cost"}:
            cost = book.costs.get(bill.linked_cost_id or "")
            if cost is None:
                status, severity, summary, review = FAIL, "S3", "Vendor bill treatment requires a linked cost that does not exist", True
            elif bill.work_order_id and cost.work_order_id and bill.work_order_id != cost.work_order_id:
                evidence["linked_cost_work_order_id"] = cost.work_order_id or "none"
                status, severity, summary, review = FAIL, "S3", "Vendor bill and linked cost refer to different work orders", True
            elif bill.treatment == "create_cost" and (cost.reference != bill.id or cost.amount != bill.amount or cost.vendor != bill.vendor):
                evidence.update({
                    "linked_cost_reference": cost.reference or "none",
                    "linked_cost_amount": _money(cost.amount),
                    "linked_cost_vendor": cost.vendor,
                })
                status, severity, summary, review = FAIL, "S3", "Created job cost no longer agrees with its vendor bill source", True
            else:
                status, severity, summary, review = PASS, "S0", "Vendor bill treatment resolves to valid cost semantics", False
        else:
            status, severity, summary, review = FAIL, "S3", "Vendor bill has an unsupported treatment", True

        results.append(VerificationResult(
            verifier_id="V-VENDOR-BILL-TREATMENT",
            title="Vendor bill / job-cost treatment",
            status=status, severity=severity,
            object_type="vendor_bill", object_id=bill.id,
            summary=summary, evidence=evidence, review_required=review,
        ))
    return results


def verify_job_margins(book) -> list[VerificationResult]:
    """V-JOB-MARGIN-CALC: deterministic margin calculation, not a profitability policy."""
    results: list[VerificationResult] = []
    for work_order in book.work_orders.values():
        invoice = book.invoice_for(work_order.id)
        cost = book.job_cost(work_order.id)
        if invoice is None:
            results.append(VerificationResult(
                verifier_id="V-JOB-MARGIN-CALC", title="Job margin calculation",
                status=UNKNOWN, severity="S0", object_type="work_order", object_id=work_order.id,
                summary="Margin cannot be calculated until the job has an invoice",
                evidence={"job_cost": _money(cost), "invoice_id": "none"}, review_required=False,
            ))
            continue
        margin = invoice.total - cost
        results.append(VerificationResult(
            verifier_id="V-JOB-MARGIN-CALC", title="Job margin calculation",
            status=PASS, severity="S0", object_type="work_order", object_id=work_order.id,
            summary="Job margin is deterministically calculable; this does not assert the margin is acceptable",
            evidence={
                "invoice_id": invoice.id,
                "job_revenue": _money(invoice.total),
                "job_cost": _money(cost),
                "margin": _money(margin),
            },
            review_required=False,
        ))
    return results


def default_registry() -> VerifierRegistry:
    registry = VerifierRegistry()
    registry.register("V-INVOICE-BALANCE", verify_invoice_balances)
    registry.register("V-PROCESSOR-SETTLEMENT", verify_processor_settlements)
    registry.register("V-WORK-ORDER-LINK", verify_work_order_links)
    registry.register("V-VENDOR-BILL-TREATMENT", verify_vendor_bill_treatment)
    registry.register("V-JOB-MARGIN-CALC", verify_job_margins)
    return registry
