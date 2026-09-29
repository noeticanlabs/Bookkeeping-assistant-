"""BK-AI-004B: capability boundary for AI-facing runtime code.

The governed AI worker is constructed from an explicit, minimal capability set.
Connector hubs, connector factories, write adapters, and arbitrary application
objects are not members of this runtime.  The boundary is allow-list based so a
future connector is excluded by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ai_credential_boundary import AICredentialDomain


@dataclass(frozen=True)
class AIRuntimeBoundary:
    """Minimal capabilities available to an AI provider invocation."""

    credentials: AICredentialDomain
    provider_invoke: Callable[..., str]

    def invoke(self, provider: str, model: str, prompt: str, context: dict[str, Any]) -> str:
        return self.provider_invoke(
            provider,
            model,
            prompt,
            context,
            credentials=self.credentials,
        )

    def capability_names(self) -> frozenset[str]:
        return frozenset({"provider_invoke", "ai_provider_credentials"})

    def get_capability(self, name: str) -> Any:
        if name == "provider_invoke":
            return self.provider_invoke
        if name == "ai_provider_credentials":
            return self.credentials
        raise PermissionError("Capability is outside the AI runtime boundary")
