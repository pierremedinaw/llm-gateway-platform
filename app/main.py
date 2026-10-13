"""API principal del gateway LLM."""

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.models import ChatCompletionRequest
from app.providers import complete_chat
from app.security import authorize

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("llm_gateway")

app = FastAPI(
    title="LLM Gateway Platform",
    version="0.1.0",
    description="Gateway mínimo de LLM con API key, allowlist y rate limiting.",
)


@app.middleware("http")
async def request_tracking(request: Request, call_next):
    # Solo registrar metadatos: nunca prompts, claves o respuestas del modelo.
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
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions", tags=["inference"])
async def chat_completions(
    body: ChatCompletionRequest,
    settings: Settings = Depends(authorize),
) -> dict:
    if body.model not in settings.model_allowlist:
        raise HTTPException(status_code=400, detail="Model not allowed")
    return await complete_chat(body, settings)
