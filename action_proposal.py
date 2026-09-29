"""Universal, proposal-only action envelope for governed automation.

BK-AI-002 establishes a provider-neutral representation for consequential actions.
An ActionProposal is data, not authority: this module contains no connector calls
and no bookkeeping mutation path.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROPOSAL_SCHEMA_VERSION = "action-proposal.v1"
ALLOWED_SOURCE_TYPES = frozenset({"ai", "human", "automation", "connector"})
ALLOWED_REQUESTED_AUTHORITY = frozenset({"observe", "propose", "prepare"})
ALLOWED_RISK_CLASSES = frozenset({"informational", "operational", "financial", "high_risk"})
ALLOWED_STATUSES = frozenset({"proposed", "rejected", "expired", "superseded"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    schema_version: str
    created_at: str
    source_type: str
    source_provider: str
    source_model: str
    actor: str
    action_type: str
    target_type: str
    target_id: str | None
    arguments: dict[str, Any]
    evidence_refs: tuple[str, ...]
    rationale: str
    requested_authority: str
    risk_class: str
    prompt_sha256: str | None
    context_sha256: str | None
    expires_at: str | None
    status: str
    proposal_sha256: str

    def canonical_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("proposal_sha256", None)
        return data

    def verify_hash(self) -> bool:
        return self.proposal_sha256 == _sha256(self.canonical_payload())


def create_action_proposal(*, source_type: str, source_provider: str, source_model: str,
                           actor: str, action_type: str, target_type: str,
                           arguments: dict[str, Any], evidence_refs: list[str] | tuple[str, ...] = (),
                           rationale: str = "", requested_authority: str = "propose",
                           risk_class: str = "operational", target_id: str | None = None,
                           prompt_sha256: str | None = None, context_sha256: str | None = None,
                           expires_at: str | None = None, proposal_id: str | None = None,
                           created_at: str | None = None) -> ActionProposal:
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError(f"Unsupported proposal source type: {source_type}")
    if requested_authority not in ALLOWED_REQUESTED_AUTHORITY:
        raise ValueError("Action proposals cannot request execution authority")
    if risk_class not in ALLOWED_RISK_CLASSES:
        raise ValueError(f"Unsupported proposal risk class: {risk_class}")
    if not action_type.strip() or not target_type.strip():
        raise ValueError("action_type and target_type are required")
    if not isinstance(arguments, dict):
        raise ValueError("proposal arguments must be an object")
    refs = tuple(sorted(set(str(x).strip() for x in evidence_refs if str(x).strip())))
    base = {
        "proposal_id": proposal_id or f"AP-{uuid.uuid4().hex[:20]}",
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "created_at": created_at or _utcnow(),
        "source_type": source_type,
        "source_provider": source_provider.strip(),
        "source_model": source_model.strip(),
        "actor": actor.strip(),
        "action_type": action_type.strip(),
        "target_type": target_type.strip(),
        "target_id": target_id.strip() if isinstance(target_id, str) and target_id.strip() else None,
        "arguments": arguments,
        "evidence_refs": refs,
        "rationale": rationale.strip(),
        "requested_authority": requested_authority,
        "risk_class": risk_class,
        "prompt_sha256": prompt_sha256,
        "context_sha256": context_sha256,
        "expires_at": expires_at,
        "status": "proposed",
    }
    return ActionProposal(**base, proposal_sha256=_sha256(base))


class ActionProposalStore:
    """Durable proposal ledger. It stores proposals but cannot execute them."""
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS action_proposals (
                proposal_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_provider TEXT NOT NULL,
                source_model TEXT NOT NULL,
                actor TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                arguments_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                requested_authority TEXT NOT NULL,
                risk_class TEXT NOT NULL,
                prompt_sha256 TEXT,
                context_sha256 TEXT,
                expires_at TEXT,
                status TEXT NOT NULL,
                proposal_sha256 TEXT NOT NULL UNIQUE
            )""")
            conn.commit()
        finally:
            conn.close()

    def add(self, proposal: ActionProposal) -> None:
        if proposal.schema_version != PROPOSAL_SCHEMA_VERSION:
            raise ValueError("Unsupported action proposal schema")
        if proposal.requested_authority not in ALLOWED_REQUESTED_AUTHORITY:
            raise ValueError("Action proposals cannot request execution authority")
        if proposal.status not in ALLOWED_STATUSES:
            raise ValueError("Unsupported proposal status")
        if not proposal.verify_hash():
            raise ValueError("Action proposal hash does not match proposal contents")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""INSERT INTO action_proposals(
                proposal_id,schema_version,created_at,source_type,source_provider,source_model,actor,
                action_type,target_type,target_id,arguments_json,evidence_refs_json,rationale,
                requested_authority,risk_class,prompt_sha256,context_sha256,expires_at,status,proposal_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                proposal.proposal_id, proposal.schema_version, proposal.created_at, proposal.source_type,
                proposal.source_provider, proposal.source_model, proposal.actor, proposal.action_type,
                proposal.target_type, proposal.target_id, _canonical(proposal.arguments),
                _canonical(proposal.evidence_refs), proposal.rationale, proposal.requested_authority,
                proposal.risk_class, proposal.prompt_sha256, proposal.context_sha256,
                proposal.expires_at, proposal.status, proposal.proposal_sha256,
            ))
            conn.commit()
        finally:
            conn.close()

    def get(self, proposal_id: str) -> ActionProposal | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ActionProposal(
            proposal_id=row["proposal_id"], schema_version=row["schema_version"], created_at=row["created_at"],
            source_type=row["source_type"], source_provider=row["source_provider"], source_model=row["source_model"],
            actor=row["actor"], action_type=row["action_type"], target_type=row["target_type"],
            target_id=row["target_id"], arguments=json.loads(row["arguments_json"]),
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])), rationale=row["rationale"],
            requested_authority=row["requested_authority"], risk_class=row["risk_class"],
            prompt_sha256=row["prompt_sha256"], context_sha256=row["context_sha256"],
            expires_at=row["expires_at"], status=row["status"], proposal_sha256=row["proposal_sha256"],
        )
