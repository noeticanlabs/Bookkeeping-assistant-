from __future__ import annotations

from dataclasses import dataclass

from bookkeeper.domain.models import Approval, AuthorityLevel, BookkeepingProposal


class AuthorityError(PermissionError):
    pass


@dataclass(frozen=True)
class AuthorityPolicy:
    max_system_authority: AuthorityLevel = AuthorityLevel.POST_APPROVED

    def assert_proposal_allowed(self, proposal: BookkeepingProposal) -> None:
        if proposal.requested_authority > self.max_system_authority:
            raise AuthorityError(
                f"Requested authority {proposal.requested_authority.name} exceeds "
                f"system maximum {self.max_system_authority.name}"
            )

    def assert_posting_approved(self, approval: Approval) -> None:
        if approval.authority_level < AuthorityLevel.POST_APPROVED:
            raise AuthorityError("Posting requires POST_APPROVED authority")
        if approval.authority_level > self.max_system_authority:
            raise AuthorityError("Approval exceeds configured system authority")

    def assert_external_action_forbidden(self) -> None:
        raise AuthorityError(
            "External financial actions are prohibited in BOOKKEEPER-AI v0.1"
        )
