"""API FastAPI para inferencia multi-proveedor controlada."""

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.models import ChatCompletionRequest
from app.providers import complete_chat
from app.security import authorize

logger = logging.getLogger("llm_gateway")

app = FastAPI(
    title="LLM Gateway Platform",
    version="0.3.0",
    description="Gateway de LLM con autenticación, rutas estáticas y resiliencia opcional.",
)


@app.middleware("http")
async def request_tracking(request: Request, call_next):
    # Registrar solo metadatos: nunca prompts, respuestas ni claves.
    request_id = uuid4().hex
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_id=%s unexpected_error", request_id)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    logger.info(
        "request_id=%s method=%s path=%s status=%s latency_ms=%s",
        request_id, request.method, request.url.path, response.status_code, elapsed_ms,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models", tags=["inference"])
async def list_models(settings: Settings = Depends(authorize)) -> dict:
    """Expone los alias públicos autorizados, no claves ni URLs privadas."""
    return {
        "object": "list",
        "data": [
            {"id": alias, "object": "model", "provider": settings.route_for(alias).provider}
            for alias in sorted(settings.model_allowlist)
        ],
    }


@app.post("/v1/chat/completions", tags=["inference"])
async def chat_completions(
    body: ChatCompletionRequest,
    settings: Settings = Depends(authorize),
) -> dict:
    return await complete_chat(body, settings)
