"""Small durable provenance model for source documents and interpretations."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SourceDocument:
    evidence_id: str
    sha256: str
    original_filename: str
    stored_path: str
    size_bytes: int
    created_at: str
    extracted_vendor: str | None = None
    extracted_amount: str | None = None
    extracted_reference: str | None = None
    extracted_document_id: str | None = None
    extracted_work_order_id: str | None = None
    extracted_record_type: str | None = None
    approved_record_type: str | None = None
    approved_record_id: str | None = None
    latest_extraction_id: str | None = None


@dataclass(frozen=True)
class ExtractionRun:
    extraction_id: str
    evidence_id: str
    sequence: int
    created_at: str
    provider: str
    model: str | None
    schema_version: str
    prompt_version: str
    source_sha256: str
    output_hash: str
    parsed_output: dict[str, object]


def extraction_output_hash(extracted: dict[str, object]) -> str:
    canonical = json.dumps(extracted, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_extraction_run(
    doc: SourceDocument,
    extracted: dict[str, object],
    *,
    sequence: int,
    provider: str = "unknown",
    model: str | None = None,
    schema_version: str = "bookkeeping_proposal_v1",
    prompt_version: str = "unspecified",
) -> ExtractionRun:
    return ExtractionRun(
        extraction_id=f"EXT-{uuid.uuid4().hex[:16]}",
        evidence_id=doc.evidence_id,
        sequence=sequence,
        created_at=datetime.now(timezone.utc).isoformat(),
        provider=provider.strip() or "unknown",
        model=model.strip() if isinstance(model, str) and model.strip() else None,
        schema_version=schema_version.strip() or "bookkeeping_proposal_v1",
        prompt_version=prompt_version.strip() or "unspecified",
        source_sha256=doc.sha256,
        output_hash=extraction_output_hash(extracted),
        parsed_output=dict(extracted),
    )


class ProvenanceStore:
    """Legacy JSON compatibility store with append-only extraction history."""

    def __init__(self, metadata_path: str | Path, document_dir: str | Path):
        self.metadata_path = Path(metadata_path)
        self.document_dir = Path(document_dir)
        self.documents: dict[str, SourceDocument] = {}
        self.extractions: list[ExtractionRun] = []
        self._load()

    def _load(self) -> None:
        if not self.metadata_path.exists():
            return
        data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        for row in data.get("documents", []):
            doc = SourceDocument(**row)
            self.documents[doc.evidence_id] = doc
        self.extractions = [ExtractionRun(**row) for row in data.get("extractions", [])]

    def save(self) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "documents": [asdict(x) for x in self.documents.values()],
            "extractions": [asdict(x) for x in self.extractions],
        }
        self.metadata_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def sha256_file(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def by_sha256(self, sha256: str) -> SourceDocument | None:
        return next((d for d in self.documents.values() if d.sha256 == sha256), None)

    def capture(self, source_path: str | Path, original_filename: str) -> SourceDocument:
        source = Path(source_path)
        sha256 = self.sha256_file(source)
        existing = self.by_sha256(sha256)
        if existing:
            return existing

        evidence_id = f"DOC-{sha256[:16]}"
        suffix = Path(original_filename).suffix.lower()
        self.document_dir.mkdir(parents=True, exist_ok=True)
        stored = self.document_dir / f"{sha256}{suffix}"
        shutil.copy2(source, stored)

        doc = SourceDocument(
            evidence_id=evidence_id,
            sha256=sha256,
            original_filename=original_filename,
            stored_path=str(stored),
            size_bytes=source.stat().st_size,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.documents[evidence_id] = doc
        self.save()
        return doc

    def extractions_for(self, evidence_id: str) -> list[ExtractionRun]:
        return sorted(
            (run for run in self.extractions if run.evidence_id == evidence_id),
            key=lambda run: run.sequence,
        )

    def latest_extraction(self, evidence_id: str) -> ExtractionRun | None:
        rows = self.extractions_for(evidence_id)
        return rows[-1] if rows else None

    def add_extraction(
        self,
        evidence_id: str,
        extracted: dict[str, object],
        *,
        provider: str = "unknown",
        model: str | None = None,
        schema_version: str = "bookkeeping_proposal_v1",
        prompt_version: str = "unspecified",
    ) -> SourceDocument:
        doc = self.documents[evidence_id]
        run = build_extraction_run(
            doc,
            extracted,
            sequence=len(self.extractions_for(evidence_id)) + 1,
            provider=provider,
            model=model,
            schema_version=schema_version,
            prompt_version=prompt_version,
        )
        self.extractions.append(run)
        doc.extracted_vendor = str(extracted.get("vendor") or "") or None
        amount = extracted.get("amount")
        doc.extracted_amount = str(amount) if amount is not None else None
        doc.extracted_reference = str(extracted.get("reference") or "") or None
        doc.extracted_document_id = str(extracted.get("document_id") or "") or None
        doc.extracted_work_order_id = str(extracted.get("work_order_id") or "") or None
        doc.extracted_record_type = str(extracted.get("record_type") or "") or None
        doc.latest_extraction_id = run.extraction_id
        self.save()
        return doc

    def bind_record(self, evidence_id: str, record_type: str, record_id: str) -> SourceDocument:
        doc = self.documents[evidence_id]
        if doc.approved_record_id and (doc.approved_record_id != record_id or doc.approved_record_type != record_type):
            raise ValueError("Source document is already bound to another bookkeeping record")
        doc.approved_record_type = record_type
        doc.approved_record_id = record_id
        self.save()
        return doc

    def source_for_record(self, record_type: str, record_id: str) -> SourceDocument | None:
        return next(
            (
                d
                for d in self.documents.values()
                if d.approved_record_type == record_type and d.approved_record_id == record_id
            ),
            None,
        )
