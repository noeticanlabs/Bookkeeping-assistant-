"""Environment-based connector setup for the runnable app."""

from __future__ import annotations

import json
import os

from connectors import ConnectorHub


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _json_object_env(name: str) -> dict[str, str]:
    raw = os.environ.get(name, "{}").strip() or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a JSON object")
    return {str(k): str(v) for k, v in value.items()}


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

    if os.environ.get("XERO_TENANT_ID") and os.environ.get("XERO_ACCESS_TOKEN"):
        from live_connectors import XeroConnector
        hub.register(XeroConnector(
            tenant_id=os.environ["XERO_TENANT_ID"],
            access_token=os.environ["XERO_ACCESS_TOKEN"],
            contact_ids=_json_object_env("XERO_CONTACT_IDS"),
            revenue_account_code=os.environ.get("XERO_REVENUE_ACCOUNT_CODE") or None,
        ))

    if os.environ.get("STRIPE_SECRET_KEY"):
        from live_connectors import StripeConnector
        hub.register(StripeConnector(secret_key=os.environ["STRIPE_SECRET_KEY"]))

    yardi_required = (
        os.environ.get("YARDI_BASE_URL"),
        os.environ.get("YARDI_WORK_ORDERS_PATH"),
        os.environ.get("YARDI_AUTH_VALUE"),
    )
    if all(yardi_required):
        from live_connectors import YardiMaintenanceConnector
        hub.register(YardiMaintenanceConnector(
            base_url=os.environ["YARDI_BASE_URL"],
            work_orders_path=os.environ["YARDI_WORK_ORDERS_PATH"],
            auth_header=os.environ.get("YARDI_AUTH_HEADER", "Authorization"),
            auth_value=os.environ["YARDI_AUTH_VALUE"],
            field_map=_json_object_env("YARDI_FIELD_MAP"),
            response_items_key=os.environ.get("YARDI_ITEMS_KEY", "work_orders"),
            response_format=os.environ.get("YARDI_RESPONSE_FORMAT", "json"),
            xml_item_tag=os.environ.get("YARDI_XML_ITEM_TAG", "WorkOrder"),
        ))

    servicetitan_required = (
        os.environ.get("SERVICETITAN_JOBS_URL"),
        os.environ.get("SERVICETITAN_ACCESS_TOKEN"),
        os.environ.get("SERVICETITAN_APP_KEY"),
    )
    if all(servicetitan_required):
        from live_connectors import ServiceTitanConnector
        hub.register(ServiceTitanConnector(
            jobs_url=os.environ["SERVICETITAN_JOBS_URL"],
            access_token=os.environ["SERVICETITAN_ACCESS_TOKEN"],
            app_key=os.environ["SERVICETITAN_APP_KEY"],
            field_map=_json_object_env("SERVICETITAN_FIELD_MAP"),
            items_key=os.environ.get("SERVICETITAN_ITEMS_KEY", "data"),
        ))

    return hub
