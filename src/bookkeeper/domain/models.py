from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import IntEnum
from typing import Iterable
from uuid import UUID, uuid4


class AuthorityLevel(IntEnum):
    OBSERVE = 0
    RECOMMEND = 1
    PREPARE = 2
    POST_APPROVED = 3
    EXTERNAL_FINANCIAL_ACTION = 4


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: UUID
    kind: str
    source_uri: str
    sha256: str


@dataclass(frozen=True)
class NormalizedTransaction:
    transaction_id: UUID
    source_account_id: str
    occurred_on: date
    amount: Decimal
    description: str
    external_id: str | None = None


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.debit < 0 or self.credit < 0:
            raise ValueError("Journal amounts cannot be negative")
        if self.debit and self.credit:
            raise ValueError("A journal line cannot contain both debit and credit")
        if not self.debit and not self.credit:
            raise ValueError("A journal line must contain a debit or credit")


@dataclass(frozen=True)
class BookkeepingProposal:
    proposal_id: UUID
    transaction: NormalizedTransaction
    classification: str
    tax_treatment: str | None
    confidence: float
    evidence: tuple[EvidenceRef, ...]
    explanation: str
    journal_lines: tuple[JournalLine, ...]
    requested_authority: AuthorityLevel = AuthorityLevel.PREPARE
    model_name: str | None = None
    model_version: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        *,
        transaction: NormalizedTransaction,
        classification: str,
        confidence: float,
        evidence: Iterable[EvidenceRef],
        explanation: str,
        journal_lines: Iterable[JournalLine],
        tax_treatment: str | None = None,
        requested_authority: AuthorityLevel = AuthorityLevel.PREPARE,
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> "BookkeepingProposal":
        return cls(
            proposal_id=uuid4(),
            transaction=transaction,
            classification=classification,
            tax_treatment=tax_treatment,
            confidence=confidence,
            evidence=tuple(evidence),
            explanation=explanation,
            journal_lines=tuple(journal_lines),
            requested_authority=requested_authority,
            model_name=model_name,
            model_version=model_version,
        )


@dataclass(frozen=True)
class Approval:
    approver_id: str
    authority_level: AuthorityLevel
    approved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None


@dataclass(frozen=True)
class PostedLedgerEntry:
    entry_id: UUID
    proposal_id: UUID
    transaction_id: UUID
    journal_lines: tuple[JournalLine, ...]
    evidence: tuple[EvidenceRef, ...]
    approval: Approval
    posted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
