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
class Bookkeeper:
    work_orders: dict[str, WorkOrder] = field(default_factory=dict)
    costs: dict[str, Cost] = field(default_factory=dict)
    invoices: dict[str, Invoice] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)

    def add_work_order(self, work_order: WorkOrder) -> None:
        self.work_orders[work_order.id] = work_order

    def add_cost(self, cost: Cost) -> None:
        self.costs[cost.id] = cost

    def add_invoice(self, invoice: Invoice) -> None:
        self.invoices[invoice.id] = invoice

    def add_payment(self, payment: Payment) -> None:
        self.payments[payment.id] = payment
        if payment.invoice_id and payment.invoice_id in self.invoices:
            self.invoices[payment.invoice_id].amount_paid += payment.amount

    def job_cost(self, work_order_id: str) -> Money:
        return sum(
            (c.amount for c in self.costs.values() if c.work_order_id == work_order_id),
            Decimal("0"),
        )

    def job_profit(self, work_order_id: str) -> Money | None:
        invoice = next(
            (i for i in self.invoices.values() if i.work_order_id == work_order_id),
            None,
        )
        if invoice is None:
            return None
        return invoice.total - self.job_cost(work_order_id)

    def review(self) -> list[ReviewItem]:
        issues: list[ReviewItem] = []

        for wo in self.work_orders.values():
            invoice = next((i for i in self.invoices.values() if i.work_order_id == wo.id), None)
            if wo.status == "complete" and invoice is None:
                issues.append(ReviewItem("unbilled_job", "Completed work order has no invoice", wo.id))
            elif invoice and invoice.customer != wo.customer:
                issues.append(ReviewItem("invoice_mismatch", "Invoice customer does not match work order", invoice.id))
            elif invoice and wo.quoted_total is not None and invoice.total != wo.quoted_total:
                issues.append(ReviewItem("invoice_total", "Invoice total differs from quoted total", invoice.id))

        for cost in self.costs.values():
            if cost.work_order_id and cost.work_order_id not in self.work_orders:
                issues.append(ReviewItem("unknown_job", "Cost references an unknown work order", cost.id))
            elif cost.work_order_id is None:
                issues.append(ReviewItem("unassigned_cost", "Cost is not assigned to a job or overhead", cost.id))

        for payment in self.payments.values():
            if payment.invoice_id is None or payment.invoice_id not in self.invoices:
                issues.append(ReviewItem("unmatched_payment", "Payment is not matched to an invoice", payment.id))

        return issues


# Integration hooks. Vendor-specific adapters implement these interfaces.
class FieldService(Protocol):
    def work_orders(self) -> list[WorkOrder]: ...
    def invoices(self) -> list[Invoice]: ...
    def issue_invoice(self, invoice: Invoice) -> str: ...


class Accounting(Protocol):
    def costs(self) -> list[Cost]: ...
    def payments(self) -> list[Payment]: ...
    def export_invoice(self, invoice: Invoice) -> str: ...
