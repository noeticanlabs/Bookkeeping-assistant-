"""BK-AI-008: reliable execution of exactly approved proposals.

Execution begins only after an exact BK-AI-007 approval verifies. Dispatch is
represented by the existing durable outbox. Ambiguous transport outcomes become
UNCERTAIN and are never automatically retried.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from action_proposal import ActionProposal
from proposal_approval import ProposalApproval, ProposalApprovalAuthority
from sync_reliability import OutboxItem, SyncReliabilityStore


class UncertainDispatch(RuntimeError):
    """The request may have reached the remote system; reconciliation is required."""


@dataclass(frozen=True)
class DispatchResult:
    status: str
    outbox: OutboxItem


def enqueue_approved_proposal(*, proposal: ActionProposal, approval: ProposalApproval,
                              approval_authority: ProposalApprovalAuthority,
                              reliability: SyncReliabilityStore,
                              connector_id: str, connector_name: str,
                              now=None) -> OutboxItem:
    if not approval_authority.verify(approval, proposal, now=now):
        raise PermissionError("Exact proposal approval is invalid or expired")
    payload = {
        "proposal_id": proposal.proposal_id,
        "proposal_sha256": proposal.proposal_sha256,
        "approval_id": approval.approval_id,
        "approval_signature": approval.signature,
        "action_type": proposal.action_type,
        "target_type": proposal.target_type,
        "target_id": proposal.target_id,
        "arguments": proposal.arguments,
        "evidence_refs": list(proposal.evidence_refs),
    }
    # proposal_id is the execution object identity. The existing outbox uniqueness
    # constraint therefore makes repeated enqueue requests converge on one item.
    return reliability.enqueue(
        connector_id, connector_name, proposal.action_type,
        "action_proposal", proposal.proposal_id, payload,
    )


def dispatch_outbox_item(*, reliability: SyncReliabilityStore, item_id: str,
                         send: Callable[[OutboxItem], str]) -> DispatchResult:
    item = reliability.begin_send(item_id)
    try:
        external_id = send(item)
        if not isinstance(external_id, str) or not external_id.strip():
            raise UncertainDispatch("Remote acknowledgement did not include an external id")
    except UncertainDispatch as exc:
        reliability.mark_uncertain(item_id, str(exc))
        return DispatchResult("UNCERTAIN", reliability.get_outbox(item_id))
    except Exception as exc:
        # Once send() has been entered, an arbitrary transport/provider exception
        # cannot prove the remote side did not mutate. Fail closed as UNCERTAIN.
        reliability.mark_uncertain(item_id, str(exc))
        return DispatchResult("UNCERTAIN", reliability.get_outbox(item_id))
    reliability.mark_sent(item_id, external_id.strip())
    return DispatchResult("EXECUTED", reliability.get_outbox(item_id))


def reconcile_uncertain(*, reliability: SyncReliabilityStore, item_id: str,
                        remote_external_id: str | None) -> DispatchResult:
    item = reliability.get_outbox(item_id)
    if item.status != "uncertain":
        raise ValueError("Only uncertain dispatches may be reconciled")
    if remote_external_id:
        # Reconciliation proved the mutation exists remotely. Move through the
        # existing sending transition solely so mark_sent retains its invariant.
        reliability.reset_uncertain(item_id)
        reliability.begin_send(item_id)
        reliability.mark_sent(item_id, remote_external_id)
        return DispatchResult("EXECUTED", reliability.get_outbox(item_id))
    # An operator/reconciler has affirmatively established that the remote action
    # does not exist. Only now may the item return to pending for an explicit retry.
    reliability.reset_uncertain(item_id)
    return DispatchResult("RETRY_ALLOWED", reliability.get_outbox(item_id))
