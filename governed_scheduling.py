"""BK-AI-006: governed scheduling.

Natural-language scheduling evidence may become a structured ActionProposal.
This module stops at HUMAN_REVIEW and contains no connector or calendar write.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from action_firewall import FirewallDecision, HUMAN_REVIEW, evaluate_action_proposal
from action_proposal import ActionProposal, ActionProposalStore, create_action_proposal


REQUIRED_SCHEDULE_ARGUMENTS = frozenset({
    "customer",
    "service_address",
    "requested_date",
    "requested_time",
    "duration_minutes",
    "technician",
    "work_description",
})


@dataclass(frozen=True)
class SchedulingProposalResult:
    proposal: ActionProposal
    firewall: FirewallDecision


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_schedule_arguments(arguments: dict[str, Any]) -> None:
    missing = sorted(name for name in REQUIRED_SCHEDULE_ARGUMENTS if arguments.get(name) in (None, ""))
    if missing:
        raise ValueError("Missing scheduling arguments: " + ", ".join(missing))
    duration = arguments.get("duration_minutes")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise ValueError("duration_minutes must be a positive integer")


def propose_work_order_schedule(
    *,
    provider: str,
    model: str,
    actor: str,
    work_order_id: str,
    arguments: dict[str, Any],
    evidence_refs: list[str] | tuple[str, ...],
    rationale: str,
    prompt_text: str = "",
    context: dict[str, Any] | None = None,
    proposal_store: ActionProposalStore | None = None,
    evidence_exists=None,
    ttl_minutes: int = 60,
    now: datetime | None = None,
) -> SchedulingProposalResult:
    """Create and firewall a scheduling proposal. Never schedules externally."""
    _require_schedule_arguments(arguments)
    if not work_order_id.strip():
        raise ValueError("work_order_id is required")
    if not evidence_refs:
        raise ValueError("Scheduling proposals require source evidence")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if ttl_minutes <= 0:
        raise ValueError("ttl_minutes must be positive")
    context_json = json.dumps(context or {}, sort_keys=True, separators=(",", ":"), default=str)
    proposal = create_action_proposal(
        source_type="ai",
        source_provider=provider,
        source_model=model,
        actor=actor,
        action_type="work_order.schedule",
        target_type="work_order",
        target_id=work_order_id,
        arguments=dict(arguments),
        evidence_refs=evidence_refs,
        rationale=rationale,
        requested_authority="propose",
        risk_class="operational",
        prompt_sha256=_sha256_text(prompt_text) if prompt_text else None,
        context_sha256=_sha256_text(context_json),
        expires_at=(current + timedelta(minutes=ttl_minutes)).isoformat(),
        created_at=current.isoformat(),
    )
    decision = evaluate_action_proposal(proposal, evidence_exists=evidence_exists, now=current)
    if decision.decision != HUMAN_REVIEW:
        raise ValueError(f"Scheduling proposal failed governed admission: {decision.reason_codes}")
    if proposal_store is not None:
        proposal_store.add(proposal)
    return SchedulingProposalResult(proposal=proposal, firewall=decision)
