"""Authenticated Bookkeeper Assistant entrypoint."""

import os
import secrets

from auth_web import install_auth
from connection_health import install_connection_health
from connection_manager import ConnectionStore, CredentialCipher
from connection_manager_web import install_connection_manager
from csrf import install_csrf
from managed_connectors import load_managed_connectors
from multi_connector_web import install_multi_connector_routes
from onboarding import install_onboarding
from pull_scheduler import PullScheduleStore
from readiness import install_readiness
from redirect_safety import install_redirect_safety
from scheduled_sync import discover_safe_pull_schedules, start_scheduler_thread
from sod_web import install_separation_of_duties
from sqlite_store import save_bookkeeper
from sync_reliability import SyncReliabilityStore
from web_app import create_app


def _configure_session_secret(app) -> None:
    configured = os.environ.get("BOOKKEEPER_SECRET", "").strip()
    production = os.environ.get("BOOKKEEPER_PRODUCTION") == "1"
    if production and not configured:
        raise RuntimeError("BOOKKEEPER_SECRET is required when BOOKKEEPER_PRODUCTION=1")
    app.secret_key = configured or secrets.token_hex(32)
    app.config["SESSION_SECRET_CONFIGURED"] = bool(configured)
    app.config["BOOKKEEPER_PRODUCTION"] = production


def create_secure_app(data_path: str | None = None, connectors=None):
    app = create_app(data_path, connectors=connectors)
    _configure_session_secret(app)
    db_path = app.config["BOOKKEEPER_DATA_PATH"]
    app.config["SAVE_BOOKKEEPER"] = lambda: save_bookkeeper(app.config["BOOKKEEPER"], db_path)
    app.config["SYNC_RELIABILITY"] = SyncReliabilityStore(db_path)
    app.config["PULL_SCHEDULES"] = PullScheduleStore(db_path)
    app.config["AUTO_SYNC_INTERVAL_SECONDS"] = max(
        60, int(os.environ.get("BOOKKEEPER_AUTO_SYNC_INTERVAL_SECONDS", "900"))
    )

    cipher = CredentialCipher.from_environment()
    connection_store = ConnectionStore(db_path, cipher) if cipher is not None else None
    app.config["CREDENTIAL_ENCRYPTION_CONFIGURED"] = cipher is not None
    if connection_store is not None:
        load_managed_connectors(app.config["CONNECTOR_HUB"], connection_store)

    # Discover schedules even when the background worker is disabled so the
    # operator can inspect/enable them from the application.
    discover_safe_pull_schedules(app, interval_seconds=app.config["AUTO_SYNC_INTERVAL_SECONDS"])

    install_csrf(app)
    install_redirect_safety(app)
    install_auth(app, db_path)
    install_separation_of_duties(app, db_path)
    install_multi_connector_routes(app)
    install_connection_manager(app, connection_store)
    install_connection_health(app, connection_store)
    install_onboarding(app, db_path)
    install_readiness(app)
    app.config["AUTO_SYNC_THREAD"] = start_scheduler_thread(app)
    app.config["AUTO_SYNC_ENABLED"] = app.config["AUTO_SYNC_THREAD"] is not None
    return app


app = create_secure_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("BOOKKEEPER_DEBUG") == "1")
