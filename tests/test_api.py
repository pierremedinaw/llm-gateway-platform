"""Pruebas de salud, autenticación, límites y contrato de respuesta."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
KEY = "test-key-at-least-16-characters"
PAYLOAD = {"model": "demo-model", "messages": [{"role": "user", "content": "Hello gateway"}]}


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("x-request-id")


def test_authentication_required() -> None:
    assert client.post("/v1/chat/completions", json=PAYLOAD).status_code == 401
    assert client.post(
        "/v1/chat/completions", json=PAYLOAD, headers={"X-API-Key": "wrong-key"}
    ).status_code == 401


def test_mock_completion() -> None:
    response = client.post(
        "/v1/chat/completions", json=PAYLOAD, headers={"X-API-Key": KEY}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "demo-model"
    assert "Hello gateway" in data["choices"][0]["message"]["content"]


def test_model_allowlist() -> None:
    payload = {**PAYLOAD, "model": "unknown-model"}
    response = client.post(
        "/v1/chat/completions", json=payload, headers={"X-API-Key": KEY}
    )
    assert response.status_code == 400


def test_invalid_payload() -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": []},
        headers={"X-API-Key": KEY},
    )
    assert response.status_code == 422


def test_rate_limit(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("REQUESTS_PER_MINUTE", "2")
    get_settings.cache_clear()
    statuses = [
        client.post("/v1/chat/completions", json=PAYLOAD, headers={"X-API-Key": KEY})
        for _ in range(3)
    ]
    assert [response.status_code for response in statuses] == [200, 200, 429]
    assert "Retry-After" in statuses[2].headers


def test_streaming_not_yet_supported() -> None:
    response = client.post(
        "/v1/chat/completions",
        json={**PAYLOAD, "stream": True},
        headers={"X-API-Key": KEY},
    )
    assert response.status_code == 422
