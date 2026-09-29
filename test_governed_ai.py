import json
import sqlite3

import pytest

from governed_ai import GovernedAIStore, execute_due_ai_jobs
from secure_web_app import create_secure_app


def test_job_rejects_execution_authority(tmp_path):
    store = GovernedAIStore(tmp_path / "bookkeeper.sqlite3")
    with pytest.raises(ValueError, match="observe/propose"):
        store.upsert_job(job_id="danger", name="Danger", prompt="Pay bills", prompt_version="v1",
                         provider="openai", model="test", context_scopes=["summary"],
                         authority="execute", interval_seconds=60)


def test_job_rejects_unknown_context_scope(tmp_path):
    store = GovernedAIStore(tmp_path / "bookkeeper.sqlite3")
    with pytest.raises(ValueError, match="Unsupported AI context"):
        store.upsert_job(job_id="secrets", name="Secrets", prompt="Read everything", prompt_version="v1",
                         provider="openai", model="test", context_scopes=["credentials"], interval_seconds=60)


def test_due_ai_job_creates_proposal_without_mutating_books(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    store = app.config["GOVERNED_AI_STORE"]
    before = json.dumps({
        "work_orders": list(app.config["BOOKKEEPER"].work_orders),
        "invoices": list(app.config["BOOKKEEPER"].invoices),
        "payments": list(app.config["BOOKKEEPER"].payments),
    }, sort_keys=True)
    store.upsert_job(job_id="daily-review", name="Daily review", prompt="Find bookkeeping exceptions.",
                     prompt_version="v1", provider="openai", model="test-model",
                     context_scopes=["summary"], authority="propose", interval_seconds=60,
                     start_immediately=True)

    seen = {}
    def fake_invoke(provider, model, prompt, context):
        seen.update(provider=provider, model=model, prompt=prompt, context=context)
        return "Proposal: review unmatched evidence."

    summary = execute_due_ai_jobs(app, invoke=fake_invoke)
    assert summary == {"proposed": 1, "failed": 0}
    assert seen["context"]["summary"]["payments"] == 0
    after = json.dumps({
        "work_orders": list(app.config["BOOKKEEPER"].work_orders),
        "invoices": list(app.config["BOOKKEEPER"].invoices),
        "payments": list(app.config["BOOKKEEPER"].payments),
    }, sort_keys=True)
    assert before == after
    run = store.recent_runs(1)[0]
    assert run["status"] == "proposed"
    assert run["authority"] == "propose"
    assert len(run["prompt_sha256"]) == 64
    assert len(run["context_sha256"]) == 64
    assert len(run["output_sha256"]) == 64


def test_provider_failure_is_durable_and_does_not_mutate_books(tmp_path):
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    app.config.update(TESTING=True)
    store = app.config["GOVERNED_AI_STORE"]
    store.upsert_job(job_id="fail", name="Fail", prompt="Review", prompt_version="v1",
                     provider="openai", model="test", context_scopes=["summary"],
                     interval_seconds=60, start_immediately=True)
    def fail(*args):
        raise RuntimeError("provider unavailable")
    summary = execute_due_ai_jobs(app, invoke=fail)
    assert summary == {"proposed": 0, "failed": 1}
    run = store.recent_runs(1)[0]
    assert run["status"] == "failed"
    assert "provider unavailable" in run["error"]


def test_governed_ai_tables_persist_across_restart(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    first = GovernedAIStore(db)
    first.upsert_job(job_id="persist", name="Persist", prompt="Review", prompt_version="v1",
                     provider="openai", model="test", context_scopes=["summary"], interval_seconds=600)
    second = GovernedAIStore(db)
    assert second.jobs()[0]["job_id"] == "persist"
