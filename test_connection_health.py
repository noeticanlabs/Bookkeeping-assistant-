import time

from cryptography.fernet import Fernet

from connection_health import connection_health
from connection_manager import ConnectionStore, CredentialCipher
from connectors import PAYMENTS_READ


def _store(tmp_path):
    return ConnectionStore(tmp_path / "bookkeeper.sqlite3", CredentialCipher(Fernet.generate_key()))


def test_authorization_required_is_explicit(tmp_path):
    store = _store(tmp_path)
    record = store.save(
        "xero", "Xero", [PAYMENTS_READ],
        {"client_id": "c", "client_secret": "s"}, status="authorization_required",
    )
    health = connection_health(store, record.connection_id)
    assert health["state"] == "authorization_required"


def test_expired_access_with_refresh_token_is_refresh_due(tmp_path):
    store = _store(tmp_path)
    record = store.save(
        "jobber", "Jobber", ["work_orders.read"],
        {
            "access_token": "expired",
            "refresh_token": "refresh",
            "expires_at": time.time() - 10,
            "account_name": "Acme Service",
        },
        status="connected",
    )
    health = connection_health(store, record.connection_id)
    assert health["state"] == "refresh_due"
    assert health["identity"] == "Acme Service"
    assert health["refreshable"] is True


def test_expired_refresh_authorization_requires_reconnect(tmp_path):
    store = _store(tmp_path)
    record = store.save(
        "quickbooks", "QuickBooks", [PAYMENTS_READ],
        {
            "access_token": "expired",
            "refresh_token": "expired-refresh",
            "expires_at": time.time() - 100,
            "refresh_expires_at": time.time() - 1,
            "realm_id": "realm-99",
        },
        status="connected",
    )
    health = connection_health(store, record.connection_id)
    assert health["state"] == "reauthorize_required"
    assert health["identity"] == "realm-99"


def test_static_key_connection_can_be_configured_without_token_expiry(tmp_path):
    store = _store(tmp_path)
    record = store.save(
        "stripe", "Stripe", [PAYMENTS_READ], {"secret_key": "sk_test"}, status="configured"
    )
    health = connection_health(store, record.connection_id)
    assert health["state"] == "configured"
    assert health["refreshable"] is False
