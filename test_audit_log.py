import io

from audit_log import AuditLog
from connectors import ConnectorHub
from web_app import create_app


class FakeDocuments:
    def __init__(self, result):
        self.result = result

    def extract(self, document_path):
        return self.result


def test_audit_log_appends_without_overwriting(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    first = log.append("document.proposed", "DOC-1", {"vendor": "Ferguson"}, actor="reader")
    second = log.append("document.approved", "DOC-1", {"record_id": "COST-1"}, actor="Mikey")

    events = log.events()
    assert [event.event_id for event in events] == [first.event_id, second.event_id]
    assert events[0].payload["vendor"] == "Ferguson"
    assert events[1].actor == "Mikey"


def test_document_approval_records_human_corrections(tmp_path):
    documents = FakeDocuments({
        "vendor": "Ferguson",
        "amount": 742.16,
        "reference": "WO-1",
        "document_id": "F-99",
        "work_order_id": "WO-1",
        "record_type": "cost",
    })
    app = create_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub(documents=documents))
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post("/work-orders", data={
        "id": "WO-1", "customer": "Smith", "description": "Repair",
        "quoted_total": "1000", "status": "complete",
    })

    response = client.post(
        "/documents/extract",
        data={"file": (io.BytesIO(b"unique receipt bytes"), "receipt.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    evidence = next(iter(app.config["PROVENANCE"].documents.values()))
    client.post("/documents/approve", data={
        "evidence_id": evidence.evidence_id,
        "actor": "Mikey",
        "record_id": "COST-99",
        "vendor": "Ferguson Plumbing Supply",
        "amount": "750.00",
        "reference": "WO-1 F-99",
        "work_order_id": "WO-1",
        "record_type": "cost",
        "treatment": "ask",
        "linked_cost_id": "",
    })

    events = app.config["AUDIT_LOG"].for_evidence(evidence.evidence_id)
    assert [event.event_type for event in events] == ["document.proposed", "document.approved"]
    approval = events[-1]
    assert approval.actor == "Mikey"
    assert approval.payload["result"] == {"record_type": "cost", "record_id": "COST-99"}
    assert approval.payload["changes"]["vendor"]["proposed"] == "Ferguson"
    assert approval.payload["changes"]["vendor"]["approved"] == "Ferguson Plumbing Supply"
    assert approval.payload["changes"]["amount"]["proposed"] == "742.16"
    assert approval.payload["changes"]["amount"]["approved"] == "750.00"


def test_approval_without_changes_is_recorded_as_same_proposal(tmp_path):
    documents = FakeDocuments({
        "vendor": "Ferguson",
        "amount": 500,
        "reference": "",
        "document_id": "F-100",
        "work_order_id": "",
        "record_type": "vendor_bill",
    })
    app = create_app(str(tmp_path / "bookkeeper.json"), connectors=ConnectorHub(documents=documents))
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post(
        "/documents/extract",
        data={"file": (io.BytesIO(b"another unique bill"), "bill.pdf")},
        content_type="multipart/form-data",
    )
    evidence = next(iter(app.config["PROVENANCE"].documents.values()))

    client.post("/documents/approve", data={
        "evidence_id": evidence.evidence_id,
        "actor": "Bookkeeper",
        "record_id": "F-100",
        "vendor": "Ferguson",
        "amount": "500",
        "reference": "",
        "work_order_id": "",
        "record_type": "vendor_bill",
        "treatment": "ask",
        "linked_cost_id": "",
    })

    approval = app.config["AUDIT_LOG"].for_evidence(evidence.evidence_id)[-1]
    assert approval.payload["changes"] == {}
