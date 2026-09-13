import pytest

from secure_web_app import create_secure_app


def test_production_requires_explicit_session_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKKEEPER_PRODUCTION", "1")
    monkeypatch.delenv("BOOKKEEPER_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="BOOKKEEPER_SECRET is required"):
        create_secure_app(str(tmp_path / "bookkeeper.json"))


def test_production_uses_configured_session_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKKEEPER_PRODUCTION", "1")
    monkeypatch.setenv("BOOKKEEPER_SECRET", "a-long-random-production-secret-for-tests")
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    assert app.secret_key == "a-long-random-production-secret-for-tests"
    assert app.config["SESSION_SECRET_CONFIGURED"] is True
    assert app.config["BOOKKEEPER_PRODUCTION"] is True


def test_local_mode_never_uses_known_development_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("BOOKKEEPER_PRODUCTION", raising=False)
    monkeypatch.delenv("BOOKKEEPER_SECRET", raising=False)
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    assert app.secret_key
    assert app.secret_key != "dev-only-change-me"
    assert app.config["SESSION_SECRET_CONFIGURED"] is False
