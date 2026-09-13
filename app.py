"""Bookkeeper Assistant — KISS core."""

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

    @property
    def payment_status(self) -> str:
        if self.amount_paid <= 0:
            return "unpaid"
        if self.balance_due > 0:
            return "partial"
        return "paid"


@dataclass
class Payment:
    id: str
    amount: Money
    invoice_id: str | None = None
    reference: str | None = None


@dataclass
class BankDeposit:
    id: str
    amount: Money
    payment_id: str | None = None
    reference: str | None = None
    processor_fee: Money = Decimal("0")


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
    deposits: dict[str, BankDeposit] = field(default_factory=dict)

    def add_work_order(self, work_order: WorkOrder) -> None:
        self.work_orders[work_order.id] = work_order

    def add_cost(self, cost: Cost) -> None:
        if cost.amount <= 0:
            raise ValueError("Cost amount must be greater than zero")
        self.costs[cost.id] = cost

    def suggest_cost_match(self, cost_id: str) -> str | None:
        cost = self.costs[cost_id]
        if not cost.reference:
            return None
        reference = cost.reference.upper()
        matches = [wo_id for wo_id in self.work_orders if wo_id.upper() in reference]
        return matches[0] if len(matches) == 1 else None

    def match_cost(self, cost_id: str, work_order_id: str) -> Cost:
        if work_order_id not in self.work_orders:
            raise ValueError("Unknown work order")
        cost = self.costs[cost_id]
        cost.work_order_id = work_order_id
        return cost

    def accept_cost_match(self, cost_id: str) -> Cost:
        suggestion = self.suggest_cost_match(cost_id)
        if suggestion is None:
            raise ValueError("No unambiguous work order match")
        return self.match_cost(cost_id, suggestion)

    def add_invoice(self, invoice: Invoice) -> None:
        self.invoices[invoice.id] = invoice

    def add_payment(self, payment: Payment) -> None:
        if payment.amount <= 0:
            raise ValueError("Payment amount must be greater than zero")
        self.payments[payment.id] = payment
        if payment.invoice_id and payment.invoice_id in self.invoices:
            self.invoices[payment.invoice_id].amount_paid += payment.amount

    def suggest_payment_match(self, payment_id: str) -> str | None:
        payment = self.payments[payment_id]
        if not payment.reference:
            return None
        reference = payment.reference.upper()
        matches = [invoice_id for invoice_id in self.invoices if invoice_id.upper() in reference]
        return matches[0] if len(matches) == 1 else None

    def match_payment(self, payment_id: str, invoice_id: str) -> Payment:
        if invoice_id not in self.invoices:
            raise ValueError("Unknown invoice")
        payment = self.payments[payment_id]
        if payment.invoice_id:
            raise ValueError("Payment is already matched")
        payment.invoice_id = invoice_id
        self.invoices[invoice_id].amount_paid += payment.amount
        return payment

    def accept_payment_match(self, payment_id: str) -> Payment:
        suggestion = self.suggest_payment_match(payment_id)
        if suggestion is None:
            raise ValueError("No unambiguous invoice match")
        return self.match_payment(payment_id, suggestion)

    def add_deposit(self, deposit: BankDeposit) -> None:
        if deposit.amount <= 0:
            raise ValueError("Deposit amount must be greater than zero")
        if deposit.processor_fee < 0:
            raise ValueError("Processor fee cannot be negative")
        self.deposits[deposit.id] = deposit

    def suggest_deposit_match(self, deposit_id: str) -> str | None:
        deposit = self.deposits[deposit_id]
        if not deposit.reference:
            return None
        reference = deposit.reference.upper()
        matches = [payment_id for payment_id in self.payments if payment_id.upper() in reference]
        return matches[0] if len(matches) == 1 else None

    def match_deposit(self, deposit_id: str, payment_id: str) -> BankDeposit:
        if payment_id not in self.payments:
            raise ValueError("Unknown payment")
        deposit = self.deposits[deposit_id]
        if deposit.payment_id:
            raise ValueError("Deposit is already matched")
        deposit.payment_id = payment_id
        return deposit

    def accept_deposit_match(self, deposit_id: str) -> BankDeposit:
        suggestion = self.suggest_deposit_match(deposit_id)
        if suggestion is None:
            raise ValueError("No unambiguous payment match")
        return self.match_deposit(deposit_id, suggestion)

    def deposit_difference(self, deposit_id: str) -> Money | None:
        deposit = self.deposits[deposit_id]
        if not deposit.payment_id:
            return None
        payment = self.payments[deposit.payment_id]
        return deposit.amount + deposit.processor_fee - payment.amount

    def deposit_status(self, deposit_id: str) -> str:
        deposit = self.deposits[deposit_id]
        if not deposit.payment_id:
            return "unmatched"
        if self.deposit_difference(deposit_id) == 0:
            return "explained" if deposit.processor_fee > 0 else "matched"
        return "difference"

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
        return InvoiceReview(wo.id, "ready" if ready else "needs_attention", invoice.id if invoice else None,
                             wo.quoted_total, invoice.total if invoice else None, self.job_cost(wo.id),
                             self.job_profit(wo.id), issues)

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
                suggestion = self.suggest_cost_match(cost.id)
                message = f"Suggested work order: {suggestion}" if suggestion else "Cost is not assigned to a job or overhead"
                issues.append(ReviewItem("unassigned_cost", message, cost.id))
        for payment in self.payments.values():
            if payment.invoice_id is None:
                suggestion = self.suggest_payment_match(payment.id)
                message = f"Suggested invoice: {suggestion}" if suggestion else "Payment is not matched to an invoice"
                issues.append(ReviewItem("unmatched_payment", message, payment.id))
        for deposit in self.deposits.values():
            status = self.deposit_status(deposit.id)
            if status == "unmatched":
                suggestion = self.suggest_deposit_match(deposit.id)
                message = f"Suggested payment: {suggestion}" if suggestion else "Bank deposit is not matched to a payment"
                issues.append(ReviewItem("unmatched_deposit", message, deposit.id))
            elif status == "difference":
                issues.append(ReviewItem("deposit_difference", f"Deposit still differs by {self.deposit_difference(deposit.id)}", deposit.id))
        return issues

    def attention_summary(self) -> dict[str, object]:
        """Small owner/bookkeeper dashboard: what needs action right now."""
        issues = self.review()
        unpaid = [i for i in self.invoices.values() if i.payment_status != "paid"]
        return {
            "completed_unbilled": sum(1 for i in issues if i.kind == "unbilled_job"),
            "unassigned_costs": sum(1 for i in issues if i.kind == "unassigned_cost"),
            "unmatched_payments": sum(1 for i in issues if i.kind == "unmatched_payment"),
            "bank_issues": sum(1 for i in issues if i.kind in {"unmatched_deposit", "deposit_difference"}),
            "open_invoice_count": len(unpaid),
            "open_invoice_balance": sum((i.balance_due for i in unpaid), Decimal("0")),
            "needs_attention": issues,
        }


class FieldService(Protocol):
    def work_orders(self) -> list[WorkOrder]: ...
    def invoices(self) -> list[Invoice]: ...
    def issue_invoice(self, invoice: Invoice) -> str: ...


class Accounting(Protocol):
    def costs(self) -> list[Cost]: ...
    def payments(self) -> list[Payment]: ...
    def deposits(self) -> list[BankDeposit]: ...
    def export_invoice(self, invoice: Invoice) -> str: ...
