"""Small dependency-free CSRF protection for the secure Flask app."""

from __future__ import annotations

import html
import re
import secrets
from hmac import compare_digest

from flask import abort, request, session

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_FORM_RE = re.compile(r"(<form\b[^>]*>)", re.IGNORECASE)


def install_csrf(app) -> None:
    """Protect unsafe requests and inject a token into rendered HTML forms.

    Tests may disable protection by setting TESTING=True (the default Flask test
    behavior), or explicitly force it with CSRF_PROTECTION_ENABLED=True.
    """

    def enabled() -> bool:
        configured = app.config.get("CSRF_PROTECTION_ENABLED")
        if configured is not None:
            return bool(configured)
        return not bool(app.config.get("TESTING"))

    def token() -> str:
        value = session.get("_csrf_token")
        if not value:
            value = secrets.token_urlsafe(32)
            session["_csrf_token"] = value
        return value

    app.jinja_env.globals["csrf_token"] = token
    app.config["CSRF_TOKEN"] = token

    @app.before_request
    def verify_csrf():
        if not enabled() or request.method not in _UNSAFE_METHODS:
            return None
        expected = session.get("_csrf_token")
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not expected or not supplied or not compare_digest(str(expected), str(supplied)):
            abort(400, description="Invalid or missing CSRF token")
        return None

    @app.after_request
    def inject_csrf(response):
        if not enabled() or response.status_code >= 400:
            return response
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type.lower():
            return response
        body = response.get_data(as_text=True)
        if "<form" not in body.lower():
            return response
        field = f'<input type="hidden" name="csrf_token" value="{html.escape(token(), quote=True)}">'
        body = _FORM_RE.sub(lambda match: match.group(1) + field, body)
        response.set_data(body)
        response.headers["Content-Length"] = str(len(response.get_data()))
        return response
