import sqlite3

import pytest

from secure_web_app import create_secure_app


def _db(app):
    return app.config["BOOKKEEPER_DATA_PATH"]


def test_secure_runtime_audit_appends_form_valid_chain(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    audit = app.config["AUDIT_LOG"]
    integrity = app.config["AUDIT_INTEGRITY"]

    audit.append("test.first", "TEST:1", {"value": 1}, actor="tester")
    audit.append("test.second", "TEST:2", {"value": 2}, actor="tester")

    verification = integrity.verify()
    assert verification.valid is True
    assert verification.event_count == 2
    assert verification.head_hash != "0" * 64


def test_audit_rows_reject_update_and_delete(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config["AUDIT_LOG"].append("test.event", "TEST:1", {"value": 1}, actor="tester")

    with sqlite3.connect(_db(app)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE audit_events SET actor='tampered' WHERE seq=1")

    with sqlite3.connect(_db(app)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM audit_events WHERE seq=1")


def test_unhashed_audit_insert_is_rejected(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))

    with sqlite3.connect(_db(app)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="hash chain is required"):
            conn.execute(
                """INSERT INTO audit_events(
                    event_id,event_type,evidence_id,actor,created_at,data
                ) VALUES(?,?,?,?,?,?)""",
                ("AUD-FAKE", "fake", "TEST", "attacker", "2026-09-14T00:00:00Z", "{}"),
            )


def test_verifier_detects_full_database_authority_tampering(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    audit = app.config["AUDIT_LOG"]
    integrity = app.config["AUDIT_INTEGRITY"]
    audit.append("test.first", "TEST:1", {"value": 1}, actor="tester")
    audit.append("test.second", "TEST:2", {"value": 2}, actor="tester")
    assert integrity.verify().valid is True

    # This intentionally models an attacker with full SQLite schema authority.
    # A local trigger cannot stop that attacker from removing the trigger first,
    # but the surviving hash chain must make the rewritten history detectable.
    with sqlite3.connect(_db(app)) as conn:
        conn.execute("DROP TRIGGER audit_events_no_update")
        conn.execute("UPDATE audit_events SET actor='rewritten' WHERE seq=1")

    verification = integrity.verify()
    assert verification.valid is False
    assert verification.error_seq == 1
    assert verification.detail == "Event hash does not match event contents"
