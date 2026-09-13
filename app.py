"""Bookkeeper Assistant — KISS core.

Connect field work to costs, invoices, payments, and accounting.
External systems plug in through small adapters at the bottom of this file.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

Money = Decimal


@dataclass
class WorkOrder:
    id: str
    customer: str
    description: str
    status: str = "open"
    quoted_total: Money | None = None


@dataclass
class Cost:
    id: str
    vendor: str
    amount: Money
    kind: str
    work_order_id: str | None = None
    reference: str | None = None


@dataclass
class Invoice:
    id: str
    work_order_id: str
    customer: str
    total: Money
    status: str = "draft"
    amount_paid: Money = Decimal("0")

    @property
    def balance_due(self) -> Money:
        return self.total - self.amount_paid


@dataclass
class Payment:
    id: str
    amount: Money
    invoice_id: str | None = None


@dataclass
class ReviewItem:
    kind: str
    message: str
    reference_id: str


@dataclass
class InvoiceReview:
    work_order_id: str
    status: str
    invoice_id: str | None
    quoted_total: Money | None
    invoice_total: Money | None
    job_cost: Money
    profit: Money | None
    issues: list[str]


@dataclass
class Bookkeeper:
    work_orders: dict[str, WorkOrder] = field(default_factory=dict)
    costs: dict[str, Cost] = field(default_factory=dict)
    invoices: dict[str, Invoice] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)

    def add_work_order(self, work_order: WorkOrder) -> None:
        self.work_orders[work_order.id] = work_order

    def add_cost(self, cost: Cost) -> None:
        if cost.amount <= 0:
            raise ValueError("Cost amount must be greater than zero")
        self.costs[cost.id] = cost

    def match_cost(self, cost_id: str, work_order_id: str) -> Cost:
        if work_order_id not in self.work_orders:
            raise ValueError("Unknown work order")
        cost = self.costs[cost_id]
        cost.work_order_id = work_order_id
        return cost

    def add_invoice(self, invoice: Invoice) -> None:
        self.invoices[invoice.id] = invoice

    def add_payment(self, payment: Payment) -> None:
        self.payments[payment.id] = payment
        if payment.invoice_id and payment.invoice_id in self.invoices:
            self.invoices[payment.invoice_id].amount_paid += payment.amount

    def invoice_for(self, work_order_id: str) -> Invoice | None:
        return next((i for i in self.invoices.values() if i.work_order_id == work_order_id), None)

    def prepare_invoice(self, work_order_id: str, total: Money | None = None) -> Invoice:
        wo = self.work_orders[work_order_id]
        if wo.status != "complete":
            raise ValueError("Work order must be complete before preparing an invoice")
        if self.invoice_for(work_order_id):
            raise ValueError("Work order already has an invoice")
        invoice_total = total if total is not None else wo.quoted_total
        if invoice_total is None or invoice_total <= 0:
            raise ValueError("Invoice needs a positive total")
        invoice = Invoice(f"DRAFT-{work_order_id}", wo.id, wo.customer, invoice_total)
        self.add_invoice(invoice)
        return invoice

    def job_cost(self, work_order_id: str) -> Money:
        return sum((c.amount for c in self.costs.values() if c.work_order_id == work_order_id), Decimal("0"))

    def job_profit(self, work_order_id: str) -> Money | None:
        invoice = self.invoice_for(work_order_id)
        return None if invoice is None else invoice.total - self.job_cost(work_order_id)

    def review_invoice(self, work_order_id: str) -> InvoiceReview:
        wo = self.work_orders[work_order_id]
        invoice = self.invoice_for(work_order_id)
        issues: list[str] = []
        if wo.status != "complete":
            issues.append("Work order is not complete")
        if invoice is None:
            issues.append("Completed work order has no invoice")
        else:
            if invoice.customer != wo.customer:
                issues.append("Invoice customer does not match work order")
            if wo.quoted_total is not None and invoice.total != wo.quoted_total:
                issues.append("Invoice total differs from quoted total")
            if invoice.total <= 0:
                issues.append("Invoice total must be greater than zero")
        ready = wo.status == "complete" and invoice is not None and not issues
        return InvoiceReview(
            wo.id, "ready" if ready else "needs_attention",
            invoice.id if invoice else None, wo.quoted_total,
            invoice.total if invoice else None, self.job_cost(wo.id),
            self.job_profit(wo.id), issues,
        )

    def completed_job_invoice_reviews(self) -> list[InvoiceReview]:
        return [self.review_invoice(wo.id) for wo in self.work_orders.values() if wo.status == "complete"]

    def review(self) -> list[ReviewItem]:
        issues: list[ReviewItem] = []
        for result in self.completed_job_invoice_reviews():
            for message in result.issues:
                kind = "unbilled_job" if result.invoice_id is None else "invoice_review"
                issues.append(ReviewItem(kind, message, result.invoice_id or result.work_order_id))
        for cost in self.costs.values():
            if cost.work_order_id and cost.work_order_id not in self.work_orders:
                issues.append(ReviewItem("unknown_job", "Cost references an unknown work order", cost.id))
            elif cost.work_order_id is None:
                issues.append(ReviewItem("unassigned_cost", "Cost is not assigned to a job or overhead", cost.id))
        for payment in self.payments.values():
            if payment.invoice_id is None or payment.invoice_id not in self.invoices:
                issues.append(ReviewItem("unmatched_payment", "Payment is not matched to an invoice", payment.id))
        return issues


class FieldService(Protocol):
    def work_orders(self) -> list[WorkOrder]: ...
    def invoices(self) -> list[Invoice]: ...
    def issue_invoice(self, invoice: Invoice) -> str: ...


class Accounting(Protocol):
    def costs(self) -> list[Cost]: ...
    def payments(self) -> list[Payment]: ...
    def export_invoice(self, invoice: Invoice) -> str: ...
