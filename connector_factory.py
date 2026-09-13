"""Environment-based connector setup for the runnable app."""

import os

from connectors import ConnectorHub


def default_connector_hub() -> ConnectorHub:
    hub = ConnectorHub()
    if os.environ.get("OPENAI_API_KEY"):
        from openai_document_connector import OpenAIDocumentConnector

        hub.documents = OpenAIDocumentConnector()
    return hub
