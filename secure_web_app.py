"""Authenticated Bookkeeper Assistant entrypoint.

Use this module for real company operation. `web_app.py` remains the bare workflow app
used by compatibility tests; this entrypoint installs authentication and authorization.
"""

import os

from auth_web import install_auth
from sqlite_store import save_bookkeeper
from web_app import create_app


def create_secure_app(data_path: str | None = None, connectors=None):
    app = create_app(data_path, connectors=connectors)
    db_path = app.config["BOOKKEEPER_DATA_PATH"]
    app.config["SAVE_BOOKKEEPER"] = lambda: save_bookkeeper(app.config["BOOKKEEPER"], db_path)
    install_auth(app, db_path)
    return app


app = create_secure_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("BOOKKEEPER_DEBUG") == "1")
