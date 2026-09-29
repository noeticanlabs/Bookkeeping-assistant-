"""BK-AI-004A: explicit credential boundary for model-provider workers.

AI workers receive an allow-listed credential snapshot.  Operational connector
credentials are never inherited into that snapshot.  This module intentionally
uses an allow-list rather than a deny-list so future connector secrets remain
excluded by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


AI_PROVIDER_SECRET_NAMES = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
})


@dataclass(frozen=True)
class AICredentialDomain:
    """Immutable, allow-listed credential view for an AI provider worker."""

    _values: tuple[tuple[str, str], ...]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "AICredentialDomain":
        values = tuple(sorted(
            (name, str(environ[name]))
            for name in AI_PROVIDER_SECRET_NAMES
            if environ.get(name)
        ))
        return cls(values)

    def get(self, name: str) -> str | None:
        if name not in AI_PROVIDER_SECRET_NAMES:
            return None
        return dict(self._values).get(name)

    def require(self, name: str) -> str:
        if name not in AI_PROVIDER_SECRET_NAMES:
            raise PermissionError("Credential is outside the AI provider domain")
        value = self.get(name)
        if not value:
            raise RuntimeError(f"{name} is required for governed AI jobs")
        return value

    def names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self._values)
