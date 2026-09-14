import io

from connectors import ConnectorHub
from extraction_history import ExtractionHistoryStore
from secure_web_app import create_secure_app


class VersionedDocuments:
    name = "Test Document AI"
    model = "test-model-v1"
    schema_version = "bookkeeping_document_v1"
    prompt_version = "test_prompt_v1"

    def __init__(self):
        self.calls = 0

    def extract(self, document_path):
        self.calls += 1
        if self.calls == 1:
            return {
                "vendor": "Ferguson",
                "amount": 100,
                "reference": "first interpretation",
                "document_id": "BILL-1",
                "work_order_id": "",
                "record_type": "vendor_bill",
            }
        return {
            "vendor": "Ferguson Enterprises",
            "amount": 105,
            "reference": "corrected interpretation",
            "document_id": "BILL-1",
            "work_order_id": "",
            "record_type": "vendor_bill",
        }


def setup_admin(client):
    return client.post("/setup", data={
        "display_name": "Admin User",
        "username": "admin",
        "password": "administrator-pass",
    })


def test_secure_document_reextraction_preserves_immutable_history(tmp_path):
    documents = VersionedDocuments()
    app = create_secure_app(
        str(tmp_path / "bookkeeper.json"),
        connectors=ConnectorHub(documents=documents),
    )
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)

    first_response = client.post(
        "/documents/extract",
        data={"file": (io.BytesIO(b"preserved invoice bytes"), "invoice.pdf")},
        content_type="multipart/form-data",
    )
    assert first_response.status_code == 200

    provenance = app.config["PROVENANCE"]
    evidence = next(iter(provenance.documents.values()))
    first_id = evidence.latest_extraction_id
    assert first_id is not None
    assert evidence.extracted_vendor == "Ferguson"
    assert evidence.extracted_amount == "100"

    second_response = client.post(f"/documents/{evidence.evidence_id}/reextract")
    assert second_response.status_code == 200
    assert documents.calls == 2

    current = provenance.documents[evidence.evidence_id]
    second_id = current.latest_extraction_id
    assert second_id is not None
    assert second_id != first_id
    assert current.extracted_vendor == "Ferguson Enterprises"
    assert current.extracted_amount == "105"

    history = app.config["EXTRACTION_HISTORY"].list_for(evidence.evidence_id)
    assert [run.sequence for run in history] == [1, 2]
    assert history[0].extraction_id == first_id
    assert history[1].extraction_id == second_id
    assert history[0].parsed_output["vendor"] == "Ferguson"
    assert history[0].parsed_output["amount"] == 100
    assert history[1].parsed_output["vendor"] == "Ferguson Enterprises"
    assert history[0].output_hash != history[1].output_hash
    assert history[0].source_sha256 == history[1].source_sha256 == evidence.sha256
    assert history[0].provider == "Test Document AI"
    assert history[0].model == "test-model-v1"
    assert history[0].schema_version == "bookkeeping_document_v1"
    assert history[0].prompt_version == "test_prompt_v1"

    restarted = ExtractionHistoryStore(app.config["BOOKKEEPER_DATA_PATH"])
    restarted_history = restarted.list_for(evidence.evidence_id)
    assert [run.extraction_id for run in restarted_history] == [first_id, second_id]
    assert restarted_history[0].parsed_output["vendor"] == "Ferguson"

    proposals = [
        event for event in app.config["AUDIT_LOG"].events()
        if event.event_type == "document.proposed" and event.evidence_id == evidence.evidence_id
    ]
    assert len(proposals) == 2
    assert [event.payload["extraction_id"] for event in proposals] == [first_id, second_id]
    assert proposals[0].payload["extraction_output_hash"] == history[0].output_hash


def test_duplicate_upload_does_not_create_second_extraction_run(tmp_path):
    documents = VersionedDocuments()
    app = create_secure_app(
        str(tmp_path / "bookkeeper.json"),
        connectors=ConnectorHub(documents=documents),
    )
    app.config.update(TESTING=True)
    client = app.test_client()
    setup_admin(client)

    payload = b"same original evidence"
    client.post(
        "/documents/extract",
        data={"file": (io.BytesIO(payload), "invoice.pdf")},
        content_type="multipart/form-data",
    )
    response = client.post(
        "/documents/extract",
        data={"file": (io.BytesIO(payload), "duplicate.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    evidence = next(iter(app.config["PROVENANCE"].documents.values()))
    history = app.config["EXTRACTION_HISTORY"].list_for(evidence.evidence_id)
    assert len(history) == 1
    assert documents.calls == 1
    assert b"use re-extraction on the preserved source" in response.data
