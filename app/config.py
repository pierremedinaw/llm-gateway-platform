"""Configuración segura y rutas estáticas de modelos mediante variables de entorno."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelRoute(BaseModel):
    """Asocia un alias público con un proveedor y un modelo real."""

    provider: Literal["mock", "openai_compatible", "gemini"]
    upstream_model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")


class Settings(BaseSettings):
    # Los secretos se leen desde .env (que está excluido del control de versiones).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gateway_api_key: str = Field(min_length=16)
    # Compatibilidad con la Fase 1 cuando MODEL_ROUTES está vacío.
    provider: Literal["mock", "openai_compatible", "gemini"] = "mock"
    provider_api_key: str = ""  # Compatibilidad con configuración OpenAI de Fase 1.
    provider_base_url: str = "https://api.openai.com/v1"
    allowed_models: str = "demo-model"

    # Cada alias puede usar un proveedor diferente en la misma instancia.
    model_routes: dict[str, ModelRoute] = Field(default_factory=dict)
    openai_api_key: str = ""
    gemini_api_key: str = ""
    requests_per_minute: int = Field(default=20, ge=1, le=10000)
    provider_timeout_seconds: float = Field(default=20, gt=0, le=120)
    # Por defecto no reintentamos: evita cargos involuntarios por peticiones duplicadas.
    max_upstream_retries: int = Field(default=0, ge=0, le=2)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=2)

    @field_validator("provider_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        # Un operador configura el upstream; nunca se acepta una URL enviada por clientes.
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider_base_url no admite usuario, contraseña ni query")
        local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if not parsed.hostname or (parsed.scheme != "https" and not local):
            raise ValueError("provider_base_url debe ser HTTPS (salvo localhost)")
        return value.rstrip("/")

    @field_validator("model_routes")
    @classmethod
    def validate_routes(cls, routes: dict[str, ModelRoute]) -> dict[str, ModelRoute]:
        for alias, route in routes.items():
            if not alias or len(alias) > 100 or not all(
                char.isalnum() or char in "-_." for char in alias
            ):
                raise ValueError("Los alias solo admiten letras, números, -, _ y .")
            # La URL nativa de Gemini se construye con el ID en un segmento de ruta.
            if route.provider == "gemini" and "/" in route.upstream_model:
                raise ValueError("El modelo Gemini no admite barras")
        return routes

    @property
    def model_allowlist(self) -> set[str]:
        if self.model_routes:
            return set(self.model_routes)
        return {item.strip() for item in self.allowed_models.split(",") if item.strip()}

    def route_for(self, public_model: str) -> ModelRoute | None:
        if public_model not in self.model_allowlist:
            return None
        if self.model_routes:
            return self.model_routes[public_model]
        return ModelRoute(provider=self.provider, upstream_model=public_model)


@lru_cache
def get_settings() -> Settings:
    # Caché de settings durante la vida del proceso; reiniciar al cambiar .env.
    return Settings()
