"""Configuración y aislamiento de las pruebas automatizadas."""

import pytest

from app.config import Settings, get_settings
from app.security import limiter


@pytest.fixture(autouse=True)
def settings_for_tests(monkeypatch):
    # Desactiva la lectura de .env únicamente durante cada prueba.
    # Evita que MODEL_ROUTES del entorno local se combine
    # con los modelos definidos por los tests.
    monkeypatch.setattr(
        Settings,
        "model_config",
        {**Settings.model_config, "env_file": None},
    )

    # Configuración independiente y reproducible para pruebas.
    monkeypatch.setenv(
        "GATEWAY_API_KEY",
        "test-key-at-least-16-characters",
    )
    monkeypatch.setenv("PROVIDER", "mock")
    monkeypatch.setenv("ALLOWED_MODELS", "demo-model")
    monkeypatch.setenv("REQUESTS_PER_MINUTE", "20")

    # Invalida la configuración almacenada en caché.
    get_settings.cache_clear()

    # Restablece el rate limiter antes de cada prueba.
    limiter.reset()

    yield

    # Limpieza posterior a cada prueba.
    get_settings.cache_clear()
    limiter.reset()