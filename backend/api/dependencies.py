"""Auth and request dependencies."""

from __future__ import annotations

import hmac
import time
from collections import defaultdict

from fastapi import Header, HTTPException, WebSocket, status

from backend.core.config import get_settings


_hits: dict[str, list[float]] = defaultdict(list)


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    settings = get_settings()
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    _enforce_rate_limit(x_api_key)
    return x_api_key


def _enforce_rate_limit(key: str) -> None:
    now = time.time()
    window = 60
    limit = get_settings().rate_limit_per_minute
    recent = [ts for ts in _hits[key] if now - ts < window]
    if len(recent) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    recent.append(now)
    _hits[key] = recent


async def authenticate_websocket(websocket: WebSocket) -> str:
    settings = get_settings()
    token = websocket.query_params.get("api_key") or websocket.headers.get("x-api-key")
    if not token or not hmac.compare_digest(token, settings.api_key):
        await websocket.close(code=4401)
        raise HTTPException(status_code=401, detail="Invalid API key")
    await websocket.accept()
    return token
