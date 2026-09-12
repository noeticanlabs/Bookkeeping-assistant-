from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bookkeeper.domain.models import BookkeepingProposal


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    failures: tuple[str, ...]


class ProposalValidator:
    def __init__(self, valid_accounts: set[str]) -> None:
        self.valid_accounts = valid_accounts

    def validate(self, proposal: BookkeepingProposal) -> ValidationResult:
        failures: list[str] = []

        if not proposal.evidence:
            failures.append("missing_evidence")

        if not 0.0 <= proposal.confidence <= 1.0:
            failures.append("invalid_confidence")

        if not proposal.journal_lines:
            failures.append("missing_journal_lines")
        else:
            debits = sum((line.debit for line in proposal.journal_lines), Decimal("0"))
            credits = sum((line.credit for line in proposal.journal_lines), Decimal("0"))
            if debits != credits:
                failures.append("unbalanced_journal")

            unknown_accounts = {
                line.account_code
                for line in proposal.journal_lines
                if line.account_code not in self.valid_accounts
            }
            if unknown_accounts:
                failures.append("invalid_account")

            transaction_amount = abs(proposal.transaction.amount)
            if debits != transaction_amount or credits != transaction_amount:
                failures.append("source_amount_mismatch")

        return ValidationResult(valid=not failures, failures=tuple(failures))
