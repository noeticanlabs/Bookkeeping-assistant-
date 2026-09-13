"""Instantiate live adapters from encrypted managed connection records."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from typing import Any

from connection_manager import ConnectionStore
from connectors import ConnectorHub
from live_connectors import (
    HousecallProConnector,
    JobberConnector,
    QuickBooksOnlineConnector,
    ServiceTitanConnector,
    StripeConnector,
    XeroConnector,
    YardiMaintenanceConnector,
)


XERO_TOKEN_URL = "https://identity.xero.com/connect/token"


def _post_form(url: str, fields: dict[str, str], *, basic_user: str | None = None,
               basic_password: str | None = None) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if basic_user is not None:
        raw = f"{basic_user}:{basic_password or ''}".encode("utf-8")
        request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth token endpoint returned an invalid response")
    return payload


class ManagedXeroConnector:
    """Xero adapter that refreshes and rotates encrypted OAuth tokens on demand."""

    name = "Xero"

    def __init__(self, store: ConnectionStore, connection_id: str):
        self.store = store
        self.connection_id = connection_id

    @property
    def capabilities(self):
        return frozenset(self.store.get(self.connection_id).capabilities)

    def _settings(self) -> dict[str, Any]:
        data = self.store.secrets(self.connection_id)
        expires_at = float(data.get("expires_at") or 0)
        if data.get("refresh_token") and expires_at <= time.time() + 60:
            token = _post_form(
                XERO_TOKEN_URL,
                {"grant_type": "refresh_token", "refresh_token": str(data["refresh_token"])},
                basic_user=str(data["client_id"]),
                basic_password=str(data["client_secret"]),
            )
            data["access_token"] = token["access_token"]
            if token.get("refresh_token"):
                data["refresh_token"] = token["refresh_token"]
            data["expires_at"] = time.time() + int(token.get("expires_in") or 1800)
            record = self.store.get(self.connection_id)
            self.store.save(
                record.provider, record.label, record.capabilities, data,
                connection_id=record.connection_id, status="connected",
            )
        return data

    def _connector(self) -> XeroConnector:
        data = self._settings()
        return XeroConnector(
            tenant_id=str(data["tenant_id"]),
            access_token=str(data["access_token"]),
            contact_ids=dict(data.get("contact_ids") or {}),
            revenue_account_code=data.get("revenue_account_code") or None,
        )

    def pull_payments(self):
        return self._connector().pull_payments()

    def push_invoice(self, invoice):
        return self._connector().push_invoice(invoice)


def build_connector(store: ConnectionStore, connection_id: str):
    record = store.get(connection_id)
    data = store.secrets(connection_id)
    provider = record.provider.lower()

    if provider == "xero":
        if record.status != "connected" or not data.get("access_token") or not data.get("tenant_id"):
            return None
        return ManagedXeroConnector(store, connection_id)
    if provider == "stripe":
        return StripeConnector(secret_key=str(data["secret_key"]))
    if provider == "jobber":
        return JobberConnector(
            access_token=str(data["access_token"]),
            graphql_version=str(data.get("graphql_version") or "2025-04-16"),
        )
    if provider == "housecall_pro":
        return HousecallProConnector(api_key=str(data["api_key"]))
    if provider == "quickbooks":
        return QuickBooksOnlineConnector(
            realm_id=str(data["realm_id"]),
            access_token=str(data["access_token"]),
            sandbox=bool(data.get("sandbox")),
        )
    if provider == "servicetitan":
        return ServiceTitanConnector(
            jobs_url=str(data["jobs_url"]),
            access_token=str(data["access_token"]),
            app_key=str(data["app_key"]),
            field_map=dict(data.get("field_map") or {}),
            items_key=str(data.get("items_key") or "data"),
        )
    if provider == "yardi":
        return YardiMaintenanceConnector(
            base_url=str(data["base_url"]),
            work_orders_path=str(data["work_orders_path"]),
            auth_header=str(data.get("auth_header") or "Authorization"),
            auth_value=str(data["auth_value"]),
            field_map=dict(data.get("field_map") or {}),
            response_items_key=str(data.get("items_key") or "work_orders"),
            response_format=str(data.get("response_format") or "json"),
            xml_item_tag=str(data.get("xml_item_tag") or "WorkOrder"),
        )
    return None


def load_managed_connectors(hub: ConnectorHub, store: ConnectionStore) -> list[str]:
    loaded: list[str] = []
    for record in store.list():
        if record.status not in {"configured", "connected"}:
            continue
        connector = build_connector(store, record.connection_id)
        if connector is not None:
            hub.register(connector)
            loaded.append(record.connection_id)
    return loaded
