"""BK-AI-007: exact, hash-bound human approval for ActionProposal.

Approval authorizes one immutable proposal identity/hash pair for a bounded time.
It does not authorize an AI, provider, action class, or later-modified proposal.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from action_proposal import ActionProposal
from ai_authority_boundary import HumanAuthorityEnvelope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


@dataclass(frozen=True)
class ProposalApproval:
    approval_id: str
    proposal_id: str
    proposal_sha256: str
    action_type: str
    approver_user_id: str
    approver_role: str
    approved_at: str
    expires_at: str
    signature: str

    def signed_payload(self) -> dict[str, str]:
        return {
            "approval_id": self.approval_id,
            "proposal_id": self.proposal_id,
            "proposal_sha256": self.proposal_sha256,
            "action_type": self.action_type,
            "approver_user_id": self.approver_user_id,
            "approver_role": self.approver_role,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
        }


class ProposalApprovalAuthority:
    """Trusted approval signer/verifier; never exposed to the AI runtime."""

    def __init__(self, signing_key: bytes):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("proposal approval signing key must be at least 32 bytes")
        self._key = signing_key

    def _sign(self, payload: dict[str, str]) -> str:
        return hmac.new(self._key, _canonical(payload), hashlib.sha256).hexdigest()

    def approve(self, proposal: ActionProposal, authority: HumanAuthorityEnvelope, *,
                ttl_minutes: int = 30, now: datetime | None = None) -> ProposalApproval:
        if not proposal.verify_hash():
            raise ValueError("Cannot approve a tampered proposal")
        if proposal.status != "proposed":
            raise ValueError("Only proposed actions may be approved")
        if not authority.authorizes(proposal.action_type):
            raise PermissionError("Human authority does not authorize this proposal action")
        current = (now or _utcnow()).astimezone(timezone.utc)
        if proposal.expires_at and _parse(proposal.expires_at) <= current:
            raise ValueError("Cannot approve an expired proposal")
        if ttl_minutes <= 0:
            raise ValueError("approval ttl must be positive")
        expires = current + timedelta(minutes=ttl_minutes)
        if proposal.expires_at:
            expires = min(expires, _parse(proposal.expires_at))
        approval_id = "APR-" + hashlib.sha256(
            _canonical([proposal.proposal_id, proposal.proposal_sha256, authority.grant.user_id, current.isoformat()])
        ).hexdigest()[:20]
        payload = {
            "approval_id": approval_id,
            "proposal_id": proposal.proposal_id,
            "proposal_sha256": proposal.proposal_sha256,
            "action_type": proposal.action_type,
            "approver_user_id": authority.grant.user_id,
            "approver_role": authority.grant.role,
            "approved_at": current.isoformat(),
            "expires_at": expires.isoformat(),
        }
        return ProposalApproval(**payload, signature=self._sign(payload))

    def verify(self, approval: ProposalApproval, proposal: ActionProposal, *, now: datetime | None = None) -> bool:
        current = (now or _utcnow()).astimezone(timezone.utc)
        if not proposal.verify_hash() or proposal.status != "proposed":
            return False
        if approval.proposal_id != proposal.proposal_id:
            return False
        if approval.proposal_sha256 != proposal.proposal_sha256:
            return False
        if approval.action_type != proposal.action_type:
            return False
        if _parse(approval.expires_at) <= current:
            return False
        expected = self._sign(approval.signed_payload())
        return hmac.compare_digest(expected, approval.signature)


class ProposalApprovalStore:
    """Durable approval receipt store; contains no execution path."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS proposal_approvals (
                approval_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                proposal_sha256 TEXT NOT NULL,
                action_type TEXT NOT NULL,
                approver_user_id TEXT NOT NULL,
                approver_role TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                signature TEXT NOT NULL UNIQUE
            )""")

    def add(self, approval: ProposalApproval) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""INSERT INTO proposal_approvals(
                approval_id,proposal_id,proposal_sha256,action_type,approver_user_id,
                approver_role,approved_at,expires_at,signature
            ) VALUES(?,?,?,?,?,?,?,?,?)""", (
                approval.approval_id, approval.proposal_id, approval.proposal_sha256,
                approval.action_type, approval.approver_user_id, approval.approver_role,
                approval.approved_at, approval.expires_at, approval.signature,
            ))
