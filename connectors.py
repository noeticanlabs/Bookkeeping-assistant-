"""Optional integration hooks. The KISS core does not depend on any vendor."""

from dataclasses import dataclass
from typing import Protocol

from app import BankDeposit, Invoice, Payment, WorkOrder


class FieldServiceConnector(Protocol):
    def pull_work_orders(self) -> list[WorkOrder]: ...
    def issue_invoice(self, invoice: Invoice) -> str: ...


class AccountingConnector(Protocol):
    def push_invoice(self, invoice: Invoice) -> str: ...
    def pull_payments(self) -> list[Payment]: ...
    def pull_deposits(self) -> list[BankDeposit]: ...


class DocumentConnector(Protocol):
    def extract(self, document_path: str) -> dict[str, object]: ...


class EventSink(Protocol):
    def emit(self, event: str, payload: dict[str, object]) -> None: ...


@dataclass
class ConnectorHub:
    """All connectors are optional; real vendor adapters plug in here later."""

    field_service: FieldServiceConnector | None = None
    accounting: AccountingConnector | None = None
    documents: DocumentConnector | None = None
    events: EventSink | None = None

    def emit(self, event: str, payload: dict[str, object]) -> None:
        if self.events:
            self.events.emit(event, payload)
