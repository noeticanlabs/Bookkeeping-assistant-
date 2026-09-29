"""BK-AI-004C: authority isolation for AI-originated work.

Model output, proposal fields, and AI runtime state are never authority facts.
Only grants minted by the trusted authority layer from an authenticated human
principal may cross this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

from authority import AuthorityGrant


AI_ACTOR_PREFIXES = ("ai", "scheduled-ai", "model", "provider")


class AIAuthorityDenied(PermissionError):
    pass


@dataclass(frozen=True)
class HumanAuthorityEnvelope:
    grant: AuthorityGrant

    @classmethod
    def from_grant(cls, grant: AuthorityGrant) -> "HumanAuthorityEnvelope":
        if not isinstance(grant, AuthorityGrant):
            raise AIAuthorityDenied("Authority must be minted by the trusted authority layer")
        user_id = str(grant.user_id).strip().lower()
        role = str(grant.role).strip().lower()
        if not user_id or any(user_id.startswith(prefix) for prefix in AI_ACTOR_PREFIXES):
            raise AIAuthorityDenied("AI principals cannot hold execution authority")
        if role in {"ai", "model", "provider", "scheduled-ai"}:
            raise AIAuthorityDenied("AI roles cannot hold execution authority")
        return cls(grant)

    def authorizes(self, action: str) -> bool:
        return self.grant.action == action


def reject_ai_claimed_authority(claimed_authority: object) -> None:
    """Proposal/model claims are data, never authority."""
    if claimed_authority not in (None, "", False):
        raise AIAuthorityDenied("AI-supplied authority claims are not trusted")
