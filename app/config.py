"""Configuración del servicio exclusivamente mediante variables de entorno."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # No guardar claves reales en el repositorio.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gateway_api_key: str = Field(min_length=16)
    provider: Literal["mock", "openai_compatible"] = "mock"
    provider_api_key: str = ""
    provider_base_url: str = "https://api.openai.com/v1"
    allowed_models: str = "demo-model"
    requests_per_minute: int = Field(default=20, ge=1, le=10000)
    provider_timeout_seconds: float = Field(default=20, gt=0, le=120)

    @field_validator("provider_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        # HTTPS por defecto; HTTP únicamente para pruebas locales.
        if not (
            value.startswith("https://")
            or value.startswith("http://localhost:")
            or value.startswith("http://127.0.0.1:")
        ):
            raise ValueError("provider_base_url debe utilizar HTTPS (salvo localhost)")
        return value.rstrip("/")

    @property
    def model_allowlist(self) -> set[str]:
        return {item.strip() for item in self.allowed_models.split(",") if item.strip()}


@lru_cache

def get_settings() -> Settings:
    # La caché evita leer el entorno en cada solicitud.
    return Settings()
