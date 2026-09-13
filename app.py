"""Bookkeeper Assistant — KISS core."""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

Money = Decimal


def _contains_id(reference: str, value: str) -> bool:
    """Match an external ID as a whole token, not as a prefix of another ID."""
    pattern = rf"(?<![A-Z0-9]){re.escape(value.upper())}(?![A-Z0-9])"
    return re.search(pattern, reference.upper()) is not None


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
    correction_of: str | None = None
    superseded_by: str | None = None

    @property
    def is_current(self) -> bool:
        return self.superseded_by is None


@dataclass
class VendorBill:
    id: str
    vendor: str
    amount: Money
    due_date: str | None = None
    amount_paid: Money = Decimal("0")
    work_order_id: str | None = None
    treatment: str | None = None
    linked_cost_id: str | None = None

    @property
    def balance_due(self) -> Money:
        return self.amount - self.amount_paid

    @property
    def payment_status(self) -> str:
        if self.amount_paid <= 0:
            return "unpaid"
        if self.balance_due > 0:
            return "partial"
        return "paid"


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
    vendor_bill_mode: str = "ask"
    work_orders: dict[str, WorkOrder] = field(default_factory=dict)
    costs: dict[str, Cost] = field(default_factory=dict)
    vendor_bills: dict[str, VendorBill] = field(default_factory=dict)
    invoices: dict[str, Invoice] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)
    deposits: dict[str, BankDeposit] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.vendor_bill_mode not in {"ask", "create_cost", "support_cost"}:
            raise ValueError("Invalid vendor bill mode")

    def add_work_order(self, work_order: WorkOrder) -> None:
        if work_order.id in self.work_orders:
            raise ValueError("Duplicate work order ID")
        self.work_orders[work_order.id] = work_order

    def add_cost(self, cost: Cost) -> None:
        if cost.id in self.costs:
            raise ValueError("Duplicate cost ID")
        if cost.amount <= 0:
            raise ValueError("Cost amount must be greater than zero")
        if cost.work_order_id and cost.work_order_id not in self.work_orders:
            raise ValueError("Unknown work order")
        if cost.correction_of and cost.correction_of not in self.costs:
            raise ValueError("Correction references an unknown cost")
        self.costs[cost.id] = cost

    def supersede_cost(self, original_cost_id: str, replacement: Cost) -> Cost:
        if original_cost_id not in self.costs:
            raise ValueError("Unknown cost")
        original = self.costs[original_cost_id]
        if original.superseded_by:
            raise ValueError("Cost has already been superseded")
        if replacement.id == original_cost_id:
            raise ValueError("Replacement cost needs a new ID")
        if replacement.correction_of and replacement.correction_of != original_cost_id:
            raise ValueError("Replacement references the wrong original cost")
        replacement.correction_of = original_cost_id
        self.add_cost(replacement)
        original.superseded_by = replacement.id
        return replacement

    def current_costs(self) -> list[Cost]:
        return [cost for cost in self.costs.values() if cost.is_current]

    def add_vendor_bill(self, bill: VendorBill) -> None:
        if bill.id in self.vendor_bills:
            raise ValueError("Duplicate vendor bill ID")
        if bill.amount <= 0:
            raise ValueError("Vendor bill amount must be greater than zero")
        if bill.amount_paid < 0 or bill.amount_paid > bill.amount:
            raise ValueError("Invalid vendor bill payment amount")
        if bill.work_order_id and bill.work_order_id not in self.work_orders:
            raise ValueError("Unknown work order")
        self.vendor_bills[bill.id] = bill

    def treat_vendor_bill(self, bill_id: str, treatment: str | None = None, linked_cost_id: str | None = None) -> VendorBill:
        bill = self.vendor_bills[bill_id]
        chosen = treatment or self.vendor_bill_mode
        if chosen == "ask":
            raise ValueError("Vendor bill treatment must be chosen")
        if chosen not in {"create_cost", "support_cost", "overhead"}:
            raise ValueError("Invalid vendor bill treatment")
        if bill.treatment is not None:
            raise ValueError("Vendor bill treatment is already set")
        if chosen == "create_cost":
            if not bill.work_order_id:
                raise ValueError("Job cost treatment requires a work order")
            cost_id = f"BILL-COST-{bill.id}"
            self.add_cost(Cost(cost_id, bill.vendor, bill.amount, "vendor_bill", bill.work_order_id, bill.id))
            bill.linked_cost_id = cost_id
        elif chosen == "support_cost":
            if not linked_cost_id or linked_cost_id not in self.costs:
                raise ValueError("Support treatment requires an existing cost")
            cost = self.costs[linked_cost_id]
            if bill.work_order_id and cost.work_order_id and bill.work_order_id != cost.work_order_id:
                raise ValueError("Vendor bill and cost reference different work orders")
            if not bill.work_order_id:
                bill.work_order_id = cost.work_order_id
            bill.linked_cost_id = linked_cost_id
        bill.treatment = chosen
        return bill

    def pay_vendor_bill(self, bill_id: str, amount: Money) -> VendorBill:
        if amount <= 0:
            raise ValueError("Vendor payment must be greater than zero")
        bill = self.vendor_bills[bill_id]
        if amount > bill.balance_due:
            raise ValueError("Vendor payment exceeds balance due")
        bill.amount_paid += amount
        return bill

    def accounts_payable_summary(self) -> dict[str, object]:
        open_bills = [b for b in self.vendor_bills.values() if b.payment_status != "paid"]
        return {"open_bill_count": len(open_bills), "open_bill_balance": sum((b.balance_due for b in open_bills), Decimal("0")), "open_bills": open_bills}

    def near_term_position(self) -> dict[str, Money]:
        """Operational snapshot only: issued open AR minus open AP. Not a cash forecast."""
        receivables = sum((i.balance_due for i in self.invoices.values() if i.status != "draft" and i.payment_status != "paid"), Decimal("0"))
        payables = self.accounts_payable_summary()["open_bill_balance"]
        return {"expected_in": receivables, "owed_out": payables, "net_position": receivables - payables}

    def suggest_cost_match(self, cost_id: str) -> str | None:
        cost = self.costs[cost_id]
        if not cost.reference:
            return None
        matches = [wo_id for wo_id in self.work_orders if _contains_id(cost.reference, wo_id)]
        return matches[0] if len(matches) == 1 else None

    def match_cost(self, cost_id: str, work_order_id: str) -> Cost:
        if work_order_id not in self.work_orders:
            raise ValueError("Unknown work order")
        cost = self.costs[cost_id]
        if not cost.is_current:
            raise ValueError("Cannot change a superseded cost")
        cost.work_order_id = work_order_id
        return cost

    def accept_cost_match(self, cost_id: str) -> Cost:
        suggestion = self.suggest_cost_match(cost_id)
        if suggestion is None:
            raise ValueError("No unambiguous work order match")
        return self.match_cost(cost_id, suggestion)

    def add_invoice(self, invoice: Invoice) -> None:
        if invoice.id in self.invoices:
            raise ValueError("Duplicate invoice ID")
        if invoice.work_order_id not in self.work_orders:
            raise ValueError("Unknown work order")
        if invoice.total <= 0:
            raise ValueError("Invoice total must be greater than zero")
        if invoice.amount_paid < 0 or invoice.amount_paid > invoice.total:
            raise ValueError("Invalid invoice paid amount")
        self.invoices[invoice.id] = invoice

    def add_payment(self, payment: Payment) -> None:
        if payment.id in self.payments:
            raise ValueError("Duplicate payment ID")
        if payment.amount <= 0:
            raise ValueError("Payment amount must be greater than zero")
        if payment.invoice_id:
            if payment.invoice_id not in self.invoices:
                raise ValueError("Unknown invoice")
            invoice = self.invoices[payment.invoice_id]
            if payment.amount > invoice.balance_due:
                raise ValueError("Payment exceeds invoice balance due")
            invoice.amount_paid += payment.amount
        self.payments[payment.id] = payment

    def suggest_payment_match(self, payment_id: str) -> str | None:
        payment = self.payments[payment_id]
        if not payment.reference:
            return None
        matches = [invoice_id for invoice_id in self.invoices if _contains_id(payment.reference, invoice_id)]
        return matches[0] if len(matches) == 1 else None

    def match_payment(self, payment_id: str, invoice_id: str) -> Payment:
        if invoice_id not in self.invoices:
            raise ValueError("Unknown invoice")
        payment = self.payments[payment_id]
        if payment.invoice_id:
            raise ValueError("Payment is already matched")
        invoice = self.invoices[invoice_id]
        if payment.amount > invoice.balance_due:
            raise ValueError("Payment exceeds invoice balance due")
        payment.invoice_id = invoice_id
        invoice.amount_paid += payment.amount
        return payment

    def accept_payment_match(self, payment_id: str) -> Payment:
        suggestion = self.suggest_payment_match(payment_id)
        if suggestion is None:
            raise ValueError("No unambiguous invoice match")
        return self.match_payment(payment_id, suggestion)

    def add_deposit(self, deposit: BankDeposit) -> None:
        if deposit.id in self.deposits:
            raise ValueError("Duplicate deposit ID")
        if deposit.amount <= 0:
            raise ValueError("Deposit amount must be greater than zero")
        if deposit.processor_fee < 0:
            raise ValueError("Processor fee cannot be negative")
        self.deposits[deposit.id] = deposit

    def suggest_deposit_match(self, deposit_id: str) -> str | None:
        deposit = self.deposits[deposit_id]
        if not deposit.reference:
            return None
        matches = [payment_id for payment_id in self.payments if _contains_id(deposit.reference, payment_id)]
        return matches[0] if len(matches) == 1 else None

    def match_deposit(self, deposit_id: str, payment_id: str) -> BankDeposit:
        if payment_id not in self.payments:
            raise ValueError("Unknown payment")
        deposit = self.deposits[deposit_id]
        if deposit.payment_id:
            raise ValueError("Deposit is already matched")
        if any(d.payment_id == payment_id for d in self.deposits.values() if d.id != deposit_id):
            raise ValueError("Payment is already matched to another deposit")
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
        if not deposit.payment_id or deposit.payment_id not in self.payments:
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
        return sum((c.amount for c in self.costs.values() if c.work_order_id == work_order_id and c.is_current), Decimal("0"))

    def job_profit(self, work_order_id: str) -> Money | None:
        invoice = self.invoice_for(work_order_id)
        return None if invoice is None else invoice.total - self.job_cost(work_order_id)

    def review_invoice(self, work_order_id: str) -> InvoiceReview:
        wo = self.work_orders[work_order_id]
        invoice = self.invoice_for(work_order_id)
        issues: list[str] = []
        if wo.status != "complete": issues.append("Work order is not complete")
        if invoice is None:
            issues.append("Completed work order has no invoice")
        else:
            if invoice.customer != wo.customer: issues.append("Invoice customer does not match work order")
            if wo.quoted_total is not None and invoice.total != wo.quoted_total: issues.append("Invoice total differs from quoted total")
        ready = wo.status == "complete" and invoice is not None and not issues
        return InvoiceReview(wo.id, "ready" if ready else "needs_attention", invoice.id if invoice else None, wo.quoted_total, invoice.total if invoice else None, self.job_cost(wo.id), self.job_profit(wo.id), issues)

    def completed_job_invoice_reviews(self) -> list[InvoiceReview]:
        return [self.review_invoice(wo.id) for wo in self.work_orders.values() if wo.status == "complete"]

    def review(self) -> list[ReviewItem]:
        issues: list[ReviewItem] = []
        for result in self.completed_job_invoice_reviews():
            for message in result.issues:
                issues.append(ReviewItem("unbilled_job" if result.invoice_id is None else "invoice_review", message, result.invoice_id or result.work_order_id))
        for cost in self.current_costs():
            if cost.work_order_id and cost.work_order_id not in self.work_orders:
                issues.append(ReviewItem("unknown_job", "Cost references an unknown work order", cost.id))
            elif cost.work_order_id is None:
                suggestion = self.suggest_cost_match(cost.id)
                issues.append(ReviewItem("unassigned_cost", f"Suggested work order: {suggestion}" if suggestion else "Cost is not assigned to a job or overhead", cost.id))
        for bill in self.vendor_bills.values():
            if bill.treatment is None: issues.append(ReviewItem("vendor_bill_treatment", "Vendor bill needs cost treatment", bill.id))
        for payment in self.payments.values():
            if payment.invoice_id is None or payment.invoice_id not in self.invoices:
                suggestion = self.suggest_payment_match(payment.id) if payment.invoice_id is None else None
                issues.append(ReviewItem("unmatched_payment", f"Suggested invoice: {suggestion}" if suggestion else "Payment is not matched to a valid invoice", payment.id))
        for deposit in self.deposits.values():
            status = self.deposit_status(deposit.id)
            if status == "unmatched":
                suggestion = self.suggest_deposit_match(deposit.id)
                issues.append(ReviewItem("unmatched_deposit", f"Suggested payment: {suggestion}" if suggestion else "Bank deposit is not matched to a valid payment", deposit.id))
            elif status == "difference":
                issues.append(ReviewItem("deposit_difference", f"Deposit still differs by {self.deposit_difference(deposit.id)}", deposit.id))
        return issues

    def attention_summary(self) -> dict[str, object]:
        issues = self.review()
        unpaid = [i for i in self.invoices.values() if i.status != "draft" and i.payment_status != "paid"]
        ap = self.accounts_payable_summary()
        position = self.near_term_position()
        return {"completed_unbilled": sum(1 for i in issues if i.kind == "unbilled_job"), "unassigned_costs": sum(1 for i in issues if i.kind == "unassigned_cost"), "vendor_bills_needing_treatment": sum(1 for i in issues if i.kind == "vendor_bill_treatment"), "unmatched_payments": sum(1 for i in issues if i.kind == "unmatched_payment"), "bank_issues": sum(1 for i in issues if i.kind in {"unmatched_deposit", "deposit_difference"}), "open_invoice_count": len(unpaid), "open_invoice_balance": position["expected_in"], "open_bill_count": ap["open_bill_count"], "open_bill_balance": position["owed_out"], "near_term_net_position": position["net_position"], "needs_attention": issues}


class FieldService(Protocol):
    def work_orders(self) -> list[WorkOrder]: ...
    def invoices(self) -> list[Invoice]: ...
    def issue_invoice(self, invoice: Invoice) -> str: ...


class Accounting(Protocol):
    def costs(self) -> list[Cost]: ...
    def payments(self) -> list[Payment]: ...
    def deposits(self) -> list[BankDeposit]: ...
    def export_invoice(self, invoice: Invoice) -> str: ...
