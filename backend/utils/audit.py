"""
Transactional hash-chained audit trail.
Inspired by agentguard-cb's signed audit trail.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.db.models import AuditEntry
from backend.utils.redact import sanitize_trace


GENESIS = "GENESIS"


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def _sign(value: str) -> str:
    secret = get_settings().secret_key.encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


class AuditTrail:
    def __init__(self, session: AsyncSession, case_id: str, session_id: str):
        self.session = session
        self.case_id = case_id
        self.session_id = session_id

    async def add_entry(self, agent: str, action: str, data: dict[str, Any]) -> dict[str, Any]:
        last = await self.session.scalar(
            select(AuditEntry)
            .where(AuditEntry.case_id == self.case_id)
            .order_by(AuditEntry.sequence.desc())
            .limit(1)
        )
        previous_hash = last.entry_hash if last else GENESIS
        sequence = (last.sequence + 1) if last else 1
        payload = {
            "case_id": self.case_id,
            "session_id": self.session_id,
            "sequence": sequence,
            "agent": agent,
            "action": action,
            "data": sanitize_trace(data),
            "previous_hash": previous_hash,
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        signature = _sign(digest)
        row = AuditEntry(
            case_id=self.case_id,
            session_id=self.session_id,
            sequence=sequence,
            agent=agent,
            action=action,
            data_json=json.dumps(payload["data"], default=str),
            previous_hash=previous_hash,
            entry_hash=digest,
            hmac_signature=signature,
        )
        self.session.add(row)
        await self.session.flush()
        return {
            "sequence": sequence,
            "hash": digest,
            "hmac": signature,
            "agent": agent,
            "action": action,
        }

    async def verify_chain(self) -> bool:
        rows = (
            await self.session.scalars(
                select(AuditEntry)
                .where(AuditEntry.case_id == self.case_id)
                .order_by(AuditEntry.sequence.asc())
            )
        ).all()
        previous = GENESIS
        for row in rows:
            payload = {
                "case_id": row.case_id,
                "session_id": row.session_id,
                "sequence": row.sequence,
                "agent": row.agent,
                "action": row.action,
                "data": json.loads(row.data_json),
                "previous_hash": row.previous_hash,
            }
            expected = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
            if row.previous_hash != previous or row.entry_hash != expected:
                return False
            if not hmac.compare_digest(row.hmac_signature, _sign(row.entry_hash)):
                return False
            previous = row.entry_hash
        return True


async def next_event_sequence(session: AsyncSession, session_id: str) -> int:
    current = await session.scalar(
        select(func.max(ProgressEvent.sequence)).where(ProgressEvent.session_id == session_id)
    )
    return (current or 0) + 1


from backend.db.models import ProgressEvent  # noqa: E402
