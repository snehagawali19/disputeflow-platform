"""Async database engine and session helpers."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import get_settings
from backend.db.models import Base

_settings = get_settings()
_engine_kwargs: dict = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
}
if _settings.is_sqlite:
    _engine_kwargs["connect_args"] = _settings.sqlite_connect_args
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(_settings.async_database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _configure_sqlite(conn) -> None:
    await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    await conn.exec_driver_sql("PRAGMA busy_timeout=30000")
    await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")


async def init_db() -> None:
    async with engine.begin() as conn:
        if _settings.is_sqlite:
            await _configure_sqlite(conn)
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
