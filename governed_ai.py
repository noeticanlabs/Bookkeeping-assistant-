"""Governed scheduled AI jobs.

V0.1 deliberately stops at proposal authority. A scheduled model may observe an
explicit context snapshot and return text, but it cannot mutate bookkeeping state
or call connectors. Runs are durable and auditable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Any

from ai_credential_boundary import AICredentialDomain


ALLOWED_CONTEXT_SCOPES = frozenset({"summary", "exceptions", "invoices", "work_orders"})
ALLOWED_AUTHORITY = frozenset({"observe", "propose"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GovernedAIJob:
    job_id: str
    name: str
    prompt: str
    prompt_version: str
    provider: str
    model: str
    context_scopes: tuple[str, ...]
    authority: str
    interval_seconds: int
    enabled: bool
    next_run_at: str


class GovernedAIStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS governed_ai_jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                context_scopes TEXT NOT NULL,
                authority TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                next_run_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS governed_ai_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                context_sha256 TEXT NOT NULL,
                output_sha256 TEXT,
                output_text TEXT,
                error TEXT,
                authority TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES governed_ai_jobs(job_id)
            );
            """)

    def upsert_job(self, *, job_id: str, name: str, prompt: str, prompt_version: str,
                   provider: str, model: str, context_scopes: list[str] | tuple[str, ...],
                   authority: str = "propose", interval_seconds: int = 86400,
                   enabled: bool = True, start_immediately: bool = False) -> None:
        scopes = tuple(sorted(set(context_scopes)))
        unknown = set(scopes) - ALLOWED_CONTEXT_SCOPES
        if unknown:
            raise ValueError(f"Unsupported AI context scopes: {', '.join(sorted(unknown))}")
        if authority not in ALLOWED_AUTHORITY:
            raise ValueError("Scheduled AI authority is limited to observe/propose")
        if interval_seconds < 60:
            raise ValueError("AI job interval must be at least 60 seconds")
        if not job_id.strip() or not prompt.strip():
            raise ValueError("job_id and prompt are required")
        now = _utcnow()
        next_run = now if start_immediately else now + timedelta(seconds=interval_seconds)
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO governed_ai_jobs(job_id,name,prompt,prompt_version,prompt_sha256,provider,model,
              context_scopes,authority,interval_seconds,enabled,next_run_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
              name=excluded.name,prompt=excluded.prompt,prompt_version=excluded.prompt_version,
              prompt_sha256=excluded.prompt_sha256,provider=excluded.provider,model=excluded.model,
              context_scopes=excluded.context_scopes,authority=excluded.authority,
              interval_seconds=excluded.interval_seconds,enabled=excluded.enabled,updated_at=excluded.updated_at
            """, (job_id, name.strip(), prompt, prompt_version, _hash_text(prompt), provider, model,
                  json.dumps(scopes), authority, interval_seconds, int(enabled), _iso(next_run), _iso(now), _iso(now)))

    def due(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM governed_ai_jobs WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,job_id",
                                (_iso(_utcnow()),)).fetchall()

    def jobs(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM governed_ai_jobs ORDER BY name,job_id").fetchall()

    def recent_runs(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM governed_ai_runs ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()

    def start_run(self, job, context_sha256: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("""INSERT INTO governed_ai_runs(
              job_id,started_at,status,provider,model,prompt_version,prompt_sha256,context_sha256,authority)
              VALUES(?,?,?,?,?,?,?,?,?)""",
              (job["job_id"], _iso(_utcnow()), "running", job["provider"], job["model"],
               job["prompt_version"], job["prompt_sha256"], context_sha256, job["authority"]))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, output: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT job_id FROM governed_ai_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown AI run")
            conn.execute("UPDATE governed_ai_runs SET finished_at=?,status='proposed',output_sha256=?,output_text=? WHERE run_id=?",
                         (_iso(_utcnow()), _hash_text(output), output, run_id))
            job = conn.execute("SELECT interval_seconds FROM governed_ai_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            conn.execute("UPDATE governed_ai_jobs SET next_run_at=? WHERE job_id=?",
                         (_iso(_utcnow() + timedelta(seconds=int(job["interval_seconds"]))), row["job_id"]))

    def fail_run(self, run_id: int, error: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT job_id FROM governed_ai_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE governed_ai_runs SET finished_at=?,status='failed',error=? WHERE run_id=?",
                         (_iso(_utcnow()), str(error)[:2000], run_id))
            job = conn.execute("SELECT interval_seconds FROM governed_ai_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            retry = min(3600, max(60, int(job["interval_seconds"]) // 4))
            conn.execute("UPDATE governed_ai_jobs SET next_run_at=? WHERE job_id=?",
                         (_iso(_utcnow() + timedelta(seconds=retry)), row["job_id"]))


def build_context(book, scopes: set[str]) -> dict[str, Any]:
    """Build a bounded, serializable snapshot. No credentials or connector secrets."""
    result: dict[str, Any] = {}
    if "summary" in scopes:
        result["summary"] = {
            "work_orders": len(book.work_orders), "costs": len(book.costs),
            "vendor_bills": len(book.vendor_bills), "invoices": len(book.invoices),
            "payments": len(book.payments), "deposits": len(book.deposits),
        }
    if "work_orders" in scopes:
        result["work_orders"] = [vars(x) for x in book.work_orders.values()]
    if "invoices" in scopes:
        result["invoices"] = [vars(x) for x in book.invoices.values()]
    return result


def execute_due_ai_jobs(app, invoke: Callable[[str, str, str, dict[str, Any]], str] | None = None) -> dict[str, int]:
    """Execute due jobs as proposal-only workers. Never mutates BOOKKEEPER."""
    store: GovernedAIStore = app.config["GOVERNED_AI_STORE"]
    book = app.config["BOOKKEEPER"]
    invoke = invoke or app.config.get("GOVERNED_AI_INVOKE")
    summary = {"proposed": 0, "failed": 0}
    for job in store.due():
        scopes = set(json.loads(job["context_scopes"]))
        context = build_context(book, scopes)
        canonical = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
        run_id = store.start_run(job, _hash_text(canonical))
        try:
            if not callable(invoke):
                raise RuntimeError("No governed AI provider is configured")
            output = invoke(job["provider"], job["model"], job["prompt"], context)
            if not isinstance(output, str) or not output.strip():
                raise ValueError("AI provider returned no proposal text")
            store.finish_run(run_id, output.strip())
            audit = app.config.get("AUDIT_LOG")
            if audit is not None:
                audit.append("ai.proposal.created", f"AIJOB:{job['job_id']}",
                             {"run_id": run_id, "prompt_sha256": job["prompt_sha256"],
                              "context_sha256": _hash_text(canonical), "authority": job["authority"]},
                             actor="scheduled-ai")
            summary["proposed"] += 1
        except Exception as exc:
            store.fail_run(run_id, str(exc))
            summary["failed"] += 1
    return summary


def openai_invoke(provider: str, model: str, prompt: str, context: dict[str, Any], *,
                  credentials: AICredentialDomain | None = None) -> str:
    """Invoke OpenAI using only the explicit AI-provider credential domain."""
    if provider != "openai":
        raise ValueError(f"Unsupported governed AI provider: {provider}")
    if credentials is None:
        raise RuntimeError("An isolated AI credential domain is required")
    key = credentials.require("OPENAI_API_KEY")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the openai package to run governed OpenAI jobs") from exc
    client = OpenAI(api_key=key)
    response = client.responses.create(
        model=model,
        input=[{"role": "system", "content": "You are a proposal-only bookkeeping assistant. You have no authority to post, pay, issue, approve, or mutate records. Identify observations, candidate relationships, and exceptions only."},
               {"role": "user", "content": prompt + "\n\nAuthorized context:\n" + json.dumps(context, sort_keys=True, default=str)}],
    )
    return getattr(response, "output_text", "") or ""
