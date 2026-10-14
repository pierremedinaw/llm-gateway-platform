"""Enrutamiento y adaptadores de LLM con respuesta uniforme y errores saneados."""

import asyncio
from time import time
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.config import ModelRoute, Settings
from app.models import ChatCompletionRequest

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/"


def make_client(timeout: float) -> httpx.AsyncClient:
    """Constructor desacoplado para simular redes externas en pruebas."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def _normalize(
    public_model: str,
    text: str,
    *,
    finish_reason: str = "stop",
    usage: dict | None = None,
    upstream_id: str | None = None,
) -> dict:
    result = {
        "id": upstream_id or f"chatcmpl-gw-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time()),
        "model": public_model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish_reason,
        }],
    }
    if usage is not None:
        result["usage"] = usage
    return result


async def _post_json(
    url: str, headers: dict[str, str], payload: dict, settings: Settings
) -> dict:
    """Reintenta únicamente errores transitorios; nunca propaga cuerpos del proveedor."""
    async with make_client(settings.provider_timeout_seconds) as client:
        for attempt in range(settings.max_upstream_retries + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                if attempt < settings.max_upstream_retries:
                    await asyncio.sleep(settings.retry_backoff_seconds * (2**attempt))
                    continue
                raise HTTPException(status_code=504, detail="Upstream provider timeout") from exc
            except httpx.RequestError as exc:
                if attempt < settings.max_upstream_retries:
                    await asyncio.sleep(settings.retry_backoff_seconds * (2**attempt))
                    continue
                raise HTTPException(status_code=502, detail="Upstream provider unavailable") from exc

            if response.status_code >= 400:
                if response.status_code in RETRYABLE_STATUSES and attempt < settings.max_upstream_retries:
                    await asyncio.sleep(settings.retry_backoff_seconds * (2**attempt))
                    continue
                if response.status_code == 429:
                    raise HTTPException(status_code=429, detail="Upstream rate limit")
                if response.status_code in {401, 403}:
                    raise HTTPException(status_code=502, detail="Upstream authentication failed")
                raise HTTPException(status_code=502, detail="Upstream provider error")

            try:
                result = response.json()
            except ValueError as exc:
                raise HTTPException(status_code=502, detail="Invalid upstream JSON") from exc
            if not isinstance(result, dict):
                raise HTTPException(status_code=502, detail="Unexpected upstream response")
            return result

    raise HTTPException(status_code=502, detail="Upstream unavailable")


async def _openai_compatible(
    body: ChatCompletionRequest, settings: Settings, route: ModelRoute
) -> dict:
    key = settings.openai_api_key or settings.provider_api_key
    if not key:
        raise HTTPException(status_code=503, detail="Provider credentials not configured")

    payload = body.model_dump(exclude_none=True)
    payload["model"] = route.upstream_model
    result = await _post_json(
        f"{settings.provider_base_url}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        payload,
        settings,
    )
    try:
        choice = result["choices"][0]
        message = choice["message"]
        text = message["content"]
        if not isinstance(text, str):
            raise TypeError("Response does not contain text")
        finish = choice.get("finish_reason") or "stop"
        raw_usage = result.get("usage")
        usage = None
        if isinstance(raw_usage, dict):
            # No inventar tokens: solo reproducir contadores enteros reportados.
            keys = ("prompt_tokens", "completion_tokens", "total_tokens")
            if all(type(raw_usage.get(k)) is int and raw_usage[k] >= 0 for k in keys):
                usage = {k: raw_usage[k] for k in keys}
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="Unexpected upstream response") from exc
    return _normalize(body.model, text, finish_reason=str(finish), usage=usage,
                      upstream_id=result.get("id") if isinstance(result.get("id"), str) else None)


def _gemini_payload(body: ChatCompletionRequest) -> dict:
    system_parts = [msg.content for msg in body.messages if msg.role == "system"]
    contents = [
        {
            "role": "model" if msg.role == "assistant" else "user",
            "parts": [{"text": msg.content}],
        }
        for msg in body.messages if msg.role != "system"
    ]
    if not contents:
        raise HTTPException(status_code=422, detail="Gemini requires a user or assistant message")
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": body.temperature,
            "maxOutputTokens": body.max_tokens,
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    return payload


async def _gemini(
    body: ChatCompletionRequest, settings: Settings, route: ModelRoute
) -> dict:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Provider credentials not configured")
    result = await _post_json(
        f"{GEMINI_URL}{route.upstream_model}:generateContent",
        {"x-goog-api-key": settings.gemini_api_key},
        _gemini_payload(body), settings,
    )
    try:
        candidate = result["candidates"][0]
        parts = candidate["content"]["parts"]
        text = "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
        if not text:
            raise ValueError("No textual response")
        reason = candidate.get("finishReason", "STOP")
        finish = "length" if reason == "MAX_TOKENS" else "stop"
        tokens = result.get("usageMetadata", {})
        usage = None
        if isinstance(tokens, dict):
            inp = tokens.get("promptTokenCount")
            out = tokens.get("candidatesTokenCount")
            total = tokens.get("totalTokenCount")
            if all(type(v) is int and v >= 0 for v in (inp, out, total)):
                usage = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": total}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Unexpected upstream response") from exc
    return _normalize(body.model, text, finish_reason=finish, usage=usage)


async def complete_chat(body: ChatCompletionRequest, settings: Settings) -> dict:
    """El cliente elige solo un alias permitido, jamás un endpoint o credencial."""
    route = settings.route_for(body.model)
    if route is None:
        raise HTTPException(status_code=400, detail="Model not allowed")

    if route.provider == "mock":
        last_user_text = next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            "No user message supplied",
        )
        return _normalize(body.model, f"[MOCK - no real model inference] Received: {last_user_text}")
    if route.provider == "openai_compatible":
        return await _openai_compatible(body, settings, route)
    if route.provider == "gemini":
        return await _gemini(body, settings, route)
    raise HTTPException(status_code=503, detail="Provider not configured")
