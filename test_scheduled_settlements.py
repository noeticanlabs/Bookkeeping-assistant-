from decimal import Decimal

from connectors import ConnectorHub, SETTLEMENTS_READ
from scheduled_sync import execute_due_pulls
from secure_web_app import create_secure_app
from settlements import SettlementEvidence


class ScheduledSettlementSource:
    name = "Stripe Test"
    capabilities = frozenset({SETTLEMENTS_READ})

    def pull_settlements(self):
        return [SettlementEvidence("STRIPE-SET:po_sched", "Stripe", Decimal("97"), "po_sched")]


def test_due_scheduled_settlement_pull_persists_processor_evidence(tmp_path):
    hub = ConnectorHub()
    source = ScheduledSettlementSource()
    hub.register(source)
    app = create_secure_app(str(tmp_path / "bookkeeper.json"), connectors=hub)
    app.config.update(TESTING=True)

    connector_id = "RUNTIME:ScheduledSettlementSource:Stripe Test"
    app.config["PULL_SCHEDULES"].ensure(
        connector_id, source.name, SETTLEMENTS_READ,
        interval_seconds=600, start_immediately=True,
    )

    summary = execute_due_pulls(app)

    assert summary["success"] == 1
    settlement = app.config["SETTLEMENT_STORE"].get("STRIPE-SET:po_sched")
    assert settlement.reported_net == Decimal("97")
    assert settlement.deposit_id is None
    assert app.config["SETTLEMENT_STORE"].reconcile(settlement.settlement_id, app.config["BOOKKEEPER"]).status == "open"

    schedule = app.config["PULL_SCHEDULES"].get(connector_id, SETTLEMENTS_READ)
    assert schedule.consecutive_failures == 0
    assert schedule.last_success_at is not None

    runs = app.config["SYNC_RELIABILITY"].list_runs()
    assert any(run.capability == SETTLEMENTS_READ and run.status == "succeeded" for run in runs)
