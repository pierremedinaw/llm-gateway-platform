"""Pruebas de timeout, no reintentos en 400, y compatibilidad de Fase 1."""

import json

import httpx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": "test-key-at-least-16-characters"}
PAYLOAD = {"model": "openai-main", "messages": [{"role": "user", "content": "hello"}]}


def configure(monkeypatch, *, retries: str = "0"):
    monkeypatch.setenv("MODEL_ROUTES", json.dumps({
        "openai-main": {"provider": "openai_compatible", "upstream_model": "example-model"}
    }))
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("MAX_UPSTREAM_RETRIES", retries)
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0")
    get_settings.cache_clear()


def test_timeout_maps_to_504_without_retry(monkeypatch):
    configure(monkeypatch)
    count = []

    def handler(request):
        count.append(1)
        raise httpx.ReadTimeout("test timeout", request=request)

    monkeypatch.setattr("app.providers.make_client", lambda timeout:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout))
    response = client.post("/v1/chat/completions", headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 504
    assert len(count) == 1


def test_400_is_never_retried(monkeypatch):
    configure(monkeypatch, retries="2")
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(400, text="private provider text")

    monkeypatch.setattr("app.providers.make_client", lambda timeout:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout))
    response = client.post("/v1/chat/completions", headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 502
    assert len(count) == 1
    assert "private provider text" not in response.text


def test_old_phase_one_environment_remains_supported(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTES", "{}")
    monkeypatch.setenv("PROVIDER", "mock")
    monkeypatch.setenv("ALLOWED_MODELS", "demo-model")
    get_settings.cache_clear()
    response = client.post("/v1/chat/completions", headers=HEADERS, json={
        "model": "demo-model", "messages": [{"role": "user", "content": "backward compatible"}]
    })
    assert response.status_code == 200
    assert "MOCK" in response.text
