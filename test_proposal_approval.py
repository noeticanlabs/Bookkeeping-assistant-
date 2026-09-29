from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from action_proposal import create_action_proposal
from ai_authority_boundary import HumanAuthorityEnvelope
from authority import AuthorityGrant
from proposal_approval import ProposalApprovalAuthority, ProposalApprovalStore


NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)
KEY = b"bk-ai-007-certification-signing-key-0001"


def _proposal(**overrides):
    args = {
        "customer": "Smith Residence",
        "service_address": "123 Main St",
        "requested_date": "2026-10-01",
        "requested_time": "10:00",
        "duration_minutes": 120,
        "technician": "Matt",
        "work_description": "Sink leak",
    }
    args.update(overrides.pop("arguments", {}))
    base = dict(
        source_type="ai", source_provider="anthropic", source_model="claude-test",
        actor="scheduled-ai", action_type="work_order.schedule", target_type="work_order",
        target_id="WO-882", arguments=args, evidence_refs=["customer-message:882"],
        rationale="Customer agreed to Thursday around 10.", requested_authority="propose",
        risk_class="operational", expires_at=(NOW + timedelta(hours=1)).isoformat(),
        created_at=NOW.isoformat(), proposal_id="AP-schedule-882",
    )
    base.update(overrides)
    return create_action_proposal(**base)


def _human(action="work_order.schedule"):
    return HumanAuthorityEnvelope.from_grant(
        AuthorityGrant(action, "bookkeeping.write", "human-owner-1", "owner")
    )


def test_approval_is_bound_to_exact_proposal_hash_and_persists(tmp_path):
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(proposal, _human(), now=NOW)
    assert approval.proposal_id == proposal.proposal_id
    assert approval.proposal_sha256 == proposal.proposal_sha256
    assert approval.action_type == proposal.action_type
    assert signer.verify(approval, proposal, now=NOW + timedelta(minutes=1))
    store = ProposalApprovalStore(tmp_path / "approvals.db")
    store.add(approval)


def test_approved_time_change_requires_new_approval():
    original = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(original, _human(), now=NOW)
    changed = _proposal(arguments={"requested_time": "11:00"})
    assert changed.proposal_sha256 != original.proposal_sha256
    assert not signer.verify(approval, changed, now=NOW + timedelta(minutes=1))


@pytest.mark.parametrize("field,value", [
    ("customer", "Jones Residence"),
    ("service_address", "999 Other St"),
    ("requested_date", "2026-10-02"),
    ("requested_time", "11:00"),
    ("duration_minutes", 60),
    ("technician", "Alex"),
    ("work_description", "Water heater leak"),
])
def test_any_scheduling_argument_change_invalidates_approval(field, value):
    original = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(original, _human(), now=NOW)
    changed = _proposal(arguments={field: value})
    assert not signer.verify(approval, changed, now=NOW + timedelta(minutes=1))


def test_same_contents_with_different_proposal_id_is_not_authorized():
    original = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(original, _human(), now=NOW)
    replacement = _proposal(proposal_id="AP-new-proposal")
    assert not signer.verify(approval, replacement, now=NOW + timedelta(minutes=1))


def test_tampering_after_approval_is_rejected_even_if_id_is_same():
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(proposal, _human(), now=NOW)
    tampered = replace(proposal, arguments={**proposal.arguments, "requested_time": "11:00"})
    assert not tampered.verify_hash()
    assert not signer.verify(approval, tampered, now=NOW + timedelta(minutes=1))


def test_forged_signature_is_rejected():
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(proposal, _human(), now=NOW)
    forged = replace(approval, signature="0" * 64)
    assert not signer.verify(forged, proposal, now=NOW + timedelta(minutes=1))


def test_other_signing_key_cannot_validate_approval():
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(proposal, _human(), now=NOW)
    attacker = ProposalApprovalAuthority(b"different-certification-key-material-0002")
    assert not attacker.verify(approval, proposal, now=NOW + timedelta(minutes=1))


def test_expired_approval_is_rejected():
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(proposal, _human(), ttl_minutes=5, now=NOW)
    assert not signer.verify(approval, proposal, now=NOW + timedelta(minutes=6))


def test_approval_never_outlives_proposal():
    proposal = _proposal(expires_at=(NOW + timedelta(minutes=3)).isoformat())
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(proposal, _human(), ttl_minutes=30, now=NOW)
    assert approval.expires_at == proposal.expires_at
    assert not signer.verify(approval, proposal, now=NOW + timedelta(minutes=4))


def test_wrong_human_action_authority_cannot_approve_schedule():
    proposal = _proposal()
    signer = ProposalApprovalAuthority(KEY)
    with pytest.raises(PermissionError, match="does not authorize"):
        signer.approve(proposal, _human("payment.record"), now=NOW)


def test_approval_is_not_general_permission_for_another_schedule():
    first = _proposal()
    second = _proposal(proposal_id="AP-schedule-999", target_id="WO-999")
    signer = ProposalApprovalAuthority(KEY)
    approval = signer.approve(first, _human(), now=NOW)
    assert not signer.verify(approval, second, now=NOW + timedelta(minutes=1))
