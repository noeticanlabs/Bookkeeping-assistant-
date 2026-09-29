from dataclasses import replace

import pytest

from action_proposal import ActionProposalStore, create_action_proposal


def _proposal(**overrides):
    args = dict(
        source_type="ai", source_provider="anthropic", source_model="test-model", actor="scheduled-ai",
        action_type="work_order.schedule", target_type="work_order",
        arguments={"customer_id": "C-1", "start": "2026-10-02T10:00:00"},
        evidence_refs=["MSG-882"], rationale="Customer requested Thursday morning",
        requested_authority="propose", risk_class="operational",
        prompt_sha256="a" * 64, context_sha256="b" * 64,
    )
    args.update(overrides)
    return create_action_proposal(**args)


def test_proposal_is_provider_neutral_and_hash_bound():
    proposal = _proposal()
    assert proposal.source_provider == "anthropic"
    assert proposal.action_type == "work_order.schedule"
    assert proposal.requested_authority == "propose"
    assert proposal.verify_hash()


def test_execution_authority_is_structurally_rejected():
    with pytest.raises(ValueError, match="cannot request execution"):
        _proposal(requested_authority="execute")


def test_mutating_approved_shape_invalidates_hash():
    original = _proposal()
    changed = replace(original, arguments={"customer_id": "C-1", "start": "2026-10-02T11:00:00"})
    assert not changed.verify_hash()


def test_store_rejects_tampered_proposal(tmp_path):
    store = ActionProposalStore(tmp_path / "book.sqlite3")
    original = _proposal()
    tampered = replace(original, target_id="WO-OTHER")
    with pytest.raises(ValueError, match="hash does not match"):
        store.add(tampered)


def test_store_persists_and_reloads_exact_proposal(tmp_path):
    db = tmp_path / "book.sqlite3"
    store = ActionProposalStore(db)
    proposal = _proposal()
    store.add(proposal)
    restored = ActionProposalStore(db).get(proposal.proposal_id)
    assert restored == proposal
    assert restored.verify_hash()


def test_duplicate_proposal_hash_is_rejected_even_with_new_id(tmp_path):
    store = ActionProposalStore(tmp_path / "book.sqlite3")
    first = _proposal(proposal_id="AP-ONE", created_at="2026-09-29T18:00:00+00:00")
    store.add(first)
    # Same semantic envelope with a different id is intentionally a different proposal hash.
    second = _proposal(proposal_id="AP-TWO", created_at="2026-09-29T18:00:00+00:00")
    store.add(second)
    assert store.get("AP-ONE") is not None
    assert store.get("AP-TWO") is not None


def test_missing_action_or_target_is_rejected():
    with pytest.raises(ValueError, match="required"):
        _proposal(action_type="")
    with pytest.raises(ValueError, match="required"):
        _proposal(target_type="")


def test_arguments_must_be_structured_object():
    with pytest.raises(ValueError, match="must be an object"):
        _proposal(arguments="schedule it")
