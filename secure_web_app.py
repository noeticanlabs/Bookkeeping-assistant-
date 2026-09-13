"""Authenticated Bookkeeper Assistant entrypoint."""

import os
import secrets

from auth_web import install_auth
from csrf import install_csrf
from onboarding import install_onboarding
from readiness import install_readiness
from sod_web import install_separation_of_duties
from sqlite_store import save_bookkeeper
from web_app import create_app


def _configure_session_secret(app) -> None:
    configured = os.environ.get("BOOKKEEPER_SECRET", "").strip()
    production = os.environ.get("BOOKKEEPER_PRODUCTION") == "1"
    if production and not configured:
        raise RuntimeError("BOOKKEEPER_SECRET is required when BOOKKEEPER_PRODUCTION=1")
    # Local development may run without configuration, but never with a known
    # static fallback. Restarting the process invalidates these dev sessions.
    app.secret_key = configured or secrets.token_hex(32)
    app.config["SESSION_SECRET_CONFIGURED"] = bool(configured)
    app.config["BOOKKEEPER_PRODUCTION"] = production


def create_secure_app(data_path: str | None = None, connectors=None):
    app = create_app(data_path, connectors=connectors)
    _configure_session_secret(app)
    db_path = app.config["BOOKKEEPER_DATA_PATH"]
    app.config["SAVE_BOOKKEEPER"] = lambda: save_bookkeeper(app.config["BOOKKEEPER"], db_path)
    install_csrf(app)
    install_auth(app, db_path)
    install_separation_of_duties(app, db_path)
    install_onboarding(app, db_path)
    install_readiness(app)
    return app


app = create_secure_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("BOOKKEEPER_DEBUG") == "1")
