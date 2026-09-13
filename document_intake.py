"""Document interpretation stays outside authoritative bookkeeping state."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app import Bookkeeper, Cost, VendorBill


@dataclass
class DocumentProposal:
    filename: str
    vendor: str
    amount: Decimal
    reference: str = ""
    work_order_id: str | None = None
    record_type: str = "vendor_bill"
    document_id: str = ""


def _contains_id(reference: str, value: str) -> bool:
    pattern = rf"(?<![A-Z0-9]){re.escape(value.upper())}(?![A-Z0-9])"
    return re.search(pattern, reference.upper()) is not None


def proposal_from_extraction(
    book: Bookkeeper,
    filename: str,
    extracted: dict[str, object],
) -> DocumentProposal:
    """Validate extractor output and make a reviewable, non-authoritative proposal."""
    vendor = str(extracted.get("vendor") or "").strip()
    if not vendor:
        raise ValueError("Document extraction did not identify a vendor")

    raw_amount = extracted.get("amount")
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Document extraction did not identify a valid amount") from exc
    if amount <= 0:
        raise ValueError("Document amount must be greater than zero")

    reference = str(extracted.get("reference") or "").strip()
    document_id = str(extracted.get("document_id") or extracted.get("invoice_id") or "").strip()
    if document_id and document_id not in reference:
        reference = f"{reference} {document_id}".strip()

    explicit_work_order = str(extracted.get("work_order_id") or "").strip() or None
    work_order_id = explicit_work_order if explicit_work_order in book.work_orders else None

    if work_order_id is None and reference:
        matches = [wo_id for wo_id in book.work_orders if _contains_id(reference, wo_id)]
        if len(matches) == 1:
            work_order_id = matches[0]

    record_type = str(extracted.get("record_type") or "vendor_bill").strip().lower()
    if record_type not in {"vendor_bill", "cost"}:
        record_type = "vendor_bill"

    return DocumentProposal(
        filename=filename,
        vendor=vendor,
        amount=amount,
        reference=reference,
        work_order_id=work_order_id,
        record_type=record_type,
        document_id=document_id,
    )


def record_approved_document(
    book: Bookkeeper,
    *,
    record_id: str,
    vendor: str,
    amount: Decimal,
    reference: str = "",
    work_order_id: str | None = None,
    record_type: str = "vendor_bill",
    treatment: str = "ask",
    linked_cost_id: str | None = None,
):
    """Validate the complete user decision before changing bookkeeping state."""
    record_id = record_id.strip()
    vendor = vendor.strip()
    if not record_id:
        raise ValueError("A record ID is required")
    if not vendor:
        raise ValueError("Vendor is required")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    if work_order_id and work_order_id not in book.work_orders:
        raise ValueError("Unknown work order")

    if record_type == "cost":
        if record_id in book.costs:
            raise ValueError("Duplicate cost ID")
        cost = Cost(
            id=record_id,
            vendor=vendor,
            amount=amount,
            kind="document_cost",
            work_order_id=work_order_id,
            reference=reference or None,
        )
        book.add_cost(cost)
        return cost

    if record_type != "vendor_bill":
        raise ValueError("Invalid document record type")
    if record_id in book.vendor_bills:
        raise ValueError("Duplicate vendor bill ID")
    if treatment not in {"ask", "create_cost", "support_cost", "overhead"}:
        raise ValueError("Invalid vendor bill treatment")

    if treatment == "create_cost":
        if not work_order_id:
            raise ValueError("Job cost treatment requires a work order")
        if f"BILL-COST-{record_id}" in book.costs:
            raise ValueError("Generated bill cost ID already exists")

    if treatment == "support_cost":
        if not linked_cost_id or linked_cost_id not in book.costs:
            raise ValueError("Support treatment requires an existing cost")
        cost = book.costs[linked_cost_id]
        if work_order_id and cost.work_order_id and work_order_id != cost.work_order_id:
            raise ValueError("Vendor bill and cost reference different work orders")

    bill = VendorBill(id=record_id, vendor=vendor, amount=amount, work_order_id=work_order_id)
    book.add_vendor_bill(bill)
    if treatment != "ask":
        book.treat_vendor_bill(record_id, treatment=treatment, linked_cost_id=linked_cost_id)
    return bill
