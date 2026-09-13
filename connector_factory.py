"""Environment-based connector setup for the runnable app."""

from __future__ import annotations

import json
import os

from connectors import ConnectorHub


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def default_connector_hub() -> ConnectorHub:
    hub = ConnectorHub()

    if os.environ.get("OPENAI_API_KEY"):
        from openai_document_connector import OpenAIDocumentConnector
        hub.register(OpenAIDocumentConnector())

    if os.environ.get("JOBBER_ACCESS_TOKEN"):
        from live_connectors import JobberConnector
        hub.register(JobberConnector(
            access_token=os.environ["JOBBER_ACCESS_TOKEN"],
            graphql_version=os.environ.get("JOBBER_GRAPHQL_VERSION", "2025-04-16"),
        ))

    if os.environ.get("HOUSECALL_PRO_API_KEY"):
        from live_connectors import HousecallProConnector
        hub.register(HousecallProConnector(api_key=os.environ["HOUSECALL_PRO_API_KEY"]))

    if os.environ.get("QBO_REALM_ID") and os.environ.get("QBO_ACCESS_TOKEN"):
        from live_connectors import QuickBooksOnlineConnector
        hub.register(QuickBooksOnlineConnector(
            realm_id=os.environ["QBO_REALM_ID"],
            access_token=os.environ["QBO_ACCESS_TOKEN"],
            sandbox=_truthy("QBO_SANDBOX"),
        ))

    yardi_required = (
        os.environ.get("YARDI_BASE_URL"),
        os.environ.get("YARDI_WORK_ORDERS_PATH"),
        os.environ.get("YARDI_AUTH_VALUE"),
    )
    if all(yardi_required):
        from live_connectors import YardiMaintenanceConnector
        raw_map = os.environ.get("YARDI_FIELD_MAP", "{}")
        try:
            field_map = json.loads(raw_map)
        except json.JSONDecodeError as exc:
            raise RuntimeError("YARDI_FIELD_MAP must be valid JSON") from exc
        if not isinstance(field_map, dict):
            raise RuntimeError("YARDI_FIELD_MAP must be a JSON object")
        hub.register(YardiMaintenanceConnector(
            base_url=os.environ["YARDI_BASE_URL"],
            work_orders_path=os.environ["YARDI_WORK_ORDERS_PATH"],
            auth_header=os.environ.get("YARDI_AUTH_HEADER", "Authorization"),
            auth_value=os.environ["YARDI_AUTH_VALUE"],
            field_map={str(k): str(v) for k, v in field_map.items()},
            response_items_key=os.environ.get("YARDI_ITEMS_KEY", "work_orders"),
            response_format=os.environ.get("YARDI_RESPONSE_FORMAT", "json"),
            xml_item_tag=os.environ.get("YARDI_XML_ITEM_TAG", "WorkOrder"),
        ))

    return hub
