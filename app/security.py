"""Autenticación y rate limiting local de ventana fija."""

from hmac import compare_digest
from threading import Lock
from time import monotonic

from fastapi import Depends, Header, HTTPException

from app.config import Settings, get_settings


class FixedWindowLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def allow(self, identity: str, limit: int) -> tuple[bool, int]:
        now = monotonic()
        with self._lock:
            opened_at, count = self._buckets.get(identity, (now, 0))
            if now - opened_at >= 60:
                opened_at, count = now, 0
            if count >= limit:
                return False, max(1, int(60 - (now - opened_at)) + 1)
            self._buckets[identity] = (opened_at, count + 1)
            return True, 0

    def reset(self) -> None:
        # Solo para aislar pruebas automatizadas.
        with self._lock:
            self._buckets.clear()


limiter = FixedWindowLimiter()


async def authorize(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Settings:
    # compare_digest evita comparar secretos carácter por carácter.
    if not x_api_key or not compare_digest(x_api_key, settings.gateway_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    allowed, retry_after = limiter.allow("primary-key", settings.requests_per_minute)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return settings
