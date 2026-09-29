from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from action_proposal import create_action_proposal
from ai_authority_boundary import HumanAuthorityEnvelope
from authority import AuthorityGrant
from proposal_approval import ProposalApprovalAuthority
from proposal_execution import (
    UncertainDispatch,
    dispatch_outbox_item,
    enqueue_approved_proposal,
    reconcile_uncertain,
)
from sync_reliability import SyncReliabilityStore

NOW = datetime(2026, 9, 29, 23, 0, tzinfo=timezone.utc)
KEY = b"bk-ai-008-certification-signing-key-0001"


def _proposal():
    return create_action_proposal(
        source_type="ai", source_provider="anthropic", source_model="claude-test",
        actor="scheduled-ai", action_type="work_order.schedule", target_type="work_order",
        target_id="WO-882", arguments={"requested_date":"2026-10-01","requested_time":"10:00"},
        evidence_refs=["customer-message:882"], rationale="Customer agreed.",
        requested_authority="propose", risk_class="operational",
        expires_at=(NOW + timedelta(hours=1)).isoformat(), created_at=NOW.isoformat(),
        proposal_id="AP-882",
    )


def _approved():
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    human = HumanAuthorityEnvelope.from_grant(
        AuthorityGrant("work_order.schedule", "bookkeeping.write", "human-owner-1", "owner")
    )
    return proposal, signer, signer.approve(proposal, human, now=NOW)


def test_exactly_approved_proposal_enqueues_once(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    first = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    second = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    assert first.item_id == second.item_id
    assert first.idempotency_key == second.idempotency_key
    assert len(store.list_outbox()) == 1


def test_changed_proposal_cannot_enqueue_under_old_approval(tmp_path):
    proposal, signer, approval = _approved()
    tampered = replace(proposal, arguments={"requested_date":"2026-10-01","requested_time":"11:00"})
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    with pytest.raises(PermissionError):
        enqueue_approved_proposal(proposal=tampered, approval=approval,
            approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    assert store.list_outbox() == []


def test_successful_dispatch_executes_once(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    item = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    calls = []
    result = dispatch_outbox_item(reliability=store, item_id=item.item_id,
                                  send=lambda x: calls.append(x.item_id) or "JOB-123")
    assert result.status == "EXECUTED"
    assert result.outbox.status == "succeeded"
    assert result.outbox.external_id == "JOB-123"
    assert calls == [item.item_id]
    with pytest.raises(ValueError, match="not retryable"):
        dispatch_outbox_item(reliability=store, item_id=item.item_id, send=lambda x: "JOB-456")
    assert calls == [item.item_id]


def test_timeout_after_send_becomes_uncertain_and_is_not_retryable(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    item = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    result = dispatch_outbox_item(reliability=store, item_id=item.item_id,
                                  send=lambda x: (_ for _ in ()).throw(TimeoutError("ack timeout")))
    assert result.status == "UNCERTAIN"
    assert result.outbox.status == "uncertain"
    with pytest.raises(ValueError, match="not retryable"):
        dispatch_outbox_item(reliability=store, item_id=item.item_id, send=lambda x: "DUPLICATE")


def test_explicit_uncertain_signal_is_not_retried(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    item = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    result = dispatch_outbox_item(reliability=store, item_id=item.item_id,
        send=lambda x: (_ for _ in ()).throw(UncertainDispatch("connection lost after request body")))
    assert result.status == "UNCERTAIN"
    assert result.outbox.attempts == 1


def test_missing_remote_ack_id_is_uncertain(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    item = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    result = dispatch_outbox_item(reliability=store, item_id=item.item_id, send=lambda x: "")
    assert result.status == "UNCERTAIN"


def test_reconciliation_found_remote_marks_executed_without_resend(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    item = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    dispatch_outbox_item(reliability=store, item_id=item.item_id,
        send=lambda x: (_ for _ in ()).throw(TimeoutError("unknown")))
    result = reconcile_uncertain(reliability=store, item_id=item.item_id, remote_external_id="JOB-REMOTE-1")
    assert result.status == "EXECUTED"
    assert result.outbox.status == "succeeded"
    assert result.outbox.external_id == "JOB-REMOTE-1"


def test_reconciliation_proves_absent_before_retry_allowed(tmp_path):
    proposal, signer, approval = _approved()
    store = SyncReliabilityStore(tmp_path / "db.sqlite")
    item = enqueue_approved_proposal(proposal=proposal, approval=approval,
        approval_authority=signer, reliability=store, connector_id="jobber", connector_name="Jobber", now=NOW)
    dispatch_outbox_item(reliability=store, item_id=item.item_id,
        send=lambda x: (_ for _ in ()).throw(TimeoutError("unknown")))
    result = reconcile_uncertain(reliability=store, item_id=item.item_id, remote_external_id=None)
    assert result.status == "RETRY_ALLOWED"
    assert result.outbox.status == "pending"
    retried = dispatch_outbox_item(reliability=store, item_id=item.item_id, send=lambda x: "JOB-NEW")
    assert retried.status == "EXECUTED"
    assert retried.outbox.attempts == 2
