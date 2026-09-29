"""Operational intelligence built from deterministic bookkeeping state.

No AI is required here. The module converts current bookkeeping state into a
small exception queue and a pre-invoice readiness decision for completed jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExceptionItem:
    kind: str
    severity: str
    reference_id: str
    message: str
    required_action: str


@dataclass(frozen=True)
class InvoiceReadiness:
    work_order_id: str
    status: str
    proposed_total: Decimal | None
    job_cost: Decimal
    projected_margin: Decimal | None
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


SEVERITY_BY_REVIEW_KIND = {
    "unbilled_job": "S2",
    "invoice_review": "S2",
    "unknown_job": "S3",
    "unassigned_cost": "S2",
    "vendor_bill_treatment": "S2",
    "unmatched_payment": "S3",
    "unmatched_deposit": "S3",
    "deposit_difference": "S3",
}

ACTION_BY_REVIEW_KIND = {
    "unbilled_job": "Review job and prepare invoice",
    "invoice_review": "Review invoice against completed job",
    "unknown_job": "Correct or reject the invalid job relationship",
    "unassigned_cost": "Assign to a job or classify as overhead",
    "vendor_bill_treatment": "Choose create-cost, support-cost, or overhead treatment",
    "unmatched_payment": "Match payment to an invoice or classify the receipt",
    "unmatched_deposit": "Match deposit to payment/settlement evidence",
    "deposit_difference": "Resolve processor fee, refund, chargeback, or unexplained difference",
}


def invoice_readiness(book, work_order_id: str) -> InvoiceReadiness:
    if work_order_id not in book.work_orders:
        raise ValueError("Unknown work order")
    wo = book.work_orders[work_order_id]
    existing = book.invoice_for(work_order_id)
    blockers: list[str] = []
    warnings: list[str] = []

    if existing is not None:
        blockers.append(f"Work order already has invoice {existing.id}")
    if wo.status != "complete":
        blockers.append("Work order is not complete")
    if wo.quoted_total is None or wo.quoted_total <= 0:
        blockers.append("No positive invoice total is available from the work order")

    untreated = [
        bill.id for bill in book.vendor_bills.values()
        if bill.work_order_id == work_order_id and bill.treatment is None
    ]
    if untreated:
        blockers.append("Vendor bill treatment is unresolved: " + ", ".join(sorted(untreated)))

    job_cost = book.job_cost(work_order_id)
    if job_cost == 0:
        warnings.append("No current job costs are recorded; verify whether this job legitimately has no direct cost")

    proposed_total = wo.quoted_total if wo.quoted_total and wo.quoted_total > 0 else None
    projected_margin = proposed_total - job_cost if proposed_total is not None else None

    if existing is not None:
        status = "already_invoiced"
    elif blockers:
        status = "needs_attention"
    else:
        status = "ready"

    return InvoiceReadiness(
        work_order_id=work_order_id,
        status=status,
        proposed_total=proposed_total,
        job_cost=job_cost,
        projected_margin=projected_margin,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def completed_job_readiness(book) -> list[InvoiceReadiness]:
    return [
        invoice_readiness(book, wo.id)
        for wo in book.work_orders.values()
        if wo.status == "complete"
    ]


def exception_queue(book, verifier_registry=None) -> list[ExceptionItem]:
    """Return human-attention items. Unknown state becomes review, never a guess.

    Deterministic verifier FAIL results are added when a registry is supplied.
    UNKNOWN results remain visible on the verification page but are not mislabeled
    as contradictions here; ordinary review rules already surface missing evidence.
    """
    result: list[ExceptionItem] = []
    seen: set[tuple[str, str, str]] = set()

    for item in book.review():
        key = (item.kind, item.reference_id, item.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(ExceptionItem(
            kind=item.kind,
            severity=SEVERITY_BY_REVIEW_KIND.get(item.kind, "S1"),
            reference_id=item.reference_id,
            message=item.message,
            required_action=ACTION_BY_REVIEW_KIND.get(item.kind, "Review supporting evidence"),
        ))

    # Invoice readiness catches unresolved vendor bills before invoice creation,
    # which the existing post-invoice review cannot express on its own.
    for readiness in completed_job_readiness(book):
        if readiness.status == "ready" or readiness.status == "already_invoiced":
            continue
        for message in readiness.blockers:
            key = ("invoice_readiness", readiness.work_order_id, message)
            if key in seen:
                continue
            seen.add(key)
            result.append(ExceptionItem(
                kind="invoice_readiness",
                severity="S2",
                reference_id=readiness.work_order_id,
                message=message,
                required_action="Resolve blockers before preparing the invoice",
            ))

    if verifier_registry is not None:
        for verification in verifier_registry.run(book):
            if verification.status != "FAIL":
                continue
            message = f"{verification.verifier_id}: {verification.summary}"
            key = ("verification_failed", verification.object_id, message)
            if key in seen:
                continue
            seen.add(key)
            result.append(ExceptionItem(
                kind="verification_failed",
                severity=verification.severity,
                reference_id=verification.object_id,
                message=message,
                required_action="Review deterministic verifier evidence before posting, reconciling, or closing",
            ))

    severity_rank = {"S5": 5, "S4": 4, "S3": 3, "S2": 2, "S1": 1, "S0": 0}
    return sorted(result, key=lambda item: (-severity_rank.get(item.severity, 0), item.kind, item.reference_id))
