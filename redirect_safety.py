"""Prevent open redirects while permitting explicit OAuth authorization hosts."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from flask import request


TRUSTED_OAUTH_REDIRECTS = {
    "xero_authorize": {"login.xero.com"},
    "quickbooks_authorize": {"appcenter.intuit.com"},
    "jobber_authorize": {"api.getjobber.com"},
}


def _same_origin(location: str) -> bool:
    target = urlparse(urljoin(request.host_url, location))
    host = urlparse(request.host_url)
    return (
        target.scheme in {"http", "https"}
        and target.netloc == host.netloc
        and target.username is None
        and target.password is None
    )


def _trusted_oauth_redirect(location: str) -> bool:
    allowed_hosts = TRUSTED_OAUTH_REDIRECTS.get(request.endpoint or "", set())
    if not allowed_hosts:
        return False
    target = urlparse(location)
    return (
        target.scheme == "https"
        and target.hostname in allowed_hosts
        and target.username is None
        and target.password is None
    )


def install_redirect_safety(app) -> None:
    @app.after_request
    def enforce_safe_redirects(response):
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            if location and not (_same_origin(location) or _trusted_oauth_redirect(location)):
                response.headers["Location"] = "/"
        return response
