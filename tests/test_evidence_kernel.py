from dataclasses import replace

import pytest

from bookkeeper.evidence.kernel import EvidenceRecord, EvidenceRegistry, ProvenanceLink


def test_same_bytes_produce_same_digest_and_duplicate_detection():
    registry = EvidenceRegistry()
    first = EvidenceRecord.from_bytes(kind="invoice", source_uri="invoice-a.pdf", content=b"same-content")
    second = EvidenceRecord.from_bytes(kind="invoice", source_uri="invoice-b.pdf", content=b"same-content")

    registry.add(first)
    registry.add(second)

    duplicates = registry.find_duplicates(second)
    assert len(duplicates) == 1
    assert duplicates[0].evidence_id == first.evidence_id
    assert first.content_sha256 == second.content_sha256


def test_different_bytes_do_not_collide_in_normal_operation():
    first = EvidenceRecord.from_bytes(kind="receipt", source_uri="r1", content=b"alpha")
    second = EvidenceRecord.from_bytes(kind="receipt", source_uri="r2", content=b"beta")
    assert first.content_sha256 != second.content_sha256


def test_evidence_identity_cannot_be_silently_rewritten():
    registry = EvidenceRegistry()
    original = EvidenceRecord.from_bytes(kind="bank", source_uri="bank.csv", content=b"original")
    registry.add(original)

    forged = replace(original, content_sha256="0" * 64)
    with pytest.raises(ValueError, match="immutable"):
        registry.add(forged)

    assert registry.get(original.evidence_id) == original


def test_provenance_chain_is_explicit():
    registry = EvidenceRegistry()
    evidence = EvidenceRecord.from_bytes(kind="invoice", source_uri="invoice.pdf", content=b"invoice")
    registry.add(evidence)

    tx_id = "tx-001"
    proposal_id = "proposal-001"
    registry.add_link(ProvenanceLink.create(
        subject_type="evidence",
        subject_id=str(evidence.evidence_id),
        relation="supports",
        object_type="transaction",
        object_id=tx_id,
    ))
    registry.add_link(ProvenanceLink.create(
        subject_type="transaction",
        subject_id=tx_id,
        relation="interpreted_as",
        object_type="proposal",
        object_id=proposal_id,
    ))

    tx_links = registry.links_for(object_type="transaction", object_id=tx_id)
    proposal_links = registry.links_for(object_type="proposal", object_id=proposal_id)

    assert len(tx_links) == 2
    assert len(proposal_links) == 1
    assert proposal_links[0].relation == "interpreted_as"
