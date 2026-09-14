"""Atomic unit of work for ordinary consequential bookkeeping mutations.

For the local-first single-process pilot model, validation, in-memory mutation,
SQLite persistence, and the durable audit event execute under one process lock
and one BEGIN IMMEDIATE transaction. On any failure the in-memory book is
restored to its pre-mutation snapshot.

This does not make multiple independent web-worker processes safe; that remains
a deployment boundary until state is loaded/mutated directly inside the shared
database transaction.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable, TypeVar

from atomic_uow import _audit_row, _persist_book, _restore_book
from sqlite_store import _connect, initialize

T = TypeVar("T")


class FinancialMutationUnitOfWork:
    def __init__(self, db_path: str | Path, book):
        self.db_path = Path(db_path)
        self.book = book
        self._lock = threading.RLock()
        initialize(self.db_path)

    def commit(
        self,
        mutate: Callable[[], T],
        *,
        event_type: str,
        evidence_id: str | Callable[[T], str],
        payload: dict[str, object] | Callable[[T], dict[str, object]],
        actor: str,
    ) -> T:
        """Commit one local financial transition and its audit receipt atomically."""
        with self._lock:
            snapshot = copy.deepcopy(self.book)
            try:
                with _connect(self.db_path) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    result = mutate()
                    _persist_book(conn, self.book)
                    resolved_evidence = evidence_id(result) if callable(evidence_id) else evidence_id
                    resolved_payload = payload(result) if callable(payload) else payload
                    _audit_row(conn, event_type, resolved_evidence, resolved_payload, actor)
                    return result
            except Exception:
                _restore_book(self.book, snapshot)
                raise
