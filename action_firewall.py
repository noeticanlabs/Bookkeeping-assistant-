"""BK-AI-003: deterministic admission firewall for ActionProposal.

The firewall decides whether a proposal is admissible for consideration.  It
never executes actions, calls connectors, or grants execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from action_proposal import ActionProposal, PROPOSAL_SCHEMA_VERSION

ADMIT = "ADMIT"
DENY = "DENY"
HUMAN_REVIEW = "HUMAN_REVIEW"


@dataclass(frozen=True)
class FirewallDecision:
    decision: str
    reason_codes: tuple[str, ...]
    proposal_id: str
    proposal_sha256: str


@dataclass(frozen=True)
class ActionPolicy:
    action_type: str
    allowed_target_types: frozenset[str]
    allowed_requested_authority: frozenset[str] = frozenset({"propose"})
    required_evidence: bool = True
    human_review: bool = True
    allowed_risk_classes: frozenset[str] = frozenset({"informational", "operational"})


DEFAULT_POLICIES = {
    "work_order.schedule": ActionPolicy(
        action_type="work_order.schedule",
        allowed_target_types=frozenset({"work_order"}),
        required_evidence=True,
        human_review=True,
        allowed_risk_classes=frozenset({"operational"}),
    ),
    "invoice.prepare": ActionPolicy(
        action_type="invoice.prepare",
        allowed_target_types=frozenset({"invoice", "work_order"}),
        required_evidence=True,
        human_review=True,
        allowed_risk_classes=frozenset({"operational", "financial"}),
    ),
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_action_proposal(
    proposal: ActionProposal,
    *,
    policies: dict[str, ActionPolicy] | None = None,
    evidence_exists: Callable[[str], bool] | None = None,
    now: datetime | None = None,
) -> FirewallDecision:
    """Fail closed. ADMIT means admissible only; it never means execute."""
    policy_map = policies or DEFAULT_POLICIES
    reasons: list[str] = []

    if proposal.schema_version != PROPOSAL_SCHEMA_VERSION:
        reasons.append("SCHEMA_UNSUPPORTED")
    if not proposal.verify_hash():
        reasons.append("PROPOSAL_HASH_INVALID")
    if proposal.status != "proposed":
        reasons.append("STATUS_NOT_PROPOSED")
    if proposal.requested_authority not in {"observe", "propose", "prepare"}:
        reasons.append("EXECUTION_AUTHORITY_FORBIDDEN")

    current = now or datetime.now(timezone.utc)
    try:
        expiry = _parse_time(proposal.expires_at)
        if expiry is not None and expiry <= current.astimezone(timezone.utc):
            reasons.append("PROPOSAL_EXPIRED")
    except (TypeError, ValueError):
        reasons.append("EXPIRY_INVALID")

    policy = policy_map.get(proposal.action_type)
    if policy is None:
        reasons.append("ACTION_UNSUPPORTED")
    else:
        if proposal.target_type not in policy.allowed_target_types:
            reasons.append("TARGET_NOT_ALLOWED")
        if proposal.requested_authority not in policy.allowed_requested_authority:
            reasons.append("AUTHORITY_NOT_ALLOWED")
        if proposal.risk_class not in policy.allowed_risk_classes:
            reasons.append("RISK_NOT_ALLOWED")
        if policy.required_evidence and not proposal.evidence_refs:
            reasons.append("EVIDENCE_REQUIRED")
        if evidence_exists is not None:
            missing = [ref for ref in proposal.evidence_refs if not evidence_exists(ref)]
            if missing:
                reasons.append("EVIDENCE_MISSING")

    if reasons:
        return FirewallDecision(DENY, tuple(sorted(set(reasons))), proposal.proposal_id, proposal.proposal_sha256)
    if policy and policy.human_review:
        return FirewallDecision(HUMAN_REVIEW, ("HUMAN_APPROVAL_REQUIRED",), proposal.proposal_id, proposal.proposal_sha256)
    return FirewallDecision(ADMIT, ("POLICY_ADMITTED",), proposal.proposal_id, proposal.proposal_sha256)
