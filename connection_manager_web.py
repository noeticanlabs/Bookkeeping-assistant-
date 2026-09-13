"""In-app external connection management with encrypted credential storage."""

from __future__ import annotations

import json
import secrets
import time
import urllib.parse
import urllib.request

from flask import flash, g, redirect, render_template, request, session, url_for

from connection_manager import ConnectionStore
from connectors import DEPOSITS_READ, INVOICES_WRITE, PAYMENTS_READ, WORK_ORDERS_READ
from managed_connectors import QBO_TOKEN_URL, _post_form, load_managed_connectors


XERO_AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
QBO_AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
QBO_SCOPE = "com.intuit.quickbooks.accounting"

PROVIDER_CAPABILITIES = {
    "xero": (PAYMENTS_READ, INVOICES_WRITE),
    "stripe": (PAYMENTS_READ, DEPOSITS_READ),
    "jobber": (WORK_ORDERS_READ,),
    "housecall_pro": (WORK_ORDERS_READ,),
    "quickbooks": (PAYMENTS_READ, DEPOSITS_READ),
    "servicetitan": (WORK_ORDERS_READ,),
    "yardi": (WORK_ORDERS_READ,),
}

PROVIDER_LABELS = {
    "xero": "Xero",
    "stripe": "Stripe",
    "jobber": "Jobber",
    "housecall_pro": "Housecall Pro",
    "quickbooks": "QuickBooks Online",
    "servicetitan": "ServiceTitan",
    "yardi": "Yardi Maintenance",
}


def _json_object(raw: str, field: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _manual_secrets(provider: str, form) -> dict:
    if provider == "stripe":
        return {"secret_key": form.get("secret_key", "").strip()}
    if provider == "jobber":
        return {
            "access_token": form.get("access_token", "").strip(),
            "graphql_version": form.get("graphql_version", "2025-04-16").strip(),
        }
    if provider == "housecall_pro":
        return {"api_key": form.get("api_key", "").strip()}
    if provider == "quickbooks":
        return {
            "client_id": form.get("client_id", "").strip(),
            "client_secret": form.get("client_secret", "").strip(),
            "redirect_uri": form.get("redirect_uri", "").strip(),
            "scope": form.get("scope", QBO_SCOPE).strip() or QBO_SCOPE,
            "sandbox": form.get("sandbox") == "yes",
        }
    if provider == "servicetitan":
        return {
            "jobs_url": form.get("jobs_url", "").strip(),
            "access_token": form.get("access_token", "").strip(),
            "app_key": form.get("app_key", "").strip(),
            "field_map": _json_object(form.get("field_map", "{}"), "Field map"),
            "items_key": form.get("items_key", "data").strip() or "data",
        }
    if provider == "yardi":
        return {
            "base_url": form.get("base_url", "").strip(),
            "work_orders_path": form.get("work_orders_path", "").strip(),
            "auth_header": form.get("auth_header", "Authorization").strip() or "Authorization",
            "auth_value": form.get("auth_value", "").strip(),
            "field_map": _json_object(form.get("field_map", "{}"), "Field map"),
            "items_key": form.get("items_key", "work_orders").strip() or "work_orders",
            "response_format": form.get("response_format", "json").strip() or "json",
            "xml_item_tag": form.get("xml_item_tag", "WorkOrder").strip() or "WorkOrder",
        }
    if provider == "xero":
        return {
            "client_id": form.get("client_id", "").strip(),
            "client_secret": form.get("client_secret", "").strip(),
            "redirect_uri": form.get("redirect_uri", "").strip(),
            "scope": form.get("scope", "openid profile email offline_access accounting.transactions").strip(),
            "contact_ids": _json_object(form.get("contact_ids", "{}"), "Contact mappings"),
            "revenue_account_code": form.get("revenue_account_code", "").strip(),
        }
    raise ValueError("Unsupported connection provider")


def _validate_required(provider: str, data: dict) -> None:
    required = {
        "stripe": ("secret_key",),
        "jobber": ("access_token",),
        "housecall_pro": ("api_key",),
        "quickbooks": ("client_id", "client_secret", "redirect_uri", "scope"),
        "servicetitan": ("jobs_url", "access_token", "app_key"),
        "yardi": ("base_url", "work_orders_path", "auth_value"),
        "xero": ("client_id", "client_secret", "redirect_uri", "scope"),
    }[provider]
    missing = [key for key in required if not str(data.get(key, "")).strip()]
    if missing:
        raise ValueError("Missing required connection fields: " + ", ".join(missing))


def _reload_managed(app, store: ConnectionStore) -> None:
    hub = app.config["CONNECTOR_HUB"]
    hub.connectors = [c for c in hub.connectors if not getattr(c, "_managed_connection_id", None)]
    hub.field_service = None
    hub.accounting = None
    hub.documents = None
    existing = list(hub.connectors)
    hub.connectors = []
    for connector in existing:
        hub.register(connector)
    load_managed_connectors(hub, store)


def install_connection_manager(app, store: ConnectionStore | None):
    app.config["CONNECTION_STORE"] = store

    def allowed() -> bool:
        user = getattr(g, "current_user", None)
        permissions = app.config["PERMISSION_STORE"]
        return bool(user and permissions.user_has(user, "company.configure"))

    @app.get("/settings/connections/manage")
    def managed_connections():
        if not allowed():
            return redirect(url_for("dashboard"))
        return render_template(
            "managed_connections.html",
            store=store,
            connections=store.list() if store else [],
            providers=PROVIDER_LABELS,
        )

    @app.route("/settings/connections/new/<provider>", methods=["GET", "POST"])
    def add_managed_connection(provider: str):
        if not allowed():
            return redirect(url_for("dashboard"))
        if store is None:
            flash("Set BOOKKEEPER_CREDENTIAL_KEY before storing connection credentials", "error")
            return redirect(url_for("managed_connections"))
        if provider not in PROVIDER_LABELS:
            flash("Unsupported connection provider", "error")
            return redirect(url_for("managed_connections"))
        if request.method == "POST":
            try:
                data = _manual_secrets(provider, request.form)
                _validate_required(provider, data)
                needs_oauth = provider in {"xero", "quickbooks"}
                status = "authorization_required" if needs_oauth else "configured"
                record = store.save(
                    provider,
                    request.form.get("label", "").strip() or PROVIDER_LABELS[provider],
                    PROVIDER_CAPABILITIES[provider],
                    data,
                    status=status,
                )
                if not needs_oauth:
                    _reload_managed(app, store)
                app.config["AUDIT_LOG"].append(
                    "connection.configured", f"CONNECTION:{record.connection_id}",
                    {"provider": provider, "connection_id": record.connection_id,
                     "capabilities": list(record.capabilities)},
                    actor=g.current_user.user_id,
                )
                flash("Connection configuration saved; secrets are encrypted at rest", "success")
                if provider == "xero":
                    return redirect(url_for("xero_authorize", connection_id=record.connection_id))
                if provider == "quickbooks":
                    return redirect(url_for("quickbooks_authorize", connection_id=record.connection_id))
                return redirect(url_for("managed_connections"))
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template(
            "managed_connection_form.html",
            provider=provider,
            provider_label=PROVIDER_LABELS[provider],
        )

    @app.get("/settings/connections/xero/<connection_id>/authorize")
    def xero_authorize(connection_id: str):
        if not allowed() or store is None:
            return redirect(url_for("dashboard"))
        try:
            record = store.get(connection_id)
            if record.provider != "xero":
                raise ValueError("Connection is not Xero")
            data = store.secrets(connection_id)
            state = secrets.token_urlsafe(32)
            session["xero_oauth_state"] = state
            session["xero_oauth_connection_id"] = connection_id
            params = {
                "response_type": "code",
                "client_id": data["client_id"],
                "redirect_uri": data["redirect_uri"],
                "scope": data["scope"],
                "state": state,
            }
            return redirect(XERO_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("managed_connections"))

    @app.get("/oauth/xero/callback")
    def xero_oauth_callback():
        if store is None:
            return redirect(url_for("login"))
        try:
            state = request.args.get("state", "")
            if not state or state != session.pop("xero_oauth_state", None):
                raise ValueError("Xero OAuth state check failed")
            connection_id = session.pop("xero_oauth_connection_id", None)
            if not connection_id:
                raise ValueError("Xero OAuth session is missing")
            code = request.args.get("code", "")
            if not code:
                raise ValueError(request.args.get("error_description") or "Xero authorization was not completed")
            data = store.secrets(connection_id)
            token = _post_form(
                "https://identity.xero.com/connect/token",
                {"grant_type": "authorization_code", "code": code, "redirect_uri": data["redirect_uri"]},
                basic_user=data["client_id"], basic_password=data["client_secret"],
            )
            access_token = token["access_token"]
            req = urllib.request.Request(XERO_CONNECTIONS_URL, method="GET")
            req.add_header("Authorization", f"Bearer {access_token}")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=30) as response:
                connections = json.loads(response.read().decode("utf-8"))
            if not isinstance(connections, list) or not connections:
                raise ValueError("Xero returned no authorised organisation")
            tenant_id = connections[0].get("tenantId")
            if not tenant_id:
                raise ValueError("Xero connection did not include a tenant ID")
            data.update({
                "access_token": access_token,
                "refresh_token": token.get("refresh_token"),
                "expires_at": time.time() + int(token.get("expires_in") or 1800),
                "tenant_id": tenant_id,
                "xero_connection_id": connections[0].get("id"),
                "tenant_name": connections[0].get("tenantName"),
            })
            record = store.get(connection_id)
            store.save(record.provider, record.label, record.capabilities, data,
                       connection_id=connection_id, status="connected")
            _reload_managed(app, store)
            app.config["AUDIT_LOG"].append(
                "connection.authorized", f"CONNECTION:{connection_id}",
                {"provider": "xero", "connection_id": connection_id,
                 "tenant_name": data.get("tenant_name")}, actor="oauth:xero",
            )
            flash("Xero connection authorized", "success")
        except Exception as exc:
            flash(f"Xero authorization failed: {exc}", "error")
        return redirect(url_for("managed_connections"))

    @app.get("/settings/connections/quickbooks/<connection_id>/authorize")
    def quickbooks_authorize(connection_id: str):
        if not allowed() or store is None:
            return redirect(url_for("dashboard"))
        try:
            record = store.get(connection_id)
            if record.provider != "quickbooks":
                raise ValueError("Connection is not QuickBooks")
            data = store.secrets(connection_id)
            state = secrets.token_urlsafe(32)
            session["qbo_oauth_state"] = state
            session["qbo_oauth_connection_id"] = connection_id
            params = {
                "client_id": data["client_id"],
                "response_type": "code",
                "scope": data.get("scope") or QBO_SCOPE,
                "redirect_uri": data["redirect_uri"],
                "state": state,
            }
            return redirect(QBO_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("managed_connections"))

    @app.get("/oauth/quickbooks/callback")
    def quickbooks_oauth_callback():
        if store is None:
            return redirect(url_for("login"))
        try:
            state = request.args.get("state", "")
            if not state or state != session.pop("qbo_oauth_state", None):
                raise ValueError("QuickBooks OAuth state check failed")
            connection_id = session.pop("qbo_oauth_connection_id", None)
            if not connection_id:
                raise ValueError("QuickBooks OAuth session is missing")
            code = request.args.get("code", "")
            realm_id = request.args.get("realmId", "").strip()
            if not code:
                raise ValueError(request.args.get("error_description") or "QuickBooks authorization was not completed")
            if not realm_id:
                raise ValueError("QuickBooks callback did not include a realmId")
            data = store.secrets(connection_id)
            token = _post_form(
                QBO_TOKEN_URL,
                {"grant_type": "authorization_code", "code": code, "redirect_uri": data["redirect_uri"]},
                basic_user=data["client_id"], basic_password=data["client_secret"],
            )
            data.update({
                "realm_id": realm_id,
                "access_token": token["access_token"],
                "refresh_token": token.get("refresh_token"),
                "expires_at": time.time() + int(token.get("expires_in") or 3600),
            })
            if token.get("x_refresh_token_expires_in") is not None:
                data["refresh_expires_at"] = time.time() + int(token["x_refresh_token_expires_in"])
            record = store.get(connection_id)
            store.save(record.provider, record.label, record.capabilities, data,
                       connection_id=connection_id, status="connected")
            _reload_managed(app, store)
            app.config["AUDIT_LOG"].append(
                "connection.authorized", f"CONNECTION:{connection_id}",
                {"provider": "quickbooks", "connection_id": connection_id,
                 "realm_id": realm_id}, actor="oauth:quickbooks",
            )
            flash("QuickBooks Online connection authorized", "success")
        except Exception as exc:
            flash(f"QuickBooks authorization failed: {exc}", "error")
        return redirect(url_for("managed_connections"))

    @app.post("/settings/connections/<connection_id>/delete")
    def delete_managed_connection(connection_id: str):
        if not allowed() or store is None:
            return redirect(url_for("dashboard"))
        try:
            record = store.get(connection_id)
            store.delete(connection_id)
            _reload_managed(app, store)
            app.config["AUDIT_LOG"].append(
                "connection.deleted", f"CONNECTION:{connection_id}",
                {"provider": record.provider, "connection_id": connection_id},
                actor=g.current_user.user_id,
            )
            flash("Connection deleted", "success")
        except KeyError as exc:
            flash(str(exc), "error")
        return redirect(url_for("managed_connections"))

    return store
