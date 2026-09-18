"""DisputeFlow application entry point."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn

from backend.api.routes import app
from backend.core.config import get_settings

__all__ = ["app"]


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "backend.api.routes:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.app_env == "development" and not settings.is_sqlite,
        log_level="info",
    )
