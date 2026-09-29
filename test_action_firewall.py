from dataclasses import replace
from datetime import datetime, timezone

from action_firewall import ADMIT, DENY, HUMAN_REVIEW, ActionPolicy, evaluate_action_proposal
from action_proposal import create_action_proposal


def _proposal(**overrides):
    args = dict(
        source_type="ai", source_provider="anthropic", source_model="test-model", actor="scheduled-ai",
        action_type="work_order.schedule", target_type="work_order",
        arguments={"customer_id": "C-1", "start": "2026-10-02T10:00:00"},
        evidence_refs=["MSG-882"], rationale="Customer requested Thursday morning",
        requested_authority="propose", risk_class="operational",
    )
    args.update(overrides)
    return create_action_proposal(**args)


def test_schedule_proposal_requires_human_review():
    result = evaluate_action_proposal(_proposal(), evidence_exists=lambda ref: ref == "MSG-882")
    assert result.decision == HUMAN_REVIEW
    assert result.reason_codes == ("HUMAN_APPROVAL_REQUIRED",)


def test_unknown_action_fails_closed():
    result = evaluate_action_proposal(_proposal(action_type="bank.transfer"))
    assert result.decision == DENY
    assert "ACTION_UNSUPPORTED" in result.reason_codes


def test_missing_required_evidence_is_denied():
    result = evaluate_action_proposal(_proposal(evidence_refs=[]))
    assert result.decision == DENY
    assert "EVIDENCE_REQUIRED" in result.reason_codes


def test_referenced_but_missing_evidence_is_denied():
    result = evaluate_action_proposal(_proposal(), evidence_exists=lambda ref: False)
    assert result.decision == DENY
    assert "EVIDENCE_MISSING" in result.reason_codes


def test_wrong_target_is_denied():
    result = evaluate_action_proposal(_proposal(target_type="payment"))
    assert result.decision == DENY
    assert "TARGET_NOT_ALLOWED" in result.reason_codes


def test_tampered_proposal_is_denied():
    proposal = _proposal()
    tampered = replace(proposal, arguments={"customer_id": "C-1", "start": "2026-10-02T08:00:00"})
    result = evaluate_action_proposal(tampered)
    assert result.decision == DENY
    assert "PROPOSAL_HASH_INVALID" in result.reason_codes


def test_expired_proposal_is_denied():
    proposal = _proposal(expires_at="2026-09-01T00:00:00+00:00")
    result = evaluate_action_proposal(proposal, now=datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert result.decision == DENY
    assert "PROPOSAL_EXPIRED" in result.reason_codes


def test_high_risk_schedule_is_denied_by_policy():
    result = evaluate_action_proposal(_proposal(risk_class="high_risk"))
    assert result.decision == DENY
    assert "RISK_NOT_ALLOWED" in result.reason_codes


def test_admit_still_has_no_execution_semantics():
    proposal = _proposal(action_type="safe.observe", target_type="work_order", evidence_refs=[])
    policies = {
        "safe.observe": ActionPolicy(
            action_type="safe.observe",
            allowed_target_types=frozenset({"work_order"}),
            required_evidence=False,
            human_review=False,
            allowed_risk_classes=frozenset({"operational"}),
        )
    }
    result = evaluate_action_proposal(proposal, policies=policies)
    assert result.decision == ADMIT
    assert result.reason_codes == ("POLICY_ADMITTED",)
    # Firewall result contains no callback, connector, or execution token.
    assert set(result.__dict__) == {"decision", "reason_codes", "proposal_id", "proposal_sha256"}
