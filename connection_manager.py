"""Encrypted connection records for external systems.

Connector credentials are encrypted at rest with Fernet (AES-backed authenticated
symmetric encryption). The encryption key is supplied separately via
BOOKKEEPER_CREDENTIAL_KEY and is never stored in SQLite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


@dataclass(frozen=True)
class ManagedConnection:
    connection_id: str
    provider: str
    label: str
    status: str
    capabilities: tuple[str, ...]
    created_at: str
    updated_at: str


class CredentialCipher:
    def __init__(self, key: str | bytes):
        raw = key.encode("ascii") if isinstance(key, str) else key
        try:
            self._fernet = Fernet(raw)
        except Exception as exc:
            raise ValueError("BOOKKEEPER_CREDENTIAL_KEY must be a valid Fernet key") from exc

    @classmethod
    def from_environment(cls) -> "CredentialCipher | None":
        key = os.environ.get("BOOKKEEPER_CREDENTIAL_KEY", "").strip()
        return cls(key) if key else None

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def decrypt(self, token: bytes) -> dict[str, Any]:
        try:
            raw = self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise ValueError("Stored connector credential cannot be decrypted with the current key") from exc
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Stored connector credential payload is invalid")
        return data


class ConnectionStore:
    def __init__(self, db_path: str | Path, cipher: CredentialCipher):
        self.db_path = Path(db_path)
        self.cipher = cipher
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS managed_connections (
                    connection_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    secret_data BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save(self, provider: str, label: str, capabilities: list[str] | tuple[str, ...],
             secrets: dict[str, Any], *, connection_id: str | None = None,
             status: str = "configured") -> ManagedConnection:
        now = datetime.now(timezone.utc).isoformat()
        connection_id = connection_id or f"CONN-{uuid.uuid4().hex[:12]}"
        encrypted = self.cipher.encrypt(secrets)
        caps = json.dumps(sorted(set(capabilities)))
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM managed_connections WHERE connection_id=?", (connection_id,)
            ).fetchone()
            created = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO managed_connections(connection_id,provider,label,status,capabilities,secret_data,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(connection_id) DO UPDATE SET
                    provider=excluded.provider,
                    label=excluded.label,
                    status=excluded.status,
                    capabilities=excluded.capabilities,
                    secret_data=excluded.secret_data,
                    updated_at=excluded.updated_at
                """,
                (connection_id, provider, label, status, caps, encrypted, created, now),
            )
        return self.get(connection_id)

    def list(self) -> list[ManagedConnection]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT connection_id,provider,label,status,capabilities,created_at,updated_at "
                "FROM managed_connections ORDER BY provider,label"
            ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, connection_id: str) -> ManagedConnection:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT connection_id,provider,label,status,capabilities,created_at,updated_at "
                "FROM managed_connections WHERE connection_id=?", (connection_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Unknown managed connection")
        return self._record(row)

    def secrets(self, connection_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT secret_data FROM managed_connections WHERE connection_id=?", (connection_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Unknown managed connection")
        return self.cipher.decrypt(row["secret_data"])

    def set_status(self, connection_id: str, status: str) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE managed_connections SET status=?, updated_at=? WHERE connection_id=?",
                (status, datetime.now(timezone.utc).isoformat(), connection_id),
            )
            if cur.rowcount != 1:
                raise KeyError("Unknown managed connection")

    def delete(self, connection_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM managed_connections WHERE connection_id=?", (connection_id,))

    @staticmethod
    def _record(row: sqlite3.Row) -> ManagedConnection:
        return ManagedConnection(
            connection_id=row["connection_id"],
            provider=row["provider"],
            label=row["label"],
            status=row["status"],
            capabilities=tuple(json.loads(row["capabilities"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
