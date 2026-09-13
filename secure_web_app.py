"""Authenticated Bookkeeper Assistant entrypoint."""

import os

from auth_web import install_auth
from onboarding import install_onboarding
from readiness import install_readiness
from sod_web import install_separation_of_duties
from sqlite_store import save_bookkeeper
from web_app import create_app


def create_secure_app(data_path: str | None = None, connectors=None):
    app = create_app(data_path, connectors=connectors)
    db_path = app.config["BOOKKEEPER_DATA_PATH"]
    app.config["SAVE_BOOKKEEPER"] = lambda: save_bookkeeper(app.config["BOOKKEEPER"], db_path)
    install_auth(app, db_path)
    install_separation_of_duties(app, db_path)
    install_onboarding(app, db_path)
    install_readiness(app)
    return app


app = create_secure_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("BOOKKEEPER_DEBUG") == "1")
