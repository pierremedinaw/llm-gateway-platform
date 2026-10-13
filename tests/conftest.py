"""Aislamiento del entorno de prueba y límites de solicitudes."""

import pytest

from app.config import get_settings
from app.security import limiter


@pytest.fixture(autouse=True)
def settings_for_tests(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key-at-least-16-characters")
    monkeypatch.setenv("PROVIDER", "mock")
    monkeypatch.setenv("ALLOWED_MODELS", "demo-model")
    monkeypatch.setenv("REQUESTS_PER_MINUTE", "20")
    get_settings.cache_clear()
    limiter.reset()
    yield
    get_settings.cache_clear()
    limiter.reset()
