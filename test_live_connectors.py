from decimal import Decimal

import live_connectors
from connectors import ConnectorHub, DEPOSITS_READ, PAYMENTS_READ, WORK_ORDERS_READ
from live_connectors import (
    HousecallProConnector,
    JobberConnector,
    QuickBooksOnlineConnector,
    YardiMaintenanceConnector,
)


def test_hub_composes_partial_system_capabilities():
    hub = ConnectorHub()
    yardi = YardiMaintenanceConnector(
        base_url="https://yardi.example", work_orders_path="maintenance",
        auth_header="Authorization", auth_value="secret",
        field_map={"id": "wo", "customer": "resident", "description": "problem", "status": "status"},
    )
    qbo = QuickBooksOnlineConnector("realm", "token")
    hub.register(yardi)
    hub.register(qbo)

    assert hub.work_order_sources() == [yardi]
    assert hub.payment_sources() == [qbo]
    assert hub.deposit_sources() == [qbo]
    assert WORK_ORDERS_READ in yardi.capabilities
    assert PAYMENTS_READ not in yardi.capabilities
    assert DEPOSITS_READ not in yardi.capabilities


def test_jobber_normalizes_jobs_as_work_orders(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append(kwargs["body"])
        return {
            "data": {
                "jobs": {
                    "nodes": [{
                        "id": "abc", "jobNumber": 22, "title": "Water heater",
                        "jobStatus": "completed", "client": {"name": "Smith"},
                    }],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    jobs = JobberConnector("token").pull_work_orders()
    assert len(jobs) == 1
    assert jobs[0].id == "JOBBER:abc"
    assert jobs[0].customer == "Smith"
    assert jobs[0].description == "Water heater"
    assert jobs[0].status == "complete"
    assert calls


def test_housecall_normalizes_jobs_as_work_orders(monkeypatch):
    def fake_request(url, **kwargs):
        return {
            "jobs": [{
                "id": "job_1", "number": "101", "work_status": "completed",
                "customer": {"first_name": "Jane", "last_name": "Doe"},
                "name": "Leak repair",
            }],
            "page": 1,
            "total_pages": 1,
        }

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    jobs = HousecallProConnector("key").pull_work_orders()
    assert jobs[0].id == "HCP:job_1"
    assert jobs[0].customer == "Jane Doe"
    assert jobs[0].description == "Leak repair"
    assert jobs[0].status == "complete"


def test_quickbooks_imports_financial_evidence_without_invoice_authority(monkeypatch):
    def fake_request(url, **kwargs):
        if "Payment" in url:
            return {"QueryResponse": {"Payment": [{"Id": "10", "TotalAmt": 125, "PaymentRefNum": "INV-7"}]}}
        return {"QueryResponse": {"Deposit": [{"Id": "20", "TotalAmt": 120, "PrivateNote": "PAY-10"}]}}

    monkeypatch.setattr(live_connectors, "_json_request", fake_request)
    qbo = QuickBooksOnlineConnector("realm", "token")
    payments = qbo.pull_payments()
    deposits = qbo.pull_deposits()
    assert payments[0].id == "QBO-PAY:10"
    assert payments[0].amount == Decimal("125")
    assert payments[0].invoice_id is None
    assert payments[0].reference == "INV-7"
    assert deposits[0].id == "QBO-DEP:20"
    assert deposits[0].amount == Decimal("120")
    assert deposits[0].payment_id is None
    assert deposits[0].reference == "PAY-10"


def test_yardi_json_mapping_is_work_order_only(monkeypatch):
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return b'{"items":[{"wo":"77","resident":"Unit 4","problem":"No heat","status":"open"}]}'

    monkeypatch.setattr(live_connectors.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    yardi = YardiMaintenanceConnector(
        base_url="https://yardi.example", work_orders_path="wo",
        auth_header="X-Key", auth_value="secret",
        field_map={"id": "wo", "customer": "resident", "description": "problem", "status": "status"},
        response_items_key="items",
    )
    rows = yardi.pull_work_orders()
    assert rows[0].id == "YARDI:77"
    assert rows[0].customer == "Unit 4"
    assert rows[0].description == "No heat"
    assert rows[0].quoted_total is None
