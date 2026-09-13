"""Safe operational summaries for encrypted managed connections.

Only non-secret metadata is exposed to templates/readiness. Token values and
client secrets never leave the encrypted credential store.
"""

from __future__ import annotations

import time

from connection_manager import ConnectionStore


def connection_health(store: ConnectionStore | None, connection_id: str) -> dict[str, object]:
    if store is None:
        return {"state": "disabled", "detail": "Encrypted credential storage is disabled."}
    try:
        record = store.get(connection_id)
        data = store.secrets(connection_id)
    except (KeyError, ValueError) as exc:
        return {"state": "error", "detail": str(exc)}

    provider = record.provider.lower()
    now = time.time()
    expires_at = float(data.get("expires_at") or 0)
    refresh_expires_at = float(data.get("refresh_expires_at") or 0)
    refreshable = bool(data.get("refresh_token"))

    identity = None
    if provider == "xero":
        identity = data.get("tenant_name") or data.get("tenant_id")
    elif provider == "quickbooks":
        identity = data.get("realm_id")
    elif provider == "jobber":
        identity = data.get("account_name") or data.get("account_id")

    if record.status == "authorization_required":
        state = "authorization_required"
        detail = "Authorization has not been completed."
    elif record.status not in {"configured", "connected"}:
        state = record.status
        detail = f"Connection status: {record.status}."
    elif expires_at:
        remaining = int(expires_at - now)
        if remaining <= 0:
            state = "refresh_due" if refreshable else "expired"
            detail = "Access token is expired; it will refresh automatically on next use." if refreshable else "Access token is expired and cannot refresh automatically."
        elif remaining <= 300:
            state = "refresh_due" if refreshable else "expiring"
            detail = f"Access token expires in about {max(1, remaining // 60)} minute(s)."
        else:
            state = "connected"
            detail = f"Access token valid for about {max(1, remaining // 60)} more minute(s)."
    else:
        state = "connected" if record.status == "connected" else "configured"
        detail = "Connection is configured."

    if refresh_expires_at and refresh_expires_at <= now:
        state = "reauthorize_required"
        detail = "Refresh authorization has expired; reconnect this provider."

    return {
        "state": state,
        "detail": detail,
        "identity": identity,
        "refreshable": refreshable,
        "provider": provider,
    }


def install_connection_health(app, store: ConnectionStore | None) -> None:
    app.jinja_env.globals["connection_health"] = lambda connection_id: connection_health(store, connection_id)
    app.config["CONNECTION_HEALTH"] = lambda connection_id: connection_health(store, connection_id)
