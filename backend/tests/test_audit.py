import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.models import Base
from backend.models.dispute import DisputeCase
from backend.repositories.disputes import DisputeRepository
from backend.utils.audit import AuditTrail


@pytest.mark.asyncio
async def test_audit_chain_valid_and_tamper_detectable(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/audit.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as session:
        repo = DisputeRepository(session)
        case = DisputeCase(dispute_amount=10)
        await repo.create("s1", case)
        trail = AuditTrail(session, case.case_id, "s1")
        await trail.add_entry("intake", "completed", {"ok": True})
        await trail.add_entry("strategy", "completed", {"ok": True})
        await session.commit()
        assert await trail.verify_chain() is True
        row = (await session.scalars(case_query(session, case.case_id))).first()
        row.entry_hash = "deadbeef"
        await session.commit()
        assert await trail.verify_chain() is False


def case_query(session, case_id):
    from sqlalchemy import select
    from backend.db.models import AuditEntry

    return select(AuditEntry).where(AuditEntry.case_id == case_id)
