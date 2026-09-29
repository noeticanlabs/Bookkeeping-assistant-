"""BK-AI-005: interchangeable proposal-only model providers.

Provider adapters translate the same bounded prompt/context contract into a
provider API call. They receive only AICredentialDomain and return text. They do
not receive bookkeeping state, connectors, authority stores, or execution tools.
"""
from __future__ import annotations

import json
from typing import Any

from ai_credential_boundary import AICredentialDomain


PROPOSAL_ONLY_SYSTEM = (
    "You are a proposal-only bookkeeping assistant. You have no authority to "
    "post, pay, issue, approve, schedule, send, or mutate records. Identify "
    "observations, candidate actions, relationships, and exceptions only."
)


def _user_content(prompt: str, context: dict[str, Any]) -> str:
    return prompt + "\n\nAuthorized context:\n" + json.dumps(context, sort_keys=True, default=str)


def openai_invoke(provider: str, model: str, prompt: str, context: dict[str, Any], *,
                  credentials: AICredentialDomain | None = None) -> str:
    if provider != "openai":
        raise ValueError(f"OpenAI adapter cannot serve provider: {provider}")
    if credentials is None:
        raise RuntimeError("An isolated AI credential domain is required")
    key = credentials.require("OPENAI_API_KEY")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the openai package to run governed OpenAI jobs") from exc
    client = OpenAI(api_key=key)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": PROPOSAL_ONLY_SYSTEM},
            {"role": "user", "content": _user_content(prompt, context)},
        ],
    )
    return getattr(response, "output_text", "") or ""


def anthropic_invoke(provider: str, model: str, prompt: str, context: dict[str, Any], *,
                     credentials: AICredentialDomain | None = None) -> str:
    if provider != "anthropic":
        raise ValueError(f"Anthropic adapter cannot serve provider: {provider}")
    if credentials is None:
        raise RuntimeError("An isolated AI credential domain is required")
    key = credentials.require("ANTHROPIC_API_KEY")
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Install the anthropic package to run governed Anthropic jobs") from exc
    client = anthropic.Anthropic(api_key=key)
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        system=PROPOSAL_ONLY_SYSTEM,
        messages=[{"role": "user", "content": _user_content(prompt, context)}],
    )
    return "\n".join(
        block.text for block in message.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", "")
    )


def governed_provider_invoke(provider: str, model: str, prompt: str, context: dict[str, Any], *,
                             credentials: AICredentialDomain | None = None) -> str:
    adapters = {
        "openai": openai_invoke,
        "anthropic": anthropic_invoke,
    }
    adapter = adapters.get(provider)
    if adapter is None:
        raise ValueError(f"Unsupported governed AI provider: {provider}")
    return adapter(provider, model, prompt, context, credentials=credentials)
