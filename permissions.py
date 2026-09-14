"""Fine-grained authorization for Bookkeeper Assistant."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


PERMISSIONS = (
    "records.read",
    "bookkeeping.write",
    "sync.run",
    "invoice.issue",
    "documents.submit",
    "documents.approve",
    "corrections.propose",
    "corrections.approve",
    "company.configure",
    "users.manage",
)

DEFAULT_ROLE_PERMISSIONS = {
    "Administrator": set(PERMISSIONS),
    "Owner": {
        "records.read", "bookkeeping.write", "sync.run", "invoice.issue",
        "documents.submit", "documents.approve", "corrections.propose", "corrections.approve",
    },
    "Bookkeeper": {
        "records.read", "bookkeeping.write", "sync.run", "invoice.issue",
        "documents.submit", "documents.approve", "corrections.propose",
    },
    "Viewer": {"records.read"},
}

# Each permission expansion is migrated once. This preserves historical access
# for roles that previously relied on bookkeeping.write without re-granting a
# permission an administrator later removes deliberately.
PERMISSION_MIGRATIONS = (
    ("action_permissions_v1", {"sync.run", "invoice.issue"}),
    ("action_permissions_v2", {"documents.submit"}),
)


class PermissionStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role TEXT PRIMARY KEY,
                    permissions TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS permission_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            for role, permissions in DEFAULT_ROLE_PERMISSIONS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions(role,permissions) VALUES(?,?)",
                    (role, json.dumps(sorted(permissions))),
                )

            for migration_key, implied_permissions in PERMISSION_MIGRATIONS:
                migrated = conn.execute(
                    "SELECT value FROM permission_meta WHERE key=?",
                    (migration_key,),
                ).fetchone()
                if migrated is not None:
                    continue
                rows = conn.execute("SELECT role,permissions FROM role_permissions").fetchall()
                for row in rows:
                    current = set(json.loads(row["permissions"]))
                    upgraded = set(current)
                    if row["role"] == "Administrator":
                        upgraded = set(PERMISSIONS)
                    elif "bookkeeping.write" in current:
                        upgraded.update(implied_permissions)
                    if upgraded != current:
                        conn.execute(
                            "UPDATE role_permissions SET permissions=? WHERE role=?",
                            (json.dumps(sorted(upgraded)), row["role"]),
                        )
                conn.execute(
                    "INSERT INTO permission_meta(key,value) VALUES(?,?)",
                    (migration_key, "complete"),
                )

    def roles(self) -> dict[str, set[str]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT role,permissions FROM role_permissions ORDER BY role").fetchall()
        return {row["role"]: set(json.loads(row["permissions"])) for row in rows}

    def permissions_for_role(self, role: str) -> set[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT permissions FROM role_permissions WHERE role=?", (role,)).fetchone()
        return set(json.loads(row["permissions"])) if row else set()

    def set_role_permissions(self, role: str, permissions: set[str]) -> None:
        role = role.strip()
        if not role:
            raise ValueError("Role name is required")
        unknown = set(permissions) - set(PERMISSIONS)
        if unknown:
            raise ValueError(f"Unknown permissions: {', '.join(sorted(unknown))}")
        if role == "Administrator" and set(permissions) != set(PERMISSIONS):
            raise ValueError("Administrator permissions cannot be reduced")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO role_permissions(role,permissions) VALUES(?,?) "
                "ON CONFLICT(role) DO UPDATE SET permissions=excluded.permissions",
                (role, json.dumps(sorted(permissions))),
            )

    def user_has(self, user, permission: str) -> bool:
        if user is None or not user.active:
            return False
        if permission not in PERMISSIONS:
            return False
        return permission in self.permissions_for_role(user.role)
