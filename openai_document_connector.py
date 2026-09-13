"""OpenAI-backed receipt/vendor-invoice extraction.

This adapter interprets documents only. It never writes bookkeeping state.
"""

import json
import os
from pathlib import Path
from typing import Any


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


class OpenAIDocumentConnector:
    """Extract a minimal bookkeeping proposal from an uploaded document."""

    def __init__(self, api_key: str | None = None, model: str | None = None, client: Any = None):
        self.model = model or os.environ.get("BOOKKEEPER_DOCUMENT_MODEL", "gpt-5.6-luna")
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
        if path.stat().st_size == 0:
            raise ValueError("Document file is empty")

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
                                    "Read this receipt or vendor invoice for bookkeeping review. "
                                    "Extract only what is supported by the document. Do not invent missing values. "
                                    "vendor is the merchant/vendor name. amount is the final total due/paid. "
                                    "reference should include useful PO, job, work-order, customer, or invoice references. "
                                    "document_id is the receipt/invoice number when present. work_order_id is an explicit "
                                    "work-order/job ID only when actually shown. record_type should be vendor_bill when "
                                    "the document represents an amount owed to a vendor, otherwise cost for a paid receipt."
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

            raw = response.output_text
            if not raw:
                raise ValueError("Document extractor returned no result")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Document extractor returned an invalid result")
            return data
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ValueError("Document extractor returned malformed structured data") from exc
        finally:
            if uploaded is not None:
                try:
                    self.client.files.delete(uploaded.id)
                except Exception:
                    # Cleanup failure must never turn a successful extraction into bookkeeping failure.
                    pass
