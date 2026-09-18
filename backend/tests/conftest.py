from __future__ import annotations

import os
import tempfile

os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-123456789012"
os.environ["API_KEY"] = "test-api-key-123456789012"
os.environ["ALLOW_HEURISTIC_MODEL"] = "true"
os.environ["LLM_PROVIDER"] = "groq"
os.environ["GROQ_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mktemp(suffix='.db')}"

from backend.core.config import get_settings

get_settings.cache_clear()

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.db.session import init_db
from backend.api.routes import app


@pytest_asyncio.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.fixture
def api_headers():
    return {"X-API-Key": get_settings().api_key}
