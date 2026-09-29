"""Local tamper-evident integrity for the durable audit trail.

Each event commits the hash of the previous event and its own canonical contents.
SQLite triggers reject UPDATE and DELETE and reject unhashed future inserts.

This proves local chain consistency; it does NOT protect against an attacker with
full database/file authority who can drop triggers and rewrite the entire chain.
External checkpoint anchoring is a separate control.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditChainVerification:
    valid: bool
    event_count: int
    head_hash: str
    error_seq: int | None = None
    detail: str | None = None


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()}


def _canonical_event_hash(
    prev_hash: str,
    event_id: str,
    event_type: str,
    evidence_id: str,
    actor: str,
    created_at: str,
    data_text: str,
) -> str:
    try:
        payload = json.loads(data_text)
    except json.JSONDecodeError:
        payload = data_text
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "event_id": event_id,
            "event_type": event_type,
            "evidence_id": evidence_id,
            "actor": actor,
            "created_at": created_at,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    columns = _columns(conn)
    if "prev_hash" not in columns:
        conn.execute("ALTER TABLE audit_events ADD COLUMN prev_hash TEXT")
    if "event_hash" not in columns:
        conn.execute("ALTER TABLE audit_events ADD COLUMN event_hash TEXT")


def _backfill_legacy_chain(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT seq,event_id,event_type,evidence_id,actor,created_at,data,prev_hash,event_hash "
        "FROM audit_events ORDER BY seq"
    ).fetchall()
    if not rows:
        return
    missing = [row for row in rows if not row["prev_hash"] or not row["event_hash"]]
    if not missing:
        return
    if len(missing) != len(rows):
        raise RuntimeError("Audit chain is partially initialized; refusing automatic repair")

    prev = GENESIS_HASH
    for row in rows:
        event_hash = _canonical_event_hash(
            prev, row["event_id"], row["event_type"], row["evidence_id"],
            row["actor"], row["created_at"], row["data"],
        )
        conn.execute(
            "UPDATE audit_events SET prev_hash=?, event_hash=? WHERE seq=?",
            (prev, event_hash, row["seq"]),
        )
        prev = event_hash


def _install_append_only_triggers(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS audit_events_no_update
        BEFORE UPDATE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events are append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
        BEFORE DELETE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events are append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS audit_events_require_chain
        BEFORE INSERT ON audit_events
        WHEN NEW.prev_hash IS NULL OR NEW.event_hash IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'audit event hash chain is required');
        END;
        """
    )


def _integrity_ready(conn: sqlite3.Connection) -> bool:
    columns = _columns(conn)
    if not {"prev_hash", "event_hash"}.issubset(columns):
        return False
    trigger = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='audit_events_no_update'"
    ).fetchone()
    return trigger is not None


def ensure_audit_integrity(conn: sqlite3.Connection) -> None:
    """Migrate legacy audit rows once and enforce append-only writes afterward.

    This function performs schema/trigger setup and therefore must not be invoked
    from inside a caller-owned financial transaction. SQLite executescript may
    commit a transaction, which would violate atomic rollback guarantees.
    """
    if conn.in_transaction:
        if not _integrity_ready(conn):
            raise RuntimeError("Audit integrity must be initialized before starting a transaction")
        return
    _ensure_columns(conn)
    trigger_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='audit_events_no_update'"
    ).fetchone()
    if trigger_exists is None:
        _backfill_legacy_chain(conn)
        _install_append_only_triggers(conn)


def append_audit_row(
    conn: sqlite3.Connection,
    event_type: str,
    evidence_id: str,
    payload: dict[str, object],
    actor: str,
    *,
    event_id: str | None = None,
    created_at: str | None = None,
) -> str:
    """Append one event to the chain using the caller's transaction when present."""
    if conn.in_transaction:
        if not _integrity_ready(conn):
            raise RuntimeError("Audit integrity must be initialized before starting a transaction")
    else:
        ensure_audit_integrity(conn)
        conn.execute("BEGIN IMMEDIATE")

    prior = conn.execute(
        "SELECT event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = prior["event_hash"] if prior and prior["event_hash"] else GENESIS_HASH
    event_id = event_id or f"AUD-{uuid.uuid4().hex[:16]}"
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    data_text = json.dumps(payload, default=str, separators=(",", ":"))
    event_hash = _canonical_event_hash(
        prev_hash, event_id, event_type, evidence_id, actor, created_at, data_text
    )
    conn.execute(
        """INSERT INTO audit_events(
            event_id,event_type,evidence_id,actor,created_at,data,prev_hash,event_hash
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (event_id, event_type, evidence_id, actor, created_at, data_text, prev_hash, event_hash),
    )
    return event_id


def verify_audit_chain(conn: sqlite3.Connection) -> AuditChainVerification:
    ensure_audit_integrity(conn)
    rows = conn.execute(
        "SELECT seq,event_id,event_type,evidence_id,actor,created_at,data,prev_hash,event_hash "
        "FROM audit_events ORDER BY seq"
    ).fetchall()
    prev = GENESIS_HASH
    for row in rows:
        if row["prev_hash"] != prev:
            return AuditChainVerification(
                False, len(rows), prev, int(row["seq"]), "Previous-hash link does not match"
            )
        expected = _canonical_event_hash(
            prev, row["event_id"], row["event_type"], row["evidence_id"],
            row["actor"], row["created_at"], row["data"],
        )
        if row["event_hash"] != expected:
            return AuditChainVerification(
                False, len(rows), prev, int(row["seq"]), "Event hash does not match event contents"
            )
        prev = expected
    return AuditChainVerification(True, len(rows), prev)


class AuditIntegrityStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        # sqlite3.Connection.__exit__ commits/rolls back but does not close the
        # connection. Explicit closing is required so Windows does not retain a
        # file handle that prevents backup/restore publication.
        with closing(sqlite3.connect(self.db_path)) as raw:
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys=ON")
            ensure_audit_integrity(raw)
            verification = verify_audit_chain(raw)
            if not verification.valid:
                raise RuntimeError(
                    f"Audit chain verification failed at sequence {verification.error_seq}: {verification.detail}"
                )
            raw.commit()

    def append(self, event_type: str, evidence_id: str, payload: dict[str, object], actor: str = "user"):
        from audit_log import AuditEvent
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            ensure_audit_integrity(conn)
            conn.execute("BEGIN IMMEDIATE")
            created_at = datetime.now(timezone.utc).isoformat()
            event_id = f"AUD-{uuid.uuid4().hex[:16]}"
            append_audit_row(
                conn, event_type, evidence_id, payload, actor,
                event_id=event_id, created_at=created_at,
            )
            conn.commit()
        return AuditEvent(event_id, event_type, created_at, evidence_id, actor, payload)

    def verify(self) -> AuditChainVerification:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            verification = verify_audit_chain(conn)
            conn.commit()
            return verification

    def head_hash(self) -> str:
        return self.verify().head_hash
