"""Tests for the Ollama provider wiring (local daemon and Ollama Cloud)."""


def _build(monkeypatch, host, key):
    from app import config
    from app.agents import team_agent

    monkeypatch.setattr(config, "MODEL_PROVIDER", "ollama")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_PARAMS", {})
    monkeypatch.setattr(config, "MODEL_ID", "gpt-oss:120b-cloud")
    monkeypatch.setattr(config, "OLLAMA_HOST", host)
    monkeypatch.setenv("OLLAMA_API_KEY", key) if key else monkeypatch.delenv(
        "OLLAMA_API_KEY", raising=False
    )
    return team_agent._model()


def test_ollama_local_daemon_no_auth(monkeypatch):
    model = _build(monkeypatch, "http://localhost:11434", "")
    assert type(model).__name__ == "OllamaModel"
    assert model.host == "http://localhost:11434"
    assert model.client_args == {}
    assert model.config["model_id"] == "gpt-oss:120b-cloud"


def test_ollama_cloud_direct_with_key(monkeypatch):
    model = _build(monkeypatch, "https://ollama.com", "sk-ollama-test")
    assert model.host == "https://ollama.com"
    assert model.client_args["headers"]["Authorization"] == "Bearer sk-ollama-test"


def test_ollama_default_model_id():
    from app.config import PROVIDERS

    assert PROVIDERS["ollama"]["default_model"] == "gpt-oss:120b-cloud"
