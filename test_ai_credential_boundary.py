import pytest

from ai_credential_boundary import AICredentialDomain
from governed_ai import openai_invoke


def test_ai_domain_allowlists_provider_secrets_only():
    env = {
        "OPENAI_API_KEY": "ai-openai-secret",
        "ANTHROPIC_API_KEY": "ai-anthropic-secret",
        "QBO_ACCESS_TOKEN": "qbo-write-secret",
        "STRIPE_SECRET_KEY": "stripe-write-secret",
        "JOBBER_ACCESS_TOKEN": "jobber-write-secret",
        "FUTURE_VENDOR_WRITE_TOKEN": "future-write-secret",
    }
    domain = AICredentialDomain.from_environment(env)
    assert domain.names() == frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
    assert domain.get("OPENAI_API_KEY") == "ai-openai-secret"
    assert domain.get("QBO_ACCESS_TOKEN") is None
    assert domain.get("STRIPE_SECRET_KEY") is None
    assert domain.get("JOBBER_ACCESS_TOKEN") is None
    assert domain.get("FUTURE_VENDOR_WRITE_TOKEN") is None


def test_unknown_future_execution_secret_is_excluded_by_default():
    domain = AICredentialDomain.from_environment({
        "OPENAI_API_KEY": "ai-secret",
        "SOMETHING_NOT_YET_IN_THE_CODE_WRITE_CREDENTIAL": "danger",
    })
    assert domain.names() == frozenset({"OPENAI_API_KEY"})
    assert domain.get("SOMETHING_NOT_YET_IN_THE_CODE_WRITE_CREDENTIAL") is None


def test_ai_domain_refuses_operational_credential_requests():
    domain = AICredentialDomain.from_environment({
        "OPENAI_API_KEY": "ai-secret",
        "QBO_ACCESS_TOKEN": "qbo-secret",
    })
    with pytest.raises(PermissionError, match="outside the AI provider domain"):
        domain.require("QBO_ACCESS_TOKEN")


def test_openai_invoke_requires_explicit_isolated_domain(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-ai-secret")
    monkeypatch.setenv("QBO_ACCESS_TOKEN", "ambient-write-secret")
    with pytest.raises(RuntimeError, match="isolated AI credential domain"):
        openai_invoke("openai", "test-model", "review", {})


def test_domain_never_exposes_complete_source_mapping():
    source = {
        "OPENAI_API_KEY": "ai-secret",
        "QBO_ACCESS_TOKEN": "qbo-secret",
        "FUTURE_VENDOR_WRITE_TOKEN": "future-secret",
    }
    domain = AICredentialDomain.from_environment(source)
    rendered = repr(domain)
    assert "qbo-secret" not in rendered
    assert "future-secret" not in rendered
