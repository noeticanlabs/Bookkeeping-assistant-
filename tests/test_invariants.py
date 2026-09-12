from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from bookkeeper.domain.models import (
    Approval,
    AuthorityLevel,
    BookkeepingProposal,
    EvidenceRef,
    JournalLine,
    NormalizedTransaction,
)
from bookkeeper.governance.authority import AuthorityError, AuthorityPolicy
from bookkeeper.verification.validator import ProposalValidator


def evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=uuid4(),
        kind="bank_transaction",
        source_uri="bank://checking/txn-1",
        sha256="abc123",
    )


def transaction(amount: str = "100.00") -> NormalizedTransaction:
    return NormalizedTransaction(
        transaction_id=uuid4(),
        source_account_id="checking",
        occurred_on=date(2026, 9, 12),
        amount=Decimal(amount),
        description="Ferguson plumbing materials",
        external_id="txn-1",
    )


def balanced_proposal() -> BookkeepingProposal:
    return BookkeepingProposal.create(
        transaction=transaction(),
        classification="job_materials",
        confidence=0.91,
        evidence=[evidence()],
        explanation="Invoice and bank transaction agree.",
        journal_lines=[
            JournalLine("5100", debit=Decimal("100.00"), job_id="JOB-1842"),
            JournalLine("1000", credit=Decimal("100.00")),
        ],
    )


def test_balanced_evidenced_proposal_passes() -> None:
    result = ProposalValidator({"1000", "5100"}).validate(balanced_proposal())
    assert result.valid
    assert result.failures == ()


def test_unbalanced_journal_cannot_validate() -> None:
    proposal = BookkeepingProposal.create(
        transaction=transaction(),
        classification="job_materials",
        confidence=0.9,
        evidence=[evidence()],
        explanation="Candidate only.",
        journal_lines=[
            JournalLine("5100", debit=Decimal("100.00")),
            JournalLine("1000", credit=Decimal("90.00")),
        ],
    )
    result = ProposalValidator({"1000", "5100"}).validate(proposal)
    assert not result.valid
    assert "unbalanced_journal" in result.failures


def test_missing_evidence_cannot_validate() -> None:
    proposal = BookkeepingProposal.create(
        transaction=transaction(),
        classification="job_materials",
        confidence=0.9,
        evidence=[],
        explanation="No source evidence.",
        journal_lines=[
            JournalLine("5100", debit=Decimal("100.00")),
            JournalLine("1000", credit=Decimal("100.00")),
        ],
    )
    result = ProposalValidator({"1000", "5100"}).validate(proposal)
    assert "missing_evidence" in result.failures


def test_external_financial_action_is_forbidden() -> None:
    policy = AuthorityPolicy()
    with pytest.raises(AuthorityError):
        policy.assert_external_action_forbidden()


def test_recommendation_cannot_authorize_posting() -> None:
    policy = AuthorityPolicy()
    approval = Approval("matt", AuthorityLevel.RECOMMEND)
    with pytest.raises(AuthorityError):
        policy.assert_posting_approved(approval)
