"""Document interpretation stays outside authoritative bookkeeping state."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app import Bookkeeper


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
