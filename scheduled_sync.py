"""Execution loop for scheduled safe connector pulls.

The loop never executes outbound write capabilities. Each due pull is isolated so
one provider failure does not prevent another provider from running.
"""

from __future__ import annotations

import os
import threading
from copy import deepcopy
from typing import Callable

from imports import import_deposits, import_payments, import_work_orders
from pull_scheduler import PullScheduleStore, SAFE_PULL_CAPABILITIES


CAPABILITY_IMPORTERS: dict[str, tuple[str, str, Callable]] = {
    "work_orders.read": ("pull_work_orders", "work orders", import_work_orders),
    "payments.read": ("pull_payments", "payments", import_payments),
    "deposits.read": ("pull_deposits", "deposits", import_deposits),
}


def _connector_name(connector) -> str:
    return getattr(connector, "name", connector.__class__.__name__)


def _connector_id(connector) -> str:
    return getattr(connector, "_managed_connection_id", None) or f"RUNTIME:{connector.__class__.__name__}:{_connector_name(connector)}"


def _restore_book(book, snapshot) -> None:
    book.vendor_bill_mode = snapshot.vendor_bill_mode
    book.work_orders = snapshot.work_orders
    book.costs = snapshot.costs
    book.vendor_bills = snapshot.vendor_bills
    book.invoices = snapshot.invoices
    book.payments = snapshot.payments
    book.deposits = snapshot.deposits


def discover_safe_pull_schedules(app, *, interval_seconds: int | None = None) -> list:
    hub = app.config["CONNECTOR_HUB"]
    schedules: PullScheduleStore = app.config["PULL_SCHEDULES"]
    interval = int(interval_seconds or app.config.get("AUTO_SYNC_INTERVAL_SECONDS") or 900)
    result = []
    for connector in hub.connectors:
        capabilities = set(getattr(connector, "capabilities", frozenset()))
        for capability in sorted(capabilities & SAFE_PULL_CAPABILITIES):
            result.append(schedules.ensure(
                _connector_id(connector), _connector_name(connector), capability,
                interval_seconds=interval,
            ))
    return result


def execute_due_pulls(app) -> dict[str, int]:
    """Run every due safe pull once and return summary counters."""
    hub = app.config["CONNECTOR_HUB"]
    book = app.config["BOOKKEEPER"]
    schedules: PullScheduleStore = app.config["PULL_SCHEDULES"]
    reliability = app.config["SYNC_RELIABILITY"]
    save = app.config["SAVE_BOOKKEEPER"]

    connectors = {_connector_id(c): c for c in hub.connectors}
    summary = {"success": 0, "failed": 0, "missing": 0}

    for schedule in schedules.due():
        if schedule.capability not in SAFE_PULL_CAPABILITIES:
            continue
        connector = connectors.get(schedule.connector_id)
        if connector is None:
            schedules.mark_failure(schedule.connector_id, schedule.capability, "Connector is not currently loaded")
            summary["missing"] += 1
            continue
        method_name, _, importer = CAPABILITY_IMPORTERS[schedule.capability]
        loader = getattr(connector, method_name, None)
        if not callable(loader):
            schedules.mark_failure(schedule.connector_id, schedule.capability, "Connector no longer provides required pull method")
            summary["missing"] += 1
            continue

        run_id = reliability.start_run(
            schedule.connector_id, schedule.connector_name, schedule.capability, "scheduled_pull"
        )
        snapshot = deepcopy(book)
        try:
            rows = loader()
            result = importer(book, rows)
            save()
            reliability.finish_run(
                run_id, added=result.added, skipped=result.skipped,
                detail={"errors": list(result.errors), "scheduler": True},
            )
            schedules.mark_success(schedule.connector_id, schedule.capability)
            summary["success"] += 1
            try:
                hub.emit(f"{schedule.capability}.scheduled", {
                    "source": schedule.connector_name,
                    "connector_id": schedule.connector_id,
                    "added": result.added,
                    "skipped": result.skipped,
                })
            except Exception:
                pass
        except Exception as exc:
            _restore_book(book, snapshot)
            try:
                reliability.fail_run(run_id, str(exc), detail={"scheduler": True})
            except ValueError:
                pass
            schedules.mark_failure(schedule.connector_id, schedule.capability, str(exc))
            summary["failed"] += 1

    return summary


def start_scheduler_thread(app) -> threading.Thread | None:
    """Start one lightweight daemon scheduler when explicitly enabled."""
    if os.environ.get("BOOKKEEPER_AUTO_SYNC", "0") != "1":
        return None
    poll_seconds = max(10, int(os.environ.get("BOOKKEEPER_SYNC_POLL_SECONDS", "30")))
    interval_seconds = max(60, int(os.environ.get("BOOKKEEPER_AUTO_SYNC_INTERVAL_SECONDS", "900")))
    app.config["AUTO_SYNC_INTERVAL_SECONDS"] = interval_seconds
    discover_safe_pull_schedules(app, interval_seconds=interval_seconds)

    def worker() -> None:
        import time
        while True:
            try:
                with app.app_context():
                    discover_safe_pull_schedules(app, interval_seconds=interval_seconds)
                    execute_due_pulls(app)
            except Exception:
                # The scheduler must not terminate the web application. Individual
                # connector errors are already persisted in sync history/schedules.
                pass
            time.sleep(poll_seconds)

    thread = threading.Thread(target=worker, name="bookkeeper-safe-pull-scheduler", daemon=True)
    thread.start()
    return thread
