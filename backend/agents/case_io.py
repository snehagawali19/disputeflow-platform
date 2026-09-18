"""Helpers for converting LangGraph state case payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.models.dispute import DisputeCase
from backend.workflows.state import DisputeFlowState


def load_case(state: DisputeFlowState) -> DisputeCase:
    case = state.get("case")
    if isinstance(case, DisputeCase):
        return case
    if isinstance(case, dict):
        return DisputeCase.model_validate(case)
    raise TypeError("State is missing a valid case payload")


def dump_case(case: DisputeCase) -> dict[str, Any]:
    return case.model_dump(mode="json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_trace(agent: str) -> dict[str, Any]:
    return {"agent": agent, "started_at": now_iso(), "status": "running"}
