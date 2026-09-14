"""Action-level authority checks for consequential bookkeeping operations.

Endpoint installation order must not determine authorization. Final route
implementations call this layer directly and receive an AuthorityGrant only when
the authenticated user holds the permission assigned to that action.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps

from flask import flash, g, redirect, url_for


ACTION_PERMISSIONS = {
    "sync.run": "sync.run",
    "invoice.issue": "invoice.issue",
}


class AuthorizationDenied(PermissionError):
    pass


@dataclass(frozen=True)
class AuthorityGrant:
    action: str
    permission: str
    user_id: str
    role: str


def authorize_action(app, action: str, user) -> AuthorityGrant:
    """Return a durable-in-request authority fact or raise AuthorizationDenied."""
    permission = ACTION_PERMISSIONS.get(action)
    if permission is None:
        raise ValueError(f"Unknown governed action: {action}")
    permissions = app.config.get("PERMISSION_STORE")
    if user is None or permissions is None or not permissions.user_has(user, permission):
        raise AuthorizationDenied(f"{permission} permission required")
    return AuthorityGrant(action, permission, user.user_id, user.role)


def requires_action(app, action: str):
    """Guard the final executable route implementation for a governed action."""
    if action not in ACTION_PERMISSIONS:
        raise ValueError(f"Unknown governed action: {action}")

    def decorator(view):
        @wraps(view)
        def guarded(*args, **kwargs):
            try:
                grant = authorize_action(app, action, getattr(g, "current_user", None))
            except AuthorizationDenied as exc:
                audit = app.config.get("AUDIT_LOG")
                user = getattr(g, "current_user", None)
                if audit is not None and user is not None:
                    audit.append(
                        "authority.denied",
                        f"ACTION:{action}",
                        {"action": action, "required_permission": ACTION_PERMISSIONS[action], "user_id": user.user_id},
                        actor=f"{user.display_name} [{user.username}] ({user.role})",
                    )
                flash(str(exc), "error")
                return redirect(url_for("dashboard"))
            g.authority_grant = grant
            return view(*args, **kwargs)

        guarded._governed_action = action
        guarded._required_permission = ACTION_PERMISSIONS[action]
        return guarded

    return decorator
