from datetime import datetime, timedelta, timezone

import pytest

from app import WorkOrder
from connectors import ConnectorHub, INVOICES_WRITE, WORK_ORDERS_READ
from pull_scheduler import PullScheduleStore
from scheduled_sync import execute_due_pulls
from secure_web_app import create_secure_app


def test_pull_backoff_doubles_and_resets_after_success(tmp_path):
    store = PullScheduleStore(tmp_path / "bookkeeper.sqlite3")
    store.ensure("CONN-1", "Source", WORK_ORDERS_READ, interval_seconds=600, start_immediately=True)
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

    delay1 = store.mark_failure("CONN-1", WORK_ORDERS_READ, "temporary", now=now, jitter=False)
    first = store.get("CONN-1", WORK_ORDERS_READ)
    delay2 = store.mark_failure("CONN-1", WORK_ORDERS_READ, "temporary", now=now, jitter=False)
    second = store.get("CONN-1", WORK_ORDERS_READ)

    assert delay1 == 60
    assert delay2 == 120
    assert first.consecutive_failures == 1
    assert second.consecutive_failures == 2
    assert second.next_attempt_at == (now + timedelta(seconds=120)).isoformat()

    store.mark_success("CONN-1", WORK_ORDERS_READ, now=now)
    recovered = store.get("CONN-1", WORK_ORDERS_READ)
    assert recovered.consecutive_failures == 0
    assert recovered.last_error is None
    assert recovered.next_attempt_at == (now + timedelta(seconds=600)).isoformat()


def test_pull_schedule_persists_across_store_restart(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    first = PullScheduleStore(db)
    first.ensure("CONN-1", "Yardi", WORK_ORDERS_READ, interval_seconds=900, start_immediately=True)
    first.mark_failure("CONN-1", WORK_ORDERS_READ, "provider unavailable", jitter=False)

    second = PullScheduleStore(db)
    restored = second.get("CONN-1", WORK_ORDERS_READ)
    assert restored.consecutive_failures == 1
    assert restored.last_error == "provider unavailable"


def test_outbound_write_capability_cannot_be_scheduled(tmp_path):
    store = PullScheduleStore(tmp_path / "bookkeeper.sqlite3")
    with pytest.raises(ValueError, match="not eligible"):
        store.ensure("CONN-1", "Ledger", INVOICES_WRITE)


class ScheduledWorkOrderSource:
    name = "Scheduled Yardi"
    capabilities = frozenset({WORK_ORDERS_READ})

    def pull_work_orders(self):
        return [WorkOrder("WO-SCHED-1", "Unit 101", "Leak", status="open")]


def test_due_scheduled_pull_imports_and_records_success(tmp_path):
    hub = ConnectorHub()
    source = ScheduledWorkOrderSource()
    hub.register(source)
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)

    connector_id = "RUNTIME:ScheduledWorkOrderSource:Scheduled Yardi"
    app.config["PULL_SCHEDULES"].ensure(
        connector_id, source.name, WORK_ORDERS_READ,
        interval_seconds=600, start_immediately=True,
    )

    summary = execute_due_pulls(app)

    assert summary["success"] == 1
    assert "WO-SCHED-1" in app.config["BOOKKEEPER"].work_orders
    schedule = app.config["PULL_SCHEDULES"].get(connector_id, WORK_ORDERS_READ)
    assert schedule.consecutive_failures == 0
    runs = app.config["SYNC_RELIABILITY"].list_runs()
    assert any(run.direction == "scheduled_pull" and run.status == "success" for run in runs)


class FailingScheduledSource:
    name = "Broken Source"
    capabilities = frozenset({WORK_ORDERS_READ})

    def pull_work_orders(self):
        raise RuntimeError("temporary outage")


def test_failed_scheduled_pull_backs_off_without_mutating_books(tmp_path, monkeypatch):
    hub = ConnectorHub()
    source = FailingScheduledSource()
    hub.register(source)
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)
    connector_id = "RUNTIME:FailingScheduledSource:Broken Source"
    app.config["PULL_SCHEDULES"].ensure(
        connector_id, source.name, WORK_ORDERS_READ,
        interval_seconds=600, start_immediately=True,
    )
    before = dict(app.config["BOOKKEEPER"].work_orders)

    # Remove jitter to make the persisted retry interval deterministic.
    monkeypatch.setattr("pull_scheduler.random.uniform", lambda a, b: 1.0)
    summary = execute_due_pulls(app)

    assert summary["failed"] == 1
    assert app.config["BOOKKEEPER"].work_orders == before
    schedule = app.config["PULL_SCHEDULES"].get(connector_id, WORK_ORDERS_READ)
    assert schedule.consecutive_failures == 1
    assert schedule.last_error == "temporary outage"
