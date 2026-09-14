"""Immutable interpretation history for source documents.

The original source document remains immutable evidence. Each extraction or
re-extraction is appended as its own ExtractionRun. The SourceDocument keeps a
materialized latest-extraction view for the existing review UI, but history is
never overwritten.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from provenance import ExtractionRun, build_extraction_run
from sqlite_store import _connect, _json


class ExtractionHistoryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _initialize(self) -> None:
        with _connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS extraction_runs (
                    extraction_id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    schema_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    data TEXT NOT NULL,
                    UNIQUE(evidence_id, sequence),
                    FOREIGN KEY(evidence_id) REFERENCES source_documents(evidence_id)
                        ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS idx_extraction_runs_evidence
                    ON extraction_runs(evidence_id, sequence);
                """
            )

    @staticmethod
    def _row_to_run(row) -> ExtractionRun:
        return ExtractionRun(**json.loads(row["data"]))

    def list_for(self, evidence_id: str) -> list[ExtractionRun]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT data FROM extraction_runs WHERE evidence_id=? ORDER BY sequence",
                (evidence_id,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def latest_for(self, evidence_id: str) -> ExtractionRun | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT data FROM extraction_runs WHERE evidence_id=? ORDER BY sequence DESC LIMIT 1",
                (evidence_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def record(
        self,
        provenance,
        evidence_id: str,
        extracted: dict[str, object],
        *,
        provider: str,
        model: str | None,
        schema_version: str,
        prompt_version: str,
    ) -> ExtractionRun:
        """Append one immutable interpretation and update the latest view atomically."""
        if evidence_id not in provenance.documents:
            raise ValueError("Unknown source document")
        doc = provenance.documents[evidence_id]

        with _connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS n FROM extraction_runs WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
            run = build_extraction_run(
                doc,
                extracted,
                sequence=int(row["n"]) + 1,
                provider=provider,
                model=model,
                schema_version=schema_version,
                prompt_version=prompt_version,
            )
            updated = replace(
                doc,
                extracted_vendor=str(extracted.get("vendor") or "") or None,
                extracted_amount=(str(extracted.get("amount")) if extracted.get("amount") is not None else None),
                extracted_reference=str(extracted.get("reference") or "") or None,
                extracted_document_id=str(extracted.get("document_id") or "") or None,
                extracted_work_order_id=str(extracted.get("work_order_id") or "") or None,
                extracted_record_type=str(extracted.get("record_type") or "") or None,
                latest_extraction_id=run.extraction_id,
            )
            conn.execute(
                """INSERT INTO extraction_runs(
                    extraction_id,evidence_id,sequence,created_at,provider,model,
                    schema_version,prompt_version,source_sha256,output_hash,data
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run.extraction_id, run.evidence_id, run.sequence, run.created_at,
                    run.provider, run.model, run.schema_version, run.prompt_version,
                    run.source_sha256, run.output_hash, _json(asdict(run)),
                ),
            )
            conn.execute(
                "INSERT INTO source_documents(evidence_id,sha256,data) VALUES(?,?,?) "
                "ON CONFLICT(evidence_id) DO UPDATE SET sha256=excluded.sha256,data=excluded.data",
                (updated.evidence_id, updated.sha256, _json(asdict(updated))),
            )

        provenance.documents[evidence_id] = updated
        return run
