from datetime import datetime, timezone

import pytest

from action_firewall import HUMAN_REVIEW
from action_proposal import ActionProposalStore
from governed_scheduling import propose_work_order_schedule


def _args():
    return {
        "customer": "Smith Residence",
        "service_address": "123 Main St",
        "requested_date": "2026-10-01",
        "requested_time": "10:00",
        "duration_minutes": 120,
        "technician": "Matt",
        "work_description": "Sink leak",
    }


def test_plumber_message_becomes_proposal_requiring_human_review(tmp_path):
    store = ActionProposalStore(tmp_path / "proposals.db")
    result = propose_work_order_schedule(
        provider="anthropic",
        model="claude-test",
        actor="scheduled-ai",
        work_order_id="WO-882",
        arguments=_args(),
        evidence_refs=["customer-message:882"],
        rationale="Customer stated Thursday around 10 works and reported a sink leak.",
        prompt_text="Extract a scheduling proposal only.",
        context={"source": "customer-message:882"},
        proposal_store=store,
        evidence_exists=lambda ref: ref == "customer-message:882",
        now=datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc),
    )
    assert result.proposal.action_type == "work_order.schedule"
    assert result.proposal.target_type == "work_order"
    assert result.proposal.target_id == "WO-882"
    assert result.proposal.requested_authority == "propose"
    assert result.proposal.risk_class == "operational"
    assert result.proposal.verify_hash()
    assert result.firewall.decision == HUMAN_REVIEW
    assert result.firewall.reason_codes == ("HUMAN_APPROVAL_REQUIRED",)
    assert store.get(result.proposal.proposal_id) == result.proposal


def test_schedule_proposal_has_no_execution_side_effect():
    external_calendar = []
    result = propose_work_order_schedule(
        provider="openai",
        model="test",
        actor="scheduled-ai",
        work_order_id="WO-1",
        arguments=_args(),
        evidence_refs=["message:1"],
        rationale="Scheduling evidence",
        evidence_exists=lambda ref: True,
    )
    assert result.firewall.decision == HUMAN_REVIEW
    assert external_calendar == []


def test_missing_evidence_is_rejected_before_proposal():
    with pytest.raises(ValueError, match="source evidence"):
        propose_work_order_schedule(
            provider="anthropic", model="test", actor="scheduled-ai",
            work_order_id="WO-1", arguments=_args(), evidence_refs=[], rationale="guess"
        )


def test_nonexistent_evidence_cannot_reach_human_review():
    with pytest.raises(ValueError, match="failed governed admission"):
        propose_work_order_schedule(
            provider="anthropic", model="test", actor="scheduled-ai",
            work_order_id="WO-1", arguments=_args(), evidence_refs=["fake:1"],
            rationale="unsupported", evidence_exists=lambda ref: False,
        )


def test_incomplete_schedule_is_rejected():
    args = _args()
    del args["requested_time"]
    with pytest.raises(ValueError, match="requested_time"):
        propose_work_order_schedule(
            provider="anthropic", model="test", actor="scheduled-ai",
            work_order_id="WO-1", arguments=args, evidence_refs=["message:1"], rationale="incomplete"
        )


def test_invalid_duration_is_rejected():
    args = _args()
    args["duration_minutes"] = 0
    with pytest.raises(ValueError, match="positive integer"):
        propose_work_order_schedule(
            provider="anthropic", model="test", actor="scheduled-ai",
            work_order_id="WO-1", arguments=args, evidence_refs=["message:1"], rationale="bad duration"
        )


def test_provider_does_not_change_scheduling_authority():
    decisions = []
    for provider in ("openai", "anthropic"):
        result = propose_work_order_schedule(
            provider=provider, model="test", actor="scheduled-ai",
            work_order_id="WO-1", arguments=_args(), evidence_refs=["message:1"],
            rationale="customer scheduling evidence", evidence_exists=lambda ref: True,
        )
        decisions.append(result.firewall.decision)
        assert result.proposal.requested_authority == "propose"
    assert decisions == [HUMAN_REVIEW, HUMAN_REVIEW]
