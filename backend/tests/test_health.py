from backend.api.routes import app
from httpx import ASGITransport, AsyncClient


async def test_root_and_live():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/")
        live = await client.get("/health/live")
        assert root.status_code == 200
        assert "DisputeFlow" in root.json()["name"]
        assert live.json()["status"] == "ok"
