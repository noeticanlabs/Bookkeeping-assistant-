"""Optional integration hooks. The KISS core does not depend on any vendor.

Connectors advertise capabilities instead of being forced into one vendor role.
A company may therefore use Yardi only for work orders, QuickBooks only for
financial evidence, and OpenAI only for document interpretation at the same time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app import BankDeposit, Invoice, Payment, WorkOrder


WORK_ORDERS_READ = "work_orders.read"
INVOICES_WRITE = "invoices.write"
PAYMENTS_READ = "payments.read"
DEPOSITS_READ = "deposits.read"
DOCUMENTS_EXTRACT = "documents.extract"


class ConnectorIdentity(Protocol):
    name: str
    capabilities: frozenset[str]


class FieldServiceConnector(Protocol):
    name: str
    capabilities: frozenset[str]

    def pull_work_orders(self) -> list[WorkOrder]: ...
    def issue_invoice(self, invoice: Invoice) -> str: ...


class AccountingConnector(Protocol):
    name: str
    capabilities: frozenset[str]

    def push_invoice(self, invoice: Invoice) -> str: ...
    def pull_payments(self) -> list[Payment]: ...
    def pull_deposits(self) -> list[BankDeposit]: ...


class DocumentConnector(Protocol):
    name: str
    capabilities: frozenset[str]

    def extract(self, document_path: str) -> dict[str, object]: ...


class EventSink(Protocol):
    def emit(self, event: str, payload: dict[str, object]) -> None: ...


@dataclass
class ConnectorHub:
    """Capability-aware connector registry with backwards-compatible primaries."""

    field_service: FieldServiceConnector | None = None
    accounting: AccountingConnector | None = None
    documents: DocumentConnector | None = None
    events: EventSink | None = None
    connectors: list[object] = field(default_factory=list)

    def __post_init__(self) -> None:
        for connector in (self.field_service, self.accounting, self.documents):
            if connector is not None and connector not in self.connectors:
                self.connectors.append(connector)

    def register(self, connector: object) -> object:
        if connector not in self.connectors:
            self.connectors.append(connector)
        caps = getattr(connector, "capabilities", frozenset())
        if self.field_service is None and WORK_ORDERS_READ in caps:
            self.field_service = connector  # compatibility primary
        if self.accounting is None and ({PAYMENTS_READ, DEPOSITS_READ, INVOICES_WRITE} & set(caps)):
            self.accounting = connector  # compatibility primary
        if self.documents is None and DOCUMENTS_EXTRACT in caps:
            self.documents = connector
        return connector

    def with_capability(self, capability: str) -> list[object]:
        return [c for c in self.connectors if capability in getattr(c, "capabilities", frozenset())]

    def work_order_sources(self) -> list[object]:
        return self.with_capability(WORK_ORDERS_READ)

    def payment_sources(self) -> list[object]:
        return self.with_capability(PAYMENTS_READ)

    def deposit_sources(self) -> list[object]:
        return self.with_capability(DEPOSITS_READ)

    def invoice_sinks(self) -> list[object]:
        return self.with_capability(INVOICES_WRITE)

    def names(self) -> list[str]:
        return [getattr(c, "name", c.__class__.__name__) for c in self.connectors]

    def emit(self, event: str, payload: dict[str, object]) -> None:
        if self.events:
            self.events.emit(event, payload)
