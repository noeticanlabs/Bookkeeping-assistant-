import sys
from types import SimpleNamespace

import pytest

from ai_credential_boundary import AICredentialDomain
from ai_providers import anthropic_invoke, governed_provider_invoke
from ai_runtime_boundary import AIRuntimeBoundary


def test_anthropic_requires_isolated_credentials():
    with pytest.raises(RuntimeError, match="isolated AI credential domain"):
        anthropic_invoke("anthropic", "claude-test", "review", {})


def test_anthropic_cannot_use_openai_or_execution_credentials():
    domain = AICredentialDomain.from_environment({
        "OPENAI_API_KEY": "openai-secret",
        "QBO_ACCESS_TOKEN": "write-secret",
    })
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        anthropic_invoke("anthropic", "claude-test", "review", {}, credentials=domain)


def test_anthropic_adapter_uses_only_bounded_prompt_context_and_api_key(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="proposal only")])

    class FakeAnthropicClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropicClient))
    domain = AICredentialDomain.from_environment({
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "QBO_ACCESS_TOKEN": "qbo-secret",
        "FUTURE_VENDOR_WRITE_TOKEN": "future-secret",
    })
    output = anthropic_invoke(
        "anthropic", "claude-test", "classify and propose", {"summary": {"invoices": 2}},
        credentials=domain,
    )
    assert output == "proposal only"
    assert captured["api_key"] == "anthropic-secret"
    serialized = repr(captured)
    assert "qbo-secret" not in serialized
    assert "future-secret" not in serialized
    assert "tools" not in captured
    assert "mcp_servers" not in captured
    assert "Authorized context" in captured["messages"][0]["content"]


def test_provider_router_rejects_unknown_provider():
    domain = AICredentialDomain.from_environment({})
    with pytest.raises(ValueError, match="Unsupported governed AI provider"):
        governed_provider_invoke("future-unknown", "model", "prompt", {}, credentials=domain)


def test_anthropic_remains_inside_same_runtime_capability_boundary():
    seen = {}

    def fake_router(provider, model, prompt, context, *, credentials):
        seen.update(provider=provider, names=credentials.names(), context=context)
        return "candidate action"

    runtime = AIRuntimeBoundary(
        AICredentialDomain.from_environment({
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "STRIPE_SECRET_KEY": "write-secret",
        }),
        fake_router,
    )
    assert runtime.invoke("anthropic", "claude-test", "review", {"summary": {}}) == "candidate action"
    assert seen["provider"] == "anthropic"
    assert seen["names"] == frozenset({"ANTHROPIC_API_KEY"})
    assert runtime.capability_names() == frozenset({"provider_invoke", "ai_provider_credentials"})


def test_anthropic_wrong_adapter_provider_is_rejected():
    domain = AICredentialDomain.from_environment({"ANTHROPIC_API_KEY": "secret"})
    with pytest.raises(ValueError, match="cannot serve provider"):
        anthropic_invoke("openai", "claude-test", "review", {}, credentials=domain)
