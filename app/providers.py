"""Adaptadores de inferencia: demostración local o proveedor compatible con OpenAI."""

from time import time
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.models import ChatCompletionRequest


async def complete_chat(body: ChatCompletionRequest, settings: Settings) -> dict:
    if settings.provider == "mock":
        # Modo reproducible sin llamadas externas ni coste por tokens.
        last_user_text = next(
            (message.content for message in reversed(body.messages) if message.role == "user"),
            "No user message supplied",
        )
        return {
            "id": f"chatcmpl-demo-{uuid4().hex}",
            "object": "chat.completion",
            "created": int(time()),
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"[MOCK - no real model inference] Received: {last_user_text}",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    if not settings.provider_api_key:
        raise HTTPException(status_code=503, detail="Provider credentials not configured")

    # Nunca aceptar una URL externa directamente desde el usuario final.
    url = f"{settings.provider_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.provider_api_key}"}
    payload = body.model_dump(exclude_none=True)

    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream provider timeout") from exc
    except httpx.HTTPStatusError as exc:
        # No propagar cuerpos externos: podrían revelar información sensible.
        if exc.response.status_code == 429:
            raise HTTPException(status_code=429, detail="Upstream rate limit") from exc
        raise HTTPException(status_code=502, detail="Upstream provider error") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Upstream provider unavailable") from exc

    if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
        raise HTTPException(status_code=502, detail="Unexpected provider response")
    return result
