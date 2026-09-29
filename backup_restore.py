"""Verified backup/restore for the local-first Bookkeeper Assistant.

A backup bundle contains a consistent SQLite snapshot, the evidence vault, and a
manifest binding every file plus the audit-chain head. Restore verifies the
bundle before replacing live state and retains rollback copies until the
installed restore has also been verified.

The credential-encryption key is intentionally NOT stored in the bundle. It must
be backed up separately and remain stable if encrypted connector credentials are
to remain usable after restore.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from audit_integrity import AuditIntegrityStore, append_audit_row, verify_audit_chain

BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "bookkeeper.sqlite3"
EVIDENCE_DIR_NAME = "evidence"


@dataclass(frozen=True)
class BackupVerification:
    valid: bool
    detail: str
    audit_head_hash: str | None = None
    audit_event_count: int = 0
    evidence_file_count: int = 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _checkpoint_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise ValueError(f"SQLite WAL checkpoint could not complete: {tuple(checkpoint)}")
        mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
        if mode != "delete":
            raise ValueError(f"SQLite snapshot could not leave WAL mode: {mode}")
        conn.commit()
    finally:
        conn.close()


def _reject_sqlite_sidecars(path: Path) -> None:
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    present = [p.name for p in sidecars if p.exists()]
    if present:
        raise ValueError(f"Backup contains unmanifested SQLite sidecar files: {present}")


def _sqlite_integrity(path: Path) -> None:
    try:
        with closing(_readonly_connect(path)) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"SQLite backup cannot be opened: {exc}") from exc
    if not row or str(row[0]).lower() != "ok":
        raise ValueError(f"SQLite integrity_check failed: {row[0] if row else 'no result'}")


def _schema_version(path: Path) -> str | None:
    with closing(_readonly_connect(path)) as conn:
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError:
            return None
    return str(row[0]) if row else None


def _audit_verification(path: Path):
    with closing(_readonly_connect(path)) as conn:
        return verify_audit_chain(conn)


def _evidence_rows(path: Path) -> list[dict[str, object]]:
    with closing(_readonly_connect(path)) as conn:
        try:
            rows = conn.execute("SELECT evidence_id,sha256,data FROM source_documents ORDER BY evidence_id").fetchall()
        except sqlite3.DatabaseError:
            return []
    return [{"evidence_id": row["evidence_id"], "sha256": row["sha256"], "data": json.loads(row["data"])} for row in rows]


def _evidence_hash_map(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                result[_sha256_file(path)] = path
    return result


def _copy_evidence(evidence_dir: Path, target: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not evidence_dir.exists():
        return rows
    for source in sorted(p for p in evidence_dir.rglob("*") if p.is_file()):
        relative = source.relative_to(evidence_dir)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        rows.append({"path": relative.as_posix(), "size_bytes": destination.stat().st_size, "sha256": _sha256_file(destination)})
    return rows


def _verify_source_documents(db_path: Path, evidence_root: Path) -> None:
    files_by_hash = _evidence_hash_map(evidence_root)
    for row in _evidence_rows(db_path):
        expected = str(row["sha256"])
        if expected not in files_by_hash:
            raise ValueError(f"Evidence {row['evidence_id']} with SHA-256 {expected} is missing from evidence vault")


def _publish_backup(staging: Path, destination: Path) -> None:
    """Publish a verified bundle using the manifest as the commit marker."""
    destination.mkdir(parents=False, exist_ok=False)
    committed = False
    try:
        source_db = staging / DATABASE_NAME
        os.replace(source_db, destination / DATABASE_NAME)
        source_evidence = staging / EVIDENCE_DIR_NAME
        if source_evidence.exists():
            shutil.copytree(source_evidence, destination / EVIDENCE_DIR_NAME)
        os.replace(staging / MANIFEST_NAME, destination / MANIFEST_NAME)
        check = verify_backup(destination)
        if not check.valid:
            raise ValueError(f"Published backup failed verification: {check.detail}")
        committed = True
    finally:
        if not committed:
            shutil.rmtree(destination, ignore_errors=True)


def create_backup(db_path: str | Path, evidence_dir: str | Path, backup_dir: str | Path) -> Path:
    source_db = Path(db_path)
    source_evidence = Path(evidence_dir)
    destination = Path(backup_dir)
    if destination.exists():
        raise FileExistsError(f"Backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        snapshot = staging / DATABASE_NAME
        with closing(sqlite3.connect(source_db)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
            target.commit()
        _checkpoint_database(snapshot)
        _reject_sqlite_sidecars(snapshot)
        _sqlite_integrity(snapshot)
        audit = _audit_verification(snapshot)
        if not audit.valid:
            raise ValueError(f"Source audit chain is invalid at sequence {audit.error_seq}: {audit.detail}")
        evidence_root = staging / EVIDENCE_DIR_NAME
        evidence_rows = _copy_evidence(source_evidence, evidence_root)
        _verify_source_documents(snapshot, evidence_root)
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": _schema_version(snapshot),
            "credential_key_included": False,
            "database": {"path": DATABASE_NAME, "size_bytes": snapshot.stat().st_size, "sha256": _sha256_file(snapshot)},
            "audit": {"event_count": audit.event_count, "head_hash": audit.head_hash},
            "evidence": evidence_rows,
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        check = verify_backup(staging)
        if not check.valid:
            raise ValueError(f"New backup failed self-verification: {check.detail}")
        _publish_backup(staging, destination)
        return destination
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def verify_backup(backup_dir: str | Path) -> BackupVerification:
    bundle = Path(backup_dir)
    try:
        manifest_path = bundle / MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError("Backup manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError(f"Unsupported backup format version: {manifest.get('format_version')}")
        db_meta = manifest.get("database") or {}
        db_path = bundle / str(db_meta.get("path") or DATABASE_NAME)
        if not db_path.is_file():
            raise ValueError("Backup database is missing")
        _reject_sqlite_sidecars(db_path)
        if db_path.stat().st_size != int(db_meta.get("size_bytes", -1)):
            raise ValueError("Backup database size does not match manifest")
        if _sha256_file(db_path) != db_meta.get("sha256"):
            raise ValueError("Backup database SHA-256 does not match manifest")
        evidence_root = bundle / EVIDENCE_DIR_NAME
        expected_paths: set[str] = set()
        for row in manifest.get("evidence") or []:
            relative = str(row["path"])
            expected_paths.add(relative)
            path = evidence_root / relative
            if not path.is_file():
                raise ValueError(f"Evidence file is missing: {relative}")
            if path.stat().st_size != int(row["size_bytes"]):
                raise ValueError(f"Evidence file size mismatch: {relative}")
            if _sha256_file(path) != row["sha256"]:
                raise ValueError(f"Evidence file SHA-256 mismatch: {relative}")
        actual_paths = {path.relative_to(evidence_root).as_posix() for path in evidence_root.rglob("*") if path.is_file()} if evidence_root.exists() else set()
        unexpected = actual_paths - expected_paths
        if unexpected:
            raise ValueError(f"Backup contains unmanifested evidence files: {sorted(unexpected)}")
        _sqlite_integrity(db_path)
        audit = _audit_verification(db_path)
        if not audit.valid:
            raise ValueError(f"Audit chain verification failed at sequence {audit.error_seq}: {audit.detail}")
        audit_meta = manifest.get("audit") or {}
        if audit.head_hash != audit_meta.get("head_hash"):
            raise ValueError("Audit-chain head does not match backup manifest")
        if audit.event_count != int(audit_meta.get("event_count", -1)):
            raise ValueError("Audit event count does not match backup manifest")
        _verify_source_documents(db_path, evidence_root)
        return BackupVerification(True, "Backup integrity verified", audit_head_hash=audit.head_hash, audit_event_count=audit.event_count, evidence_file_count=len(expected_paths))
    except (ValueError, OSError, sqlite3.DatabaseError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return BackupVerification(False, str(exc))


def _relocate_source_document_paths(db_path: Path, staged_evidence_dir: Path, final_evidence_dir: Path) -> None:
    staged_by_hash = _evidence_hash_map(staged_evidence_dir)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT evidence_id,sha256,data FROM source_documents").fetchall()
        for row in rows:
            staged_file = staged_by_hash.get(row["sha256"])
            if staged_file is None:
                raise ValueError(f"Restored evidence file missing for {row['evidence_id']}")
            relative = staged_file.relative_to(staged_evidence_dir)
            data = json.loads(row["data"])
            data["stored_path"] = str(final_evidence_dir / relative)
            conn.execute("UPDATE source_documents SET data=? WHERE evidence_id=?", (json.dumps(data, default=str, separators=(",", ":")), row["evidence_id"]))
        conn.commit()


def _restore_rollback(target_db: Path, target_evidence: Path, rollback_db: Path, rollback_evidence: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(target_db) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    if target_db.exists():
        target_db.unlink()
    if target_evidence.exists():
        shutil.rmtree(target_evidence, ignore_errors=True)
    if rollback_db.exists():
        os.replace(rollback_db, target_db)
    if rollback_evidence.exists():
        os.replace(rollback_evidence, target_evidence)


def restore_backup(backup_dir: str | Path, target_db_path: str | Path, target_evidence_dir: str | Path, *, actor: str = "administrator") -> None:
    verification = verify_backup(backup_dir)
    if not verification.valid:
        raise ValueError(f"Restore refused: {verification.detail}")
    bundle = Path(backup_dir)
    target_db = Path(target_db_path)
    target_evidence = Path(target_evidence_dir)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target_evidence.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".bookkeeper-restore-", dir=target_db.parent))
    staged_db = stage_root / DATABASE_NAME
    staged_evidence = stage_root / EVIDENCE_DIR_NAME
    rollback_db = target_db.with_name(target_db.name + ".pre-restore")
    rollback_evidence = target_evidence.with_name(target_evidence.name + ".pre-restore")
    swapped = False
    try:
        shutil.copy2(bundle / DATABASE_NAME, staged_db)
        if (bundle / EVIDENCE_DIR_NAME).exists():
            shutil.copytree(bundle / EVIDENCE_DIR_NAME, staged_evidence)
        else:
            staged_evidence.mkdir(parents=True)
        _relocate_source_document_paths(staged_db, staged_evidence, target_evidence)
        _checkpoint_database(staged_db)
        _sqlite_integrity(staged_db)
        audit = _audit_verification(staged_db)
        if not audit.valid:
            raise ValueError(f"Staged restore audit chain invalid: {audit.detail}")
        _verify_source_documents(staged_db, staged_evidence)
        AuditIntegrityStore(staged_db)
        with closing(sqlite3.connect(staged_db)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            append_audit_row(conn, "system.restore.completed", "SYSTEM:BACKUP", {"backup_audit_head": verification.audit_head_hash, "restored_at": datetime.now(timezone.utc).isoformat()}, actor)
            conn.commit()
        _checkpoint_database(staged_db)
        _reject_sqlite_sidecars(staged_db)
        if rollback_db.exists():
            rollback_db.unlink()
        if rollback_evidence.exists():
            shutil.rmtree(rollback_evidence)
        if target_db.exists():
            os.replace(target_db, rollback_db)
        if target_evidence.exists():
            os.replace(target_evidence, rollback_evidence)
        os.replace(staged_db, target_db)
        os.replace(staged_evidence, target_evidence)
        swapped = True
        _sqlite_integrity(target_db)
        installed_audit = _audit_verification(target_db)
        if not installed_audit.valid:
            raise ValueError(f"Installed restore audit chain invalid: {installed_audit.detail}")
        _verify_source_documents(target_db, target_evidence)
        if rollback_db.exists():
            rollback_db.unlink()
        if rollback_evidence.exists():
            shutil.rmtree(rollback_evidence)
        swapped = False
    except Exception:
        if swapped or rollback_db.exists() or rollback_evidence.exists():
            _restore_rollback(target_db, target_evidence, rollback_db, rollback_evidence)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
