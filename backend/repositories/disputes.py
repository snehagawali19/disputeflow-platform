"""Case persistence and optimistic concurrency."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import CaseRecord, ProgressEvent
from backend.models.dispute import DisputeCase


class VersionConflict(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DisputeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, session_id: str, case: DisputeCase) -> CaseRecord:
        record = CaseRecord(
            session_id=session_id,
            case_id=case.case_id,
            status=case.status.value,
            pipeline_status="created",
            version=1,
            payload_json=case.model_dump_json(),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get(self, session_id: str) -> CaseRecord | None:
        return await self.session.get(CaseRecord, session_id)

    async def get_or_raise(self, session_id: str) -> CaseRecord:
        record = await self.get(session_id)
        if record is None:
            raise KeyError(session_id)
        return record

    async def list_all(self) -> list[CaseRecord]:
        result = await self.session.scalars(select(CaseRecord).order_by(CaseRecord.created_at.desc()))
        return list(result.all())

    def to_case(self, record: CaseRecord) -> DisputeCase:
        return DisputeCase.model_validate_json(record.payload_json)

    async def save(
        self,
        record: CaseRecord,
        case: DisputeCase,
        pipeline_status: str | None = None,
        expected_version: int | None = None,
        last_error: str = "",
    ) -> CaseRecord:
        if expected_version is not None and record.version != expected_version:
            raise VersionConflict(f"Case version {record.version} != {expected_version}")
        record.payload_json = case.model_dump_json()
        record.status = case.status.value if hasattr(case.status, "value") else str(case.status)
        record.version += 1
        record.updated_at = _utcnow()
        record.last_error = last_error
        if pipeline_status is not None:
            record.pipeline_status = pipeline_status
        await self.session.flush()
        return record

    async def add_event(self, session_id: str, sequence: int, event: dict) -> ProgressEvent:
        row = ProgressEvent(
            session_id=session_id,
            sequence=sequence,
            event_json=json.dumps(event, default=str),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_events(self, session_id: str, after: int = 0) -> list[dict]:
        rows = (
            await self.session.scalars(
                select(ProgressEvent)
                .where(ProgressEvent.session_id == session_id, ProgressEvent.sequence > after)
                .order_by(ProgressEvent.sequence.asc())
            )
        ).all()
        events = []
        for row in rows:
            payload = json.loads(row.event_json)
            payload["sequence"] = row.sequence
            events.append(payload)
        return events
