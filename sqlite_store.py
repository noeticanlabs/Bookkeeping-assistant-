"""SQLite persistence for Bookkeeper Assistant.

SQLite is authoritative for application state. Legacy JSON/JSONL stores remain importable.
Original source documents stay as files in the evidence vault; their metadata lives here.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from app import BankDeposit, Bookkeeper, Cost, Invoice, Payment, VendorBill, WorkOrder
from audit_log import AuditEvent
from company_config import CompanyProfile
from corrections import CostCorrection
from provenance import SourceDocument

SCHEMA_VERSION = 1


def _connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize(path: str | Path) -> None:
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_orders (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS costs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vendor_bills (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deposits (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_profile (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_documents (
                evidence_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS corrections (
                correction_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                original_cost_id TEXT NOT NULL,
                replacement_cost_id TEXT NOT NULL,
                data TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


def _json(value: object) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def save_bookkeeper(book: Bookkeeper, path: str | Path) -> None:
    initialize(path)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('vendor_bill_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (book.vendor_bill_mode,),
        )
        groups = (
            ("work_orders", book.work_orders),
            ("costs", book.costs),
            ("vendor_bills", book.vendor_bills),
            ("invoices", book.invoices),
            ("payments", book.payments),
            ("deposits", book.deposits),
        )
        for table, items in groups:
            conn.execute(f"DELETE FROM {table}")
            conn.executemany(
                f"INSERT INTO {table}(id,data) VALUES(?,?)",
                [(item_id, _json(asdict(item))) for item_id, item in items.items()],
            )


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    return [json.loads(row["data"]) for row in conn.execute(f"SELECT data FROM {table} ORDER BY id")]


def load_bookkeeper(path: str | Path) -> Bookkeeper:
    initialize(path)
    with _connect(path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='vendor_bill_mode'").fetchone()
        book = Bookkeeper(vendor_bill_mode=row["value"] if row else "ask")
        for data in _rows(conn, "work_orders"):
            data["quoted_total"] = Decimal(str(data["quoted_total"])) if data.get("quoted_total") is not None else None
            book.add_work_order(WorkOrder(**data))
        for data in _rows(conn, "costs"):
            data["amount"] = Decimal(str(data["amount"]))
            book.add_cost(Cost(**data))
        for data in _rows(conn, "vendor_bills"):
            data["amount"] = Decimal(str(data["amount"]))
            data["amount_paid"] = Decimal(str(data.get("amount_paid", "0")))
            book.add_vendor_bill(VendorBill(**data))
        for data in _rows(conn, "invoices"):
            data["total"] = Decimal(str(data["total"]))
            data["amount_paid"] = Decimal(str(data.get("amount_paid", "0")))
            book.add_invoice(Invoice(**data))
        for data in _rows(conn, "payments"):
            data["amount"] = Decimal(str(data["amount"]))
            payment = Payment(**data)
            book.payments[payment.id] = payment
        for data in _rows(conn, "deposits"):
            data["amount"] = Decimal(str(data["amount"]))
            data["processor_fee"] = Decimal(str(data.get("processor_fee", "0")))
            book.add_deposit(BankDeposit(**data))
        return book


class SQLiteCompanyConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        initialize(self.path)
        self.profile = self._load()

    def _load(self) -> CompanyProfile:
        with _connect(self.path) as conn:
            row = conn.execute("SELECT data FROM company_profile WHERE singleton=1").fetchone()
        if not row:
            return CompanyProfile()
        data = json.loads(row["data"])
        threshold = data.get("approval_threshold")
        data["approval_threshold"] = Decimal(str(threshold)) if threshold not in (None, "") else None
        data["approver_roles"] = tuple(data.get("approver_roles") or ("Owner", "Bookkeeper"))
        profile = CompanyProfile(**data)
        profile.validate()
        return profile

    def save(self) -> None:
        self.profile.validate()
        data = asdict(self.profile)
        data["approval_threshold"] = str(self.profile.approval_threshold) if self.profile.approval_threshold is not None else None
        data["approver_roles"] = list(self.profile.approver_roles)
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT INTO company_profile(singleton,data) VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET data=excluded.data",
                (_json(data),),
            )

    def update_from_strings(self, **kwargs) -> CompanyProfile:
        from company_config import CompanyConfigStore
        # Reuse the validated parser without persisting its temporary result.
        threshold = kwargs["approval_threshold"].strip()
        roles = tuple(x.strip() for x in kwargs["approver_roles"].split(",") if x.strip())
        profile = CompanyProfile(
            name=kwargs["name"].strip(),
            job_label=kwargs["job_label"].strip(),
            customer_label=kwargs["customer_label"].strip(),
            vendor_bill_mode=kwargs["vendor_bill_mode"].strip(),
            approval_threshold=Decimal(threshold) if threshold else None,
            approver_roles=roles,
            field_service_system=kwargs["field_service_system"].strip() or "none",
            accounting_system=kwargs["accounting_system"].strip() or "none",
            bank_system=kwargs["bank_system"].strip() or "none",
            document_system=kwargs["document_system"].strip() or "none",
        )
        profile.validate()
        self.profile = profile
        self.save()
        return profile


class SQLiteAuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        initialize(self.path)

    def append(self, event_type: str, evidence_id: str, payload: dict[str, object], actor: str = "user") -> AuditEvent:
        import uuid
        from datetime import datetime, timezone
        event = AuditEvent(
            event_id=f"AUD-{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            created_at=datetime.now(timezone.utc).isoformat(),
            evidence_id=evidence_id,
            actor=actor,
            payload=payload,
        )
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT INTO audit_events(event_id,event_type,evidence_id,actor,created_at,data) VALUES(?,?,?,?,?,?)",
                (event.event_id, event.event_type, event.evidence_id, event.actor, event.created_at, _json(event.payload)),
            )
        return event

    def events(self) -> list[AuditEvent]:
        with _connect(self.path) as conn:
            rows = conn.execute("SELECT * FROM audit_events ORDER BY seq").fetchall()
        return [AuditEvent(row["event_id"], row["event_type"], row["created_at"], row["evidence_id"], row["actor"], json.loads(row["data"])) for row in rows]

    def for_evidence(self, evidence_id: str) -> list[AuditEvent]:
        return [event for event in self.events() if event.evidence_id == evidence_id]


class SQLiteProvenanceStore:
    def __init__(self, path: str | Path, document_dir: str | Path):
        self.path = Path(path)
        self.document_dir = Path(document_dir)
        initialize(self.path)
        self.documents = self._load()

    def _load(self) -> dict[str, SourceDocument]:
        with _connect(self.path) as conn:
            rows = conn.execute("SELECT data FROM source_documents ORDER BY evidence_id").fetchall()
        return {doc.evidence_id: doc for doc in (SourceDocument(**json.loads(row["data"])) for row in rows)}

    def _save_doc(self, doc: SourceDocument) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT INTO source_documents(evidence_id,sha256,data) VALUES(?,?,?) "
                "ON CONFLICT(evidence_id) DO UPDATE SET sha256=excluded.sha256,data=excluded.data",
                (doc.evidence_id, doc.sha256, _json(asdict(doc))),
            )
        self.documents[doc.evidence_id] = doc

    @staticmethod
    def sha256_file(path: str | Path) -> str:
        from provenance import ProvenanceStore
        return ProvenanceStore.sha256_file(path)

    def by_sha256(self, sha256: str) -> SourceDocument | None:
        return next((d for d in self.documents.values() if d.sha256 == sha256), None)

    def capture(self, source_path: str | Path, original_filename: str) -> SourceDocument:
        from datetime import datetime, timezone
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
        doc = SourceDocument(evidence_id, sha256, original_filename, str(stored), source.stat().st_size, datetime.now(timezone.utc).isoformat())
        self._save_doc(doc)
        return doc

    def add_extraction(self, evidence_id: str, extracted: dict[str, object]) -> SourceDocument:
        doc = self.documents[evidence_id]
        doc.extracted_vendor = str(extracted.get("vendor") or "") or None
        doc.extracted_amount = str(extracted.get("amount")) if extracted.get("amount") is not None else None
        doc.extracted_reference = str(extracted.get("reference") or "") or None
        doc.extracted_document_id = str(extracted.get("document_id") or "") or None
        doc.extracted_work_order_id = str(extracted.get("work_order_id") or "") or None
        doc.extracted_record_type = str(extracted.get("record_type") or "") or None
        self._save_doc(doc)
        return doc

    def bind_record(self, evidence_id: str, record_type: str, record_id: str) -> SourceDocument:
        doc = self.documents[evidence_id]
        if doc.approved_record_id and (doc.approved_record_id != record_id or doc.approved_record_type != record_type):
            raise ValueError("Source document is already bound to another bookkeeping record")
        doc.approved_record_type = record_type
        doc.approved_record_id = record_id
        self._save_doc(doc)
        return doc

    def source_for_record(self, record_type: str, record_id: str) -> SourceDocument | None:
        return next((d for d in self.documents.values() if d.approved_record_type == record_type and d.approved_record_id == record_id), None)


class SQLiteCorrectionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        initialize(self.path)
        self.corrections = self._load()

    def _load(self) -> dict[str, CostCorrection]:
        with _connect(self.path) as conn:
            rows = conn.execute("SELECT data FROM corrections ORDER BY correction_id").fetchall()
        return {c.correction_id: c for c in (CostCorrection(**json.loads(row["data"])) for row in rows)}

    def save(self) -> None:
        with _connect(self.path) as conn:
            conn.execute("DELETE FROM corrections")
            conn.executemany(
                "INSERT INTO corrections(correction_id,status,original_cost_id,replacement_cost_id,data) VALUES(?,?,?,?,?)",
                [(c.correction_id, c.status, c.original_cost_id, c.replacement_cost_id, _json(asdict(c))) for c in self.corrections.values()],
            )

    def propose(self, *args, **kwargs):
        from corrections import CorrectionStore
        # Use the proven domain implementation against this in-memory collection.
        temp = CorrectionStore.__new__(CorrectionStore)
        temp.path = Path(".") / "unused"
        temp.corrections = self.corrections
        temp.save = self.save
        return CorrectionStore.propose(temp, *args, **kwargs)

    def approve(self, *args, **kwargs):
        from corrections import CorrectionStore
        temp = CorrectionStore.__new__(CorrectionStore)
        temp.path = Path(".") / "unused"
        temp.corrections = self.corrections
        temp.save = self.save
        return CorrectionStore.approve(temp, *args, **kwargs)

    def reject(self, *args, **kwargs):
        from corrections import CorrectionStore
        temp = CorrectionStore.__new__(CorrectionStore)
        temp.path = Path(".") / "unused"
        temp.corrections = self.corrections
        temp.save = self.save
        return CorrectionStore.reject(temp, *args, **kwargs)


def migrate_legacy(db_path: str | Path, legacy_data_path: str | Path) -> bool:
    """One-time import of existing JSON/JSONL state when SQLite is empty."""
    db_path = Path(db_path)
    legacy = Path(legacy_data_path)
    initialize(db_path)
    with _connect(db_path) as conn:
        already = conn.execute("SELECT value FROM meta WHERE key='legacy_migrated'").fetchone()
        existing = conn.execute("SELECT COUNT(*) AS n FROM work_orders").fetchone()["n"]
    if already or existing:
        return False

    if legacy.exists():
        from storage import load_bookkeeper as load_legacy_bookkeeper
        save_bookkeeper(load_legacy_bookkeeper(legacy), db_path)

    company_path = legacy.with_name(f"{legacy.stem}-company.json")
    if company_path.exists():
        from company_config import CompanyConfigStore
        old = CompanyConfigStore(company_path)
        new = SQLiteCompanyConfigStore(db_path)
        new.profile = old.profile
        new.save()

    provenance_path = legacy.with_name(f"{legacy.stem}-provenance.json")
    document_dir = legacy.parent / f"{legacy.stem}-documents"
    if provenance_path.exists():
        from provenance import ProvenanceStore
        old = ProvenanceStore(provenance_path, document_dir)
        new = SQLiteProvenanceStore(db_path, document_dir)
        for doc in old.documents.values():
            new._save_doc(doc)

    audit_path = legacy.with_name(f"{legacy.stem}-audit.jsonl")
    if audit_path.exists():
        from audit_log import AuditLog
        old = AuditLog(audit_path)
        new = SQLiteAuditLog(db_path)
        with _connect(db_path) as conn:
            for event in old.events():
                conn.execute(
                    "INSERT OR IGNORE INTO audit_events(event_id,event_type,evidence_id,actor,created_at,data) VALUES(?,?,?,?,?,?)",
                    (event.event_id, event.event_type, event.evidence_id, event.actor, event.created_at, _json(event.payload)),
                )

    corrections_path = legacy.with_name(f"{legacy.stem}-corrections.json")
    if corrections_path.exists():
        from corrections import CorrectionStore
        old = CorrectionStore(corrections_path)
        new = SQLiteCorrectionStore(db_path)
        new.corrections = old.corrections
        new.save()

    with _connect(db_path) as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES('legacy_migrated','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    return True
