"""Live external-system adapters with narrow, explicit capabilities.

Credentials are supplied by environment/configuration. No adapter is authoritative:
all pulled records enter the existing normalization/review paths.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app import BankDeposit, Payment, WorkOrder
from connectors import DEPOSITS_READ, PAYMENTS_READ, WORK_ORDERS_READ


class ConnectorError(RuntimeError):
    pass


def _json_request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
                  body: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:
        raise ConnectorError(f"Connector request failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectorError("Connector returned invalid JSON") from exc


def _normalize_status(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"completed", "complete", "closed", "done", "paid"}:
        return "complete"
    if text in {"cancelled", "canceled", "void"}:
        return "cancelled"
    return text or "open"


def _money(value: object) -> Decimal:
    return Decimal(str(value or "0"))


@dataclass
class YardiMaintenanceConnector:
    """Yardi Voyager Maintenance API work-order source.

    Yardi exposes Maintenance API access through its interface program, but the
    exact endpoint/schema is client/interface specific. This adapter therefore
    uses the live endpoint and field mapping supplied by the customer's Yardi
    interface configuration instead of inventing undocumented paths.
    """

    base_url: str
    work_orders_path: str
    auth_header: str
    auth_value: str
    field_map: dict[str, str]
    response_items_key: str = "work_orders"
    response_format: str = "json"
    xml_item_tag: str = "WorkOrder"
    name: str = "Yardi Maintenance"
    capabilities: frozenset[str] = frozenset({WORK_ORDERS_READ})

    def _value(self, row: dict[str, Any], logical: str, default: Any = None) -> Any:
        return row.get(self.field_map.get(logical, logical), default)

    def _rows(self) -> list[dict[str, Any]]:
        url = self.base_url.rstrip("/") + "/" + self.work_orders_path.lstrip("/")
        request = urllib.request.Request(url, method="GET")
        request.add_header(self.auth_header, self.auth_value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise ConnectorError(f"Yardi request failed: {exc}") from exc

        if self.response_format.lower() == "xml":
            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise ConnectorError("Yardi returned invalid XML") from exc
            return [{child.tag.split("}")[-1]: child.text for child in node} for node in root.iter() if node.tag.split("}")[-1] == self.xml_item_tag]

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectorError("Yardi returned invalid JSON") from exc
        rows = payload.get(self.response_items_key, payload if isinstance(payload, list) else [])
        if not isinstance(rows, list):
            raise ConnectorError("Yardi work-order response is not a list")
        return rows

    def pull_work_orders(self) -> list[WorkOrder]:
        result: list[WorkOrder] = []
        for row in self._rows():
            external_id = str(self._value(row, "id", "")).strip()
            if not external_id:
                continue
            customer = str(self._value(row, "customer", self._value(row, "resident", "Unknown"))).strip() or "Unknown"
            description = str(self._value(row, "description", self._value(row, "problem", "Work order"))).strip() or "Work order"
            result.append(WorkOrder(
                id=f"YARDI:{external_id}",
                customer=customer,
                description=description,
                status=_normalize_status(self._value(row, "status", "open")),
                quoted_total=None,
            ))
        return result


@dataclass
class JobberConnector:
    access_token: str
    graphql_version: str = "2025-04-16"
    name: str = "Jobber"
    capabilities: frozenset[str] = frozenset({WORK_ORDERS_READ})
    endpoint: str = "https://api.getjobber.com/api/graphql"

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = _json_request(
            self.endpoint,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "X-JOBBER-GRAPHQL-VERSION": self.graphql_version,
            },
            body={"query": query, "variables": variables},
        )
        if payload.get("errors"):
            raise ConnectorError(f"Jobber GraphQL error: {payload['errors']}")
        return payload.get("data") or {}

    def pull_work_orders(self) -> list[WorkOrder]:
        query = """
        query GetJobs($cursor: String) {
          jobs(first: 100, after: $cursor) {
            nodes { id jobNumber title jobStatus client { name } }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        cursor = None
        result: list[WorkOrder] = []
        while True:
            data = self._query(query, {"cursor": cursor})
            jobs = data.get("jobs") or {}
            for row in jobs.get("nodes") or []:
                external_id = str(row.get("id") or row.get("jobNumber") or "").strip()
                if not external_id:
                    continue
                result.append(WorkOrder(
                    id=f"JOBBER:{external_id}",
                    customer=((row.get("client") or {}).get("name") or "Unknown").strip(),
                    description=(row.get("title") or f"Job {row.get('jobNumber', '')}").strip(),
                    status=_normalize_status(row.get("jobStatus")),
                    quoted_total=None,
                ))
            page = jobs.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                break
        return result


@dataclass
class HousecallProConnector:
    api_key: str
    name: str = "Housecall Pro"
    capabilities: frozenset[str] = frozenset({WORK_ORDERS_READ})
    base_url: str = "https://api.housecallpro.com"

    def pull_work_orders(self) -> list[WorkOrder]:
        page = 1
        result: list[WorkOrder] = []
        while True:
            query = urllib.parse.urlencode({"page": page, "page_size": 100})
            payload = _json_request(
                f"{self.base_url.rstrip('/')}/jobs?{query}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            rows = payload.get("jobs") or []
            for row in rows:
                external_id = str(row.get("id") or row.get("uuid") or row.get("number") or "").strip()
                if not external_id:
                    continue
                customer = row.get("customer") or {}
                customer_name = customer.get("name") or customer.get("company") or " ".join(
                    part for part in (customer.get("first_name"), customer.get("last_name")) if part
                ) or "Unknown"
                description = row.get("name") or row.get("description") or row.get("notes") or f"Job {row.get('number', '')}"
                result.append(WorkOrder(
                    id=f"HCP:{external_id}",
                    customer=str(customer_name).strip(),
                    description=str(description).strip() or "Job",
                    status=_normalize_status(row.get("work_status") or row.get("status")),
                    quoted_total=None,
                ))
            total_pages = int(payload.get("total_pages") or page)
            if page >= total_pages:
                break
            page += 1
        return result


@dataclass
class QuickBooksOnlineConnector:
    realm_id: str
    access_token: str
    sandbox: bool = False
    name: str = "QuickBooks Online"
    capabilities: frozenset[str] = frozenset({PAYMENTS_READ, DEPOSITS_READ})

    @property
    def base_url(self) -> str:
        return "https://sandbox-quickbooks.api.intuit.com" if self.sandbox else "https://quickbooks.api.intuit.com"

    def _query(self, entity: str) -> list[dict[str, Any]]:
        statement = urllib.parse.quote(f"select * from {entity} maxresults 1000", safe="")
        url = f"{self.base_url}/v3/company/{urllib.parse.quote(self.realm_id)}/query?query={statement}"
        payload = _json_request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
        )
        return (payload.get("QueryResponse") or {}).get(entity) or []

    def pull_payments(self) -> list[Payment]:
        result: list[Payment] = []
        for row in self._query("Payment"):
            payment_id = str(row.get("Id") or "").strip()
            if not payment_id:
                continue
            reference = row.get("PaymentRefNum") or row.get("PrivateNote")
            result.append(Payment(
                id=f"QBO-PAY:{payment_id}",
                amount=_money(row.get("TotalAmt")),
                reference=str(reference).strip() if reference else None,
            ))
        return result

    def pull_deposits(self) -> list[BankDeposit]:
        result: list[BankDeposit] = []
        for row in self._query("Deposit"):
            deposit_id = str(row.get("Id") or "").strip()
            if not deposit_id:
                continue
            reference = row.get("PrivateNote")
            result.append(BankDeposit(
                id=f"QBO-DEP:{deposit_id}",
                amount=_money(row.get("TotalAmt")),
                reference=str(reference).strip() if reference else None,
            ))
        return result
