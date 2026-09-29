"""Minimal local authentication and role store for Bookkeeper Assistant."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    created_at: str


class UserStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id,username,display_name,role,active,created_at FROM users ORDER BY username"
            ).fetchall()
        return [User(r["user_id"], r["username"], r["display_name"], r["role"], bool(r["active"]), r["created_at"]) for r in rows]

    def get(self, user_id: str | None) -> User | None:
        if not user_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id,username,display_name,role,active,created_at FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return User(row["user_id"], row["username"], row["display_name"], row["role"], bool(row["active"]), row["created_at"])

    def create_user(self, username: str, display_name: str, role: str, password: str) -> User:
        username = username.strip()
        display_name = display_name.strip()
        role = role.strip()
        if not username or not display_name or not role:
            raise ValueError("Username, display name, and role are required")
        if len(password) < 10:
            raise ValueError("Password must be at least 10 characters")
        user = User(
            user_id=f"USR-{uuid.uuid4().hex[:16]}",
            username=username,
            display_name=display_name,
            role=role,
            active=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users(user_id,username,display_name,role,password_hash,active,created_at) VALUES(?,?,?,?,?,?,?)",
                    (user.user_id, user.username, user.display_name, user.role, generate_password_hash(password), 1, user.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists") from exc
        return user

    def authenticate(self, username: str, password: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
        if not row or not bool(row["active"]) or not check_password_hash(row["password_hash"], password):
            return None
        return User(row["user_id"], row["username"], row["display_name"], row["role"], True, row["created_at"])

    def set_active(self, user_id: str, active: bool) -> None:
        with self._connect() as conn:
            if conn.execute("UPDATE users SET active=? WHERE user_id=?", (1 if active else 0, user_id)).rowcount != 1:
                raise ValueError("Unknown user")


def is_approver(user: User | None, approver_roles: tuple[str, ...]) -> bool:
    if user is None or not user.active:
        return False
    return user.role == "Administrator" or user.role in approver_roles


def is_admin(user: User | None) -> bool:
    return bool(user and user.active and user.role == "Administrator")
