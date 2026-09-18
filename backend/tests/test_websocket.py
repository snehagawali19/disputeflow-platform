import pytest
from starlette.testclient import TestClient

from backend.api.routes import app
from backend.core.config import get_settings
from backend.db.session import init_db


@pytest.mark.asyncio
async def test_websocket_unknown_session():
    await init_db()
    client = TestClient(app)
    with client.websocket_connect(f"/ws/missing?api_key={get_settings().api_key}") as ws:
        message = ws.receive_json()
        assert message["event"] == "error"
