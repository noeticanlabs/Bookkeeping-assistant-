from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from connection_manager import ConnectionStore, CredentialCipher
from connectors import ConnectorHub, DEPOSITS_READ, PAYMENTS_READ
from managed_connectors import load_managed_connectors


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
