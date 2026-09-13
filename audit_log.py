"""Append-only audit trail for document proposals and approvals."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: str
    created_at: str
    evidence_id: str
    actor: str
    payload: dict[str, object]


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(
        self,
        event_type: str,
        evidence_id: str,
        payload: dict[str, object],
        actor: str = "user",
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"AUD-{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            created_at=datetime.now(timezone.utc).isoformat(),
            evidence_id=evidence_id,
            actor=actor,
            payload=payload,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), default=str, separators=(",", ":")) + "\n")
        return event

    def events(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []
        result: list[AuditEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            result.append(AuditEvent(**json.loads(line)))
        return result

    def for_evidence(self, evidence_id: str) -> list[AuditEvent]:
        return [event for event in self.events() if event.evidence_id == evidence_id]
