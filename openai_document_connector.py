"""OpenAI-backed receipt/vendor-invoice extraction.

This adapter interprets documents only. It never writes bookkeeping state.
"""

import json
import os
from pathlib import Path
from typing import Any

from connectors import DOCUMENTS_EXTRACT


EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "amount": {"type": ["number", "null"]},
        "reference": {"type": "string"},
        "document_id": {"type": "string"},
        "work_order_id": {"type": "string"},
        "record_type": {"type": "string", "enum": ["vendor_bill", "cost"]},
    },
    "required": ["vendor", "amount", "reference", "document_id", "work_order_id", "record_type"],
    "additionalProperties": False,
}

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


class OpenAIDocumentConnector:
    """Extract a minimal bookkeeping proposal from an uploaded document."""

    name = "OpenAI Documents"
    capabilities = frozenset({DOCUMENTS_EXTRACT})

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
        max_bytes: int = 20 * 1024 * 1024,
    ):
        self.model = model or os.environ.get("BOOKKEEPER_DOCUMENT_MODEL", "gpt-5.6-luna")
        self.max_bytes = max_bytes
        if client is not None:
            self.client = client
            return

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI document extraction")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the openai package to use OpenAI document extraction") from exc
        self.client = OpenAI(api_key=key)

    def extract(self, document_path: str) -> dict[str, object]:
        path = Path(document_path)
        if not path.is_file():
            raise ValueError("Document file does not exist")
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError("Supported document types: PDF, PNG, JPG, JPEG, WEBP")
        size = path.stat().st_size
        if size == 0:
            raise ValueError("Document file is empty")
        if size > self.max_bytes:
            raise ValueError("Document file is too large")

        uploaded = None
        try:
            with path.open("rb") as handle:
                uploaded = self.client.files.create(file=handle, purpose="user_data")

            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Read this bookkeeping source document. Extract only the fields in the schema. "
                                    "Do not infer a work order unless an identifier is actually present. "
                                    "Use vendor_bill for an invoice/bill requesting payment; use cost for a receipt or already-paid purchase."
                                ),
                            },
                            {"type": "input_file", "file_id": uploaded.id},
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "bookkeeping_document",
                        "strict": True,
                        "schema": EXTRACTION_SCHEMA,
                    }
                },
            )
            text = getattr(response, "output_text", None)
            if not text:
                raise ValueError("Document extraction returned no structured output")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("Document extraction returned malformed structured output") from exc
            if not isinstance(data, dict):
                raise ValueError("Document extraction returned invalid structured output")
            return data
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("Document extraction service failed") from exc
        finally:
            if uploaded is not None:
                try:
                    self.client.files.delete(uploaded.id)
                except Exception:
                    pass
