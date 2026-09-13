import time

import pytest
from cryptography.fernet import Fernet

from connection_manager import ConnectionStore, CredentialCipher
from connectors import ConnectorHub, DEPOSITS_READ, PAYMENTS_READ
import managed_connectors
from managed_connectors import ManagedQuickBooksConnector, ManagedXeroConnector, load_managed_connectors


def test_connection_secrets_are_encrypted_at_rest(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    secret = "sk_live_super_secret_value"
    store = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    record = store.save("stripe", "Stripe", [PAYMENTS_READ, DEPOSITS_READ], {"secret_key": secret})

    assert store.secrets(record.connection_id)["secret_key"] == secret
    assert secret.encode("utf-8") not in db.read_bytes()


def test_wrong_credential_key_cannot_decrypt_saved_connection(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    first = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    record = first.save("stripe", "Stripe", [PAYMENTS_READ], {"secret_key": "sk_test_123"})

    second = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    with pytest.raises(ValueError, match="cannot be decrypted"):
        second.secrets(record.connection_id)


def test_encrypted_connection_rebuilds_live_capability_adapter(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    store = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    store.save("stripe", "Stripe", [PAYMENTS_READ, DEPOSITS_READ], {"secret_key": "sk_test_123"})
    hub = ConnectorHub()

    loaded = load_managed_connectors(hub, store)

    assert len(loaded) == 1
    assert len(hub.payment_sources()) == 1
    assert len(hub.deposit_sources()) == 1
    connector = hub.payment_sources()[0]
    assert getattr(connector, "_managed_connection_id", None) == loaded[0]


def test_xero_requires_authorization_before_runtime_loading(tmp_path):
    db = tmp_path / "bookkeeper.sqlite3"
    store = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    store.save(
        "xero", "Xero", [PAYMENTS_READ, "invoices.write"],
        {"client_id": "client", "client_secret": "secret", "redirect_uri": "http://localhost/callback"},
        status="authorization_required",
    )
    hub = ConnectorHub()

    assert load_managed_connectors(hub, store) == []
    assert hub.payment_sources() == []


def test_xero_refresh_rotates_tokens_inside_encrypted_store(tmp_path, monkeypatch):
    db = tmp_path / "bookkeeper.sqlite3"
    store = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    record = store.save(
        "xero", "Xero", [PAYMENTS_READ, "invoices.write"],
        {
            "client_id": "client",
            "client_secret": "secret",
            "tenant_id": "tenant",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": time.time() - 10,
        },
        status="connected",
    )

    def fake_post(url, fields, **kwargs):
        assert fields["refresh_token"] == "old-refresh"
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800}

    monkeypatch.setattr(managed_connectors, "_post_form", fake_post)
    connector = ManagedXeroConnector(store, record.connection_id)
    settings = connector._settings()

    assert settings["access_token"] == "new-access"
    assert settings["refresh_token"] == "new-refresh"
    persisted = store.secrets(record.connection_id)
    assert persisted["access_token"] == "new-access"
    assert persisted["refresh_token"] == "new-refresh"
    assert b"new-refresh" not in db.read_bytes()


def test_quickbooks_refresh_rotates_tokens_and_preserves_realm(tmp_path, monkeypatch):
    db = tmp_path / "bookkeeper.sqlite3"
    store = ConnectionStore(db, CredentialCipher(Fernet.generate_key()))
    record = store.save(
        "quickbooks", "QuickBooks Online", [PAYMENTS_READ, DEPOSITS_READ],
        {
            "client_id": "qbo-client",
            "client_secret": "qbo-secret",
            "realm_id": "company-123",
            "access_token": "old-qbo-access",
            "refresh_token": "old-qbo-refresh",
            "expires_at": time.time() - 10,
            "sandbox": True,
        },
        status="connected",
    )

    def fake_post(url, fields, **kwargs):
        assert url == managed_connectors.QBO_TOKEN_URL
        assert fields["grant_type"] == "refresh_token"
        assert fields["refresh_token"] == "old-qbo-refresh"
        assert kwargs["basic_user"] == "qbo-client"
        assert kwargs["basic_password"] == "qbo-secret"
        return {
            "access_token": "new-qbo-access",
            "refresh_token": "new-qbo-refresh",
            "expires_in": 3600,
            "x_refresh_token_expires_in": 8640000,
        }

    monkeypatch.setattr(managed_connectors, "_post_form", fake_post)
    connector = ManagedQuickBooksConnector(store, record.connection_id)
    settings = connector._settings()

    assert settings["access_token"] == "new-qbo-access"
    assert settings["refresh_token"] == "new-qbo-refresh"
    assert settings["realm_id"] == "company-123"
    persisted = store.secrets(record.connection_id)
    assert persisted["realm_id"] == "company-123"
    assert persisted["refresh_token"] == "new-qbo-refresh"
    assert persisted["refresh_expires_at"] > time.time()
    assert b"new-qbo-refresh" not in db.read_bytes()
