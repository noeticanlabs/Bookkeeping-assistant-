"""Prevent open redirects from the secure Flask application."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from flask import request


def _same_origin(location: str) -> bool:
    target = urlparse(urljoin(request.host_url, location))
    host = urlparse(request.host_url)
    return (
        target.scheme in {"http", "https"}
        and target.netloc == host.netloc
        and target.username is None
        and target.password is None
    )


def install_redirect_safety(app) -> None:
    @app.after_request
    def enforce_same_origin_redirects(response):
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            if location and not _same_origin(location):
                response.headers["Location"] = "/"
        return response
