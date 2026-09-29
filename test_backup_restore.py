import json
import sqlite3
from decimal import Decimal

import pytest

from app import Bookkeeper, WorkOrder
from audit_integrity import AuditIntegrityStore
from backup_restore import create_backup, restore_backup, verify_backup
from sqlite_store import SQLiteProvenanceStore, load_bookkeeper, save_bookkeeper


def _build_source(tmp_path):
    db = tmp_path / "source.sqlite3"
    evidence = tmp_path / "source-documents"
    book = Bookkeeper()
    book.add_work_order(WorkOrder("WO-1", "Smith", "Repair", "complete", Decimal("1000")))
    save_bookkeeper(book, db)

    audit = AuditIntegrityStore(db)
    audit.append("work_order.created", "WORK_ORDER:WO-1", {"work_order_id": "WO-1"}, actor="tester")

    source = tmp_path / "receipt.pdf"
    source.write_bytes(b"real receipt bytes")
    provenance = SQLiteProvenanceStore(db, evidence)
    doc = provenance.capture(source, "receipt.pdf")
    return db, evidence, doc


def test_backup_self_verifies_database_evidence_and_audit_chain(tmp_path):
    db, evidence, _ = _build_source(tmp_path)
    bundle = create_backup(db, evidence, tmp_path / "backup-001")

    result = verify_backup(bundle)
    assert result.valid is True
    assert result.audit_event_count == 1
    assert result.evidence_file_count == 1
    assert len(result.audit_head_hash) == 64

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["credential_key_included"] is False
    assert manifest["audit"]["head_hash"] == result.audit_head_hash


def test_evidence_tamper_is_detected_and_restore_is_refused(tmp_path):
    db, evidence, _ = _build_source(tmp_path)
    bundle = create_backup(db, evidence, tmp_path / "backup-001")
    evidence_file = next((bundle / "evidence").iterdir())
    evidence_file.write_bytes(b"tampered receipt")

    result = verify_backup(bundle)
    assert result.valid is False
    assert "Evidence file" in result.detail

    with pytest.raises(ValueError, match="Restore refused"):
        restore_backup(bundle, tmp_path / "restored.sqlite3", tmp_path / "restored-documents")


def test_database_tamper_is_detected_before_restore(tmp_path):
    db, evidence, _ = _build_source(tmp_path)
    bundle = create_backup(db, evidence, tmp_path / "backup-001")

    # Change a durable financial row without updating the manifest.
    with sqlite3.connect(bundle / "bookkeeper.sqlite3") as conn:
        conn.execute("UPDATE work_orders SET data=? WHERE id='WO-1'", ('{\"id\":\"WO-1\",\"customer\":\"ALTERED\"}',))

    result = verify_backup(bundle)
    assert result.valid is False
    assert "database" in result.detail.lower()


def test_restore_relocates_evidence_and_extends_audit_chain(tmp_path):
    db, evidence, doc = _build_source(tmp_path)
    bundle = create_backup(db, evidence, tmp_path / "backup-001")
    backup_check = verify_backup(bundle)

    target_db = tmp_path / "new-install" / "bookkeeper.sqlite3"
    target_evidence = tmp_path / "new-install" / "evidence"
    restore_backup(bundle, target_db, target_evidence, actor="restore-admin")

    restored = load_bookkeeper(target_db)
    assert restored.work_orders["WO-1"].customer == "Smith"

    restored_provenance = SQLiteProvenanceStore(target_db, target_evidence)
    restored_doc = restored_provenance.documents[doc.evidence_id]
    restored_path = target_evidence / (tmp_path / restored_doc.stored_path).name
    assert restored_doc.stored_path.startswith(str(target_evidence))
    assert restored_path.exists()
    assert restored_provenance.sha256_file(restored_path) == doc.sha256

    audit = AuditIntegrityStore(target_db)
    verification = audit.verify()
    assert verification.valid is True
    assert verification.event_count == backup_check.audit_event_count + 1
    assert audit.verify().head_hash != backup_check.audit_head_hash


def test_invalid_backup_does_not_replace_existing_live_state(tmp_path):
    source_db, source_evidence, _ = _build_source(tmp_path)
    bundle = create_backup(source_db, source_evidence, tmp_path / "backup-001")
    next((bundle / "evidence").iterdir()).write_bytes(b"corrupt")

    target_root = tmp_path / "live"
    target_root.mkdir()
    target_db = target_root / "bookkeeper.sqlite3"
    target_evidence = target_root / "evidence"
    target_evidence.mkdir()

    live = Bookkeeper()
    live.add_work_order(WorkOrder("LIVE-1", "Existing", "Keep me", "open", Decimal("50")))
    save_bookkeeper(live, target_db)
    AuditIntegrityStore(target_db).append("live.marker", "WORK_ORDER:LIVE-1", {}, actor="tester")
    marker = target_evidence / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="Restore refused"):
        restore_backup(bundle, target_db, target_evidence)

    still_live = load_bookkeeper(target_db)
    assert "LIVE-1" in still_live.work_orders
    assert marker.read_text(encoding="utf-8") == "keep"
    assert AuditIntegrityStore(target_db).verify().valid is True
