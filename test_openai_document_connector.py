import json
from types import SimpleNamespace

import pytest

from openai_document_connector import OpenAIDocumentConnector


class FakeFiles:
    def __init__(self):
        self.created = []
        self.deleted = []

    def create(self, file, purpose):
        self.created.append((file.name, purpose))
        return SimpleNamespace(id="file-test-1")

    def delete(self, file_id):
        self.deleted.append(file_id)


class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output)


class FakeClient:
    def __init__(self, output):
        self.files = FakeFiles()
        self.responses = FakeResponses(output)


def test_openai_connector_uploads_extracts_structured_data_and_deletes_file(tmp_path):
    document = tmp_path / "receipt.pdf"
    document.write_bytes(b"fake pdf bytes")
    output = json.dumps({
        "vendor": "Ferguson",
        "amount": 742.16,
        "reference": "WO-1842",
        "document_id": "F-99",
        "work_order_id": "WO-1842",
        "record_type": "vendor_bill",
    })
    client = FakeClient(output)
    connector = OpenAIDocumentConnector(model="test-model", client=client)

    result = connector.extract(str(document))

    assert result["vendor"] == "Ferguson"
    assert result["amount"] == 742.16
    assert client.files.created[0][1] == "user_data"
    assert client.files.deleted == ["file-test-1"]
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["input"][0]["content"][1] == {"type": "input_file", "file_id": "file-test-1"}
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True


def test_openai_connector_rejects_empty_file(tmp_path):
    document = tmp_path / "receipt.pdf"
    document.write_bytes(b"")
    connector = OpenAIDocumentConnector(client=FakeClient("{}"))
    with pytest.raises(ValueError, match="empty"):
        connector.extract(str(document))


def test_openai_connector_fails_closed_on_malformed_output_and_cleans_up(tmp_path):
    document = tmp_path / "receipt.jpg"
    document.write_bytes(b"image bytes")
    client = FakeClient("not-json")
    connector = OpenAIDocumentConnector(client=client)
    with pytest.raises(ValueError, match="malformed"):
        connector.extract(str(document))
    assert client.files.deleted == ["file-test-1"]
