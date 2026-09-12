from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Iterable
from uuid import UUID, uuid4


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: UUID
    kind: str
    source_uri: str
    content_sha256: str
    byte_length: int
    captured_at: datetime
    metadata: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_bytes(
        cls,
        *,
        kind: str,
        source_uri: str,
        content: bytes,
        metadata: dict[str, str] | None = None,
        captured_at: datetime | None = None,
    ) -> "EvidenceRecord":
        return cls(
            evidence_id=uuid4(),
            kind=kind,
            source_uri=source_uri,
            content_sha256=sha256(content).hexdigest(),
            byte_length=len(content),
            captured_at=captured_at or datetime.now(timezone.utc),
            metadata=tuple(sorted((metadata or {}).items())),
        )


@dataclass(frozen=True)
class ProvenanceLink:
    link_id: UUID
    subject_type: str
    subject_id: str
    relation: str
    object_type: str
    object_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        *,
        subject_type: str,
        subject_id: str,
        relation: str,
        object_type: str,
        object_id: str,
    ) -> "ProvenanceLink":
        return cls(
            link_id=uuid4(),
            subject_type=subject_type,
            subject_id=subject_id,
            relation=relation,
            object_type=object_type,
            object_id=object_id,
        )


class EvidenceRegistry:
    """Append-only in-memory kernel for v0.1.

    Persistence adapters can wrap this interface later. Existing records are never
    replaced; conflicting content for the same evidence_id is rejected.
    """

    def __init__(self) -> None:
        self._records: dict[UUID, EvidenceRecord] = {}
        self._by_digest: dict[str, list[UUID]] = {}
        self._links: list[ProvenanceLink] = []

    def add(self, record: EvidenceRecord) -> None:
        existing = self._records.get(record.evidence_id)
        if existing is not None and existing != record:
            raise ValueError("Evidence identity is immutable")
        if existing is not None:
            return
        self._records[record.evidence_id] = record
        self._by_digest.setdefault(record.content_sha256, []).append(record.evidence_id)

    def get(self, evidence_id: UUID) -> EvidenceRecord:
        return self._records[evidence_id]

    def find_duplicates(self, record: EvidenceRecord) -> tuple[EvidenceRecord, ...]:
        ids = self._by_digest.get(record.content_sha256, ())
        return tuple(self._records[eid] for eid in ids if eid != record.evidence_id)

    def add_link(self, link: ProvenanceLink) -> None:
        self._links.append(link)

    def links_for(self, *, object_type: str, object_id: str) -> tuple[ProvenanceLink, ...]:
        return tuple(
            link
            for link in self._links
            if (link.subject_type == object_type and link.subject_id == object_id)
            or (link.object_type == object_type and link.object_id == object_id)
        )

    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records.values())

    def links(self) -> tuple[ProvenanceLink, ...]:
        return tuple(self._links)


def content_digest(chunks: Iterable[bytes]) -> str:
    h = sha256()
    for chunk in chunks:
        h.update(chunk)
    return h.hexdigest()
