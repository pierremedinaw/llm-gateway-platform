"""Pruebas sin internet: proveedores reales simulados mediante MockTransport."""

import json

import httpx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
KEY = "test-key-at-least-16-characters"
HEADERS = {"X-API-Key": KEY}
BASE_PAYLOAD = {"messages": [{"role": "user", "content": "What is MLOps?"}]}


def configure(monkeypatch, routes: dict, **extra: str) -> None:
    monkeypatch.setenv("MODEL_ROUTES", json.dumps(routes))
    for name, value in extra.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def inject_transport(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        "app.providers.make_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout),
    )


def test_list_models_contains_only_public_aliases(monkeypatch) -> None:
    configure(monkeypatch, {
        "demo-model": {"provider": "mock", "upstream_model": "demo-model"},
        "gemini-main": {"provider": "gemini", "upstream_model": "gemini-2.5-flash"},
    })
    result = client.get("/v1/models", headers=HEADERS)
    assert result.status_code == 200
    assert [item["id"] for item in result.json()["data"]] == ["demo-model", "gemini-main"]
    assert "gemini_api_key" not in result.text


def test_routes_support_mock_and_openai_in_one_gateway(monkeypatch) -> None:
    configure(monkeypatch, {
        "demo-model": {"provider": "mock", "upstream_model": "demo-model"},
        "openai-main": {"provider": "openai_compatible", "upstream_model": "gpt-4.1-mini"},
    }, OPENAI_API_KEY="fake-test-token")

    def upstream(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fake-test-token"
        assert json.loads(request.content)["model"] == "gpt-4.1-mini"
        return httpx.Response(200, json={
            "id": "upstream-id", "choices": [{"message": {"role": "assistant", "content": "MLOps means..."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        })

    inject_transport(monkeypatch, upstream)
    mock = client.post("/v1/chat/completions", headers=HEADERS, json={**BASE_PAYLOAD, "model": "demo-model"})
    real = client.post("/v1/chat/completions", headers=HEADERS, json={**BASE_PAYLOAD, "model": "openai-main"})
    assert mock.status_code == 200 and "MOCK" in mock.text
    assert real.status_code == 200
    assert real.json()["model"] == "openai-main"
    assert real.json()["usage"]["total_tokens"] == 12
    assert real.json()["choices"][0]["message"]["content"] == "MLOps means..."


def test_gemini_native_maps_roles_system_and_usage(monkeypatch) -> None:
    configure(monkeypatch, {"gemini-main": {"provider": "gemini", "upstream_model": "gemini-2.5-flash"}},
              GEMINI_API_KEY="not-a-real-key")

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "not-a-real-key"
        assert request.url.path.endswith("/models/gemini-2.5-flash:generateContent")
        body = json.loads(request.content)
        assert body["systemInstruction"]["parts"][0]["text"] == "Respond concisely"
        assert body["contents"][0]["role"] == "user"
        assert body["contents"][1]["role"] == "model"
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "A reproducible ML"}, {"text": " lifecycle"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4, "totalTokenCount": 14},
        })

    inject_transport(monkeypatch, upstream)
    result = client.post("/v1/chat/completions", headers=HEADERS, json={
        "model": "gemini-main", "messages": [
            {"role": "system", "content": "Respond concisely"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ],
    })
    assert result.status_code == 200
    assert result.json()["choices"][0]["message"]["content"] == "A reproducible ML lifecycle"
    assert result.json()["usage"]["total_tokens"] == 14


def test_missing_provider_key_fails_closed(monkeypatch) -> None:
    configure(monkeypatch, {"gemini-main": {"provider": "gemini", "upstream_model": "gemini-2.5-flash"}},
              GEMINI_API_KEY="")
    result = client.post("/v1/chat/completions", headers=HEADERS,
                         json={**BASE_PAYLOAD, "model": "gemini-main"})
    assert result.status_code == 503
    assert "credentials" in result.text


def test_no_retry_by_default_on_rate_limit(monkeypatch) -> None:
    configure(monkeypatch, {"openai-main": {"provider": "openai_compatible", "upstream_model": "example-model"}},
              OPENAI_API_KEY="fake", MAX_UPSTREAM_RETRIES="0")
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={"error": "sensitive upstream details"})

    inject_transport(monkeypatch, upstream)
    result = client.post("/v1/chat/completions", headers=HEADERS,
                         json={**BASE_PAYLOAD, "model": "openai-main"})
    assert result.status_code == 429
    assert len(calls) == 1
    assert "sensitive upstream details" not in result.text


def test_opt_in_retry_recovers_from_503(monkeypatch) -> None:
    configure(monkeypatch, {"openai-main": {"provider": "openai_compatible", "upstream_model": "example-model"}},
              OPENAI_API_KEY="fake", MAX_UPSTREAM_RETRIES="1", RETRY_BACKOFF_SECONDS="0")
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Recovered"}}]})

    inject_transport(monkeypatch, upstream)
    result = client.post("/v1/chat/completions", headers=HEADERS,
                         json={**BASE_PAYLOAD, "model": "openai-main"})
    assert result.status_code == 200
    assert len(calls) == 2


def test_non_retryable_authentication_failure(monkeypatch) -> None:
    configure(monkeypatch, {"openai-main": {"provider": "openai_compatible", "upstream_model": "example-model"}},
              OPENAI_API_KEY="fake", MAX_UPSTREAM_RETRIES="2", RETRY_BACKOFF_SECONDS="0")
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="secret error")

    inject_transport(monkeypatch, upstream)
    result = client.post("/v1/chat/completions", headers=HEADERS,
                         json={**BASE_PAYLOAD, "model": "openai-main"})
    assert result.status_code == 502
    assert len(calls) == 1
    assert "secret error" not in result.text


def test_upstream_bad_response_returns_sanitized_error(monkeypatch) -> None:
    configure(monkeypatch, {"gemini-main": {"provider": "gemini", "upstream_model": "gemini-2.5-flash"}},
              GEMINI_API_KEY="fake")
    inject_transport(monkeypatch, lambda request: httpx.Response(200, json={"candidates": []}))
    result = client.post("/v1/chat/completions", headers=HEADERS,
                         json={**BASE_PAYLOAD, "model": "gemini-main"})
    assert result.status_code == 502


def test_unlisted_model_is_rejected_before_network(monkeypatch) -> None:
    configure(monkeypatch, {"demo-model": {"provider": "mock", "upstream_model": "demo-model"}})
    result = client.post("/v1/chat/completions", headers=HEADERS,
                         json={**BASE_PAYLOAD, "model": "gemini-main"})
    assert result.status_code == 400
