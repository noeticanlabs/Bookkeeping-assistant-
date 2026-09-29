import pytest

from ai_credential_boundary import AICredentialDomain
from ai_runtime_boundary import AIRuntimeBoundary
from secure_web_app import create_secure_app


def _fake_provider(provider, model, prompt, context, *, credentials):
    assert credentials.get("OPENAI_API_KEY") == "model-secret"
    return "proposal only"


def test_ai_runtime_exposes_only_minimal_capabilities():
    runtime = AIRuntimeBoundary(
        credentials=AICredentialDomain.from_environment({"OPENAI_API_KEY": "model-secret"}),
        provider_invoke=_fake_provider,
    )
    assert runtime.capability_names() == frozenset({"provider_invoke", "ai_provider_credentials"})
    for forbidden in (
        "connector_hub", "connector_factory", "write_connector", "quickbooks",
        "jobber", "stripe", "yardi", "servicetitan", "execute", "bookkeeper",
        "application", "future_connector",
    ):
        with pytest.raises(PermissionError, match="outside the AI runtime boundary"):
            runtime.get_capability(forbidden)


def test_future_connector_is_denied_without_boundary_change():
    runtime = AIRuntimeBoundary(
        credentials=AICredentialDomain.from_environment({}),
        provider_invoke=_fake_provider,
    )
    with pytest.raises(PermissionError):
        runtime.get_capability("NEW_VENDOR_SUPER_WRITE_CONNECTOR")


def test_runtime_invocation_receives_no_connector_capability():
    seen = {}

    def adversarial_provider(provider, model, prompt, context, *, credentials):
        seen["credentials"] = credentials.names()
        seen["context"] = context
        return "try to execute anyway"

    runtime = AIRuntimeBoundary(
        credentials=AICredentialDomain.from_environment({
            "OPENAI_API_KEY": "model-secret",
            "QBO_ACCESS_TOKEN": "write-secret",
        }),
        provider_invoke=adversarial_provider,
    )
    result = runtime.invoke("openai", "test", "execute", {"summary": {"payments": 0}})
    assert result == "try to execute anyway"
    assert seen["credentials"] == frozenset({"OPENAI_API_KEY"})
    assert set(seen["context"]) == {"summary"}


def test_secure_app_wires_ai_to_runtime_not_connector_hub(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "model-secret")
    monkeypatch.setenv("QBO_ACCESS_TOKEN", "write-secret")
    app = create_secure_app(str(tmp_path / "bookkeeper.json"))
    runtime = app.config["GOVERNED_AI_RUNTIME"]
    assert app.config["GOVERNED_AI_INVOKE"] == runtime.invoke
    assert "connector_hub" not in runtime.capability_names()
    assert "QBO_ACCESS_TOKEN" not in runtime.credentials.names()


def test_ai_runtime_object_has_no_connector_fields():
    runtime = AIRuntimeBoundary(
        credentials=AICredentialDomain.from_environment({}),
        provider_invoke=_fake_provider,
    )
    state = vars(runtime)
    assert set(state) == {"credentials", "provider_invoke"}
    assert not any("connector" in name.lower() for name in state)
