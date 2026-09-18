"""Durable workflow runner with HITL resume and progress events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.types import Command
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.case_io import dump_case, load_case
from backend.core.config import get_settings
from backend.models.dispute import DisputeCase, DisputeStatus
from backend.repositories.disputes import DisputeRepository
from backend.utils.audit import AuditTrail
from backend.workflows.graph import (
    accept_node,
    awaiting_outcome_node,
    evidence_node,
    filing_node,
    force_file_node,
    get_graph,
    response_node,
    strategy_node,
)
from backend.workflows.state import DisputeFlowState


class WorkflowError(Exception):
    pass


class InvalidTransition(WorkflowError):
    pass


def initial_state(case: DisputeCase, session_id: str) -> DisputeFlowState:
    return {
        "case": dump_case(case),
        "current_stage": "intake",
        "messages": [],
        "needs_human_approval": False,
        "human_decision": None,
        "human_feedback": "",
        "review_origin": None,
        "resume_target": None,
        "win_probability": 0.0,
        "retry_count": 0,
        "max_retries": 3,
        "last_error": "",
        "session_id": session_id,
        "run_id": str(uuid.uuid4()),
        "case_version": 1,
    }


def _needs_response_after_approve(resume_state: DisputeFlowState | None) -> bool:
    if not resume_state:
        return False
    decision = resume_state.get("human_decision")
    origin = resume_state.get("review_origin") or "strategy"
    return decision == "approve" and origin == "strategy"


class WorkflowService:
    def __init__(self, session: AsyncSession, broadcaster):
        self.session = session
        self.repo = DisputeRepository(session)
        self.broadcaster = broadcaster
        self.graph = get_graph()

    async def _persist_event(self, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        events = await self.repo.list_events(session_id, after=0)
        sequence = (events[-1]["sequence"] if events else 0) + 1
        event = {**event, "sequence": sequence}
        await self.repo.add_event(session_id, sequence, event)
        await self.broadcaster(session_id, event)
        return event

    async def _save_case(self, record, case: DisputeCase, pipeline_status: str, last_error: str = "") -> None:
        await self.repo.save(record, case, pipeline_status=pipeline_status, last_error=last_error)
        audit = AuditTrail(self.session, case.case_id, record.session_id)
        await audit.add_entry("workflow", pipeline_status, {"status": case.status.value, "error": last_error})
        await self.session.commit()

    async def _emit_stage_completed(
        self,
        session_id: str,
        record,
        node_name: str,
        node_output: dict[str, Any],
    ) -> tuple[DisputeCase, str, bool]:
        latest = load_case(node_output)
        stage = node_output.get("current_stage", node_name)
        needs_human = bool(node_output.get("needs_human_approval"))
        pipeline = "awaiting_human_review" if needs_human or stage == "human_review" else stage
        if needs_human or stage == "human_review":
            origin = node_output.get("review_origin") or "strategy"
            if not any(entry.get("open") and entry.get("origin") == origin for entry in latest.human_approvals):
                latest.human_approvals.append(
                    {
                        "requested_at": datetime.now(timezone.utc).isoformat(),
                        "origin": origin,
                        "stage": node_name,
                        "win_probability": node_output.get("win_probability", 0.0),
                        "risk_level": latest.risk_level.value,
                        "open": True,
                    }
                )
            latest.status = DisputeStatus.PENDING_HUMAN_REVIEW
        await self._save_case(record, latest, pipeline, node_output.get("last_error", ""))
        await self._persist_event(
            session_id,
            {
                "event": "stage_completed",
                "stage": node_name,
                "next_stage": stage,
                "case_status": latest.status.value,
                "needs_human_approval": needs_human,
            },
        )
        return latest, stage, needs_human

    def _config(self, session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}}

    async def start(self, session_id: str) -> dict[str, Any]:
        record = await self.repo.get_or_raise(session_id)
        case = self.repo.to_case(record)
        if record.pipeline_status in {"running", "awaiting_human_review"}:
            raise InvalidTransition("Pipeline already started")
        if case.status not in {DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW}:
            if record.pipeline_status not in {"created", "failed"}:
                raise InvalidTransition(f"Cannot start from status {case.status}")

        await self._persist_event(session_id, {"event": "pipeline_started", "stage": "intake"})
        record.pipeline_status = "running"
        await self.session.commit()
        state = initial_state(case, session_id)
        return await self._stream(session_id, record, state, resume=False)

    async def decide(
        self, session_id: str, decision: str, feedback: str = "", automatic: bool = False
    ) -> dict[str, Any]:
        record = await self.repo.get_or_raise(session_id)
        if record.pipeline_status != "awaiting_human_review":
            case = self.repo.to_case(record)
            already_decided = any(
                entry.get("decision") == decision and not entry.get("open")
                for entry in case.human_approvals
            )
            if already_decided:
                return {"status": record.pipeline_status, "case": case, "idempotent": True}
            raise InvalidTransition("Case is not awaiting human review")
        case = self.repo.to_case(record)
        origin = "strategy"
        if case.human_approvals:
            origin = case.human_approvals[-1].get("origin") or "strategy"
            case.human_approvals[-1]["automatic"] = automatic
        resume_state: DisputeFlowState = {
            "case": dump_case(case),
            "human_decision": decision,  # type: ignore[typeddict-item]
            "human_feedback": feedback,
            "needs_human_approval": False,
            "review_origin": origin,  # type: ignore[typeddict-item]
            "current_stage": "human_review",
            "session_id": session_id,
            "messages": [],
            "win_probability": case.strategy.win_probability if case.strategy else 0.0,
            "retry_count": 0,
            "max_retries": 3,
            "last_error": "",
        }
        await self._persist_event(
            session_id, {"event": "human_decision", "decision": decision, "automatic": automatic}
        )
        record.pipeline_status = "running"
        await self.session.commit()

        config = self._config(session_id)
        resume_payload = {"decision": decision, "feedback": feedback}
        try:
            snapshot = self.graph.get_state(config)
        except Exception:
            snapshot = None

        if snapshot and snapshot.values and snapshot.next:
            return await self._stream(
                session_id,
                record,
                Command(resume=resume_payload),
                resume=True,
                resume_state=resume_state,
            )
        return await self._fallback_resume(session_id, record, resume_state)

    async def record_outcome(self, session_id: str, outcome: str, reason: str) -> DisputeCase:
        record = await self.repo.get_or_raise(session_id)
        case = self.repo.to_case(record)
        if case.status in {DisputeStatus.WON, DisputeStatus.LOST, DisputeStatus.ACCEPTED}:
            raise InvalidTransition("Outcome already recorded for this case")
        if case.status not in {DisputeStatus.AWAITING_OUTCOME, DisputeStatus.FILED}:
            raise InvalidTransition("Outcome can only be recorded after simulated filing")
        case.actual_outcome = outcome  # type: ignore[assignment]
        case.outcome_reason = reason
        if outcome == "won":
            case.status = DisputeStatus.WON
        elif outcome == "lost":
            case.status = DisputeStatus.LOST
        else:
            case.status = DisputeStatus.ACCEPTED
        from backend.agents.feedback_loop_agent import run as feedback_run
        from backend.services.learning import maybe_retrain_from_real_outcomes, persist_training_outcome

        result = await feedback_run({"case": dump_case(case), "session_id": session_id})
        case = load_case(result)
        await persist_training_outcome(self.session, case)
        await maybe_retrain_from_real_outcomes(self.session)
        await self._save_case(record, case, "completed")
        await self._persist_event(session_id, {"event": "pipeline_completed", "outcome": outcome})
        await self.session.commit()
        return case

    async def _stream(
        self,
        session_id: str,
        record,
        state: DisputeFlowState | Command | None,
        resume: bool,
        resume_state: DisputeFlowState | None = None,
    ) -> dict[str, Any]:
        config = self._config(session_id)
        latest = self.repo.to_case(record)
        hit_response_drafting = False
        try:
            stream = self.graph.astream(state, config=config)
            async for event in stream:
                if "__interrupt__" in event:
                    record.pipeline_status = "awaiting_human_review"
                    await self._save_case(record, latest, "awaiting_human_review")
                    await self.session.commit()
                    if get_settings().app_env != "test" and get_settings().auto_approve_human_review:
                        await self._persist_event(
                            session_id,
                            {
                                "event": "default_approval_applied",
                                "decision": "approve",
                                "automatic": True,
                                "reason": "Default approval enabled",
                            },
                        )
                        return await self.decide(
                            session_id,
                            "approve",
                            "Automatically approved by the configured default.",
                            automatic=True,
                        )
                    return {"status": "awaiting_human_review", "case": latest}

                for node_name, node_output in event.items():
                    if not isinstance(node_output, dict) or "case" not in node_output:
                        continue
                    if node_name == "response_drafting":
                        hit_response_drafting = True
                    latest, stage, needs_human = await self._emit_stage_completed(
                        session_id, record, node_name, node_output
                    )
                    if (needs_human or stage == "human_review") and not resume:
                        record.pipeline_status = "awaiting_human_review"
                        await self.session.commit()
                        if get_settings().app_env != "test" and get_settings().auto_approve_human_review:
                            await self._persist_event(
                                session_id,
                                {
                                    "event": "default_approval_applied",
                                    "decision": "approve",
                                    "automatic": True,
                                    "reason": "Default approval enabled",
                                },
                            )
                            return await self.decide(
                                session_id,
                                "approve",
                                "Automatically approved by the configured default.",
                                automatic=True,
                            )
                        return {"status": "awaiting_human_review", "case": latest}
                    if stage in {"awaiting_outcome", "accepted", "completed", "failed"}:
                        terminal = "awaiting_outcome" if stage == "awaiting_outcome" else stage
                        record.pipeline_status = terminal
                        await self.session.commit()
                        await self._persist_event(session_id, {"event": "pipeline_completed", "stage": terminal})
                        return {"status": terminal, "case": latest}

            if resume and _needs_response_after_approve(resume_state) and not hit_response_drafting:
                if latest.response is None:
                    return await self._fallback_resume(session_id, record, resume_state)  # type: ignore[arg-type]

            record.pipeline_status = latest.status.value
            await self._save_case(record, latest, record.pipeline_status)
            return {"status": record.pipeline_status, "case": latest}
        except Exception as exc:
            logger.exception(f"Pipeline error for {session_id}")
            latest.error_log.append(str(exc))
            latest.status = DisputeStatus.FAILED
            await self._save_case(record, latest, "failed", str(exc))
            await self._persist_event(session_id, {"event": "pipeline_failed", "error": str(exc)})
            raise

    async def _run_fallback_nodes(
        self,
        session_id: str,
        record,
        current: DisputeFlowState,
        steps: list[tuple[str, Any]],
    ) -> DisputeFlowState:
        for node_name, runner in steps:
            output = await runner(current)
            current = {**current, **output}
            latest, stage, needs_human = await self._emit_stage_completed(session_id, record, node_name, output)
            if needs_human or stage == "human_review":
                return current
            if stage in {"awaiting_outcome", "accepted", "completed", "failed"}:
                await self._persist_event(session_id, {"event": "pipeline_completed", "stage": stage})
                return current
        return current

    async def _fallback_resume(self, session_id: str, record, state: DisputeFlowState) -> dict[str, Any]:
        decision = state.get("human_decision")
        origin = state.get("review_origin") or "strategy"
        current = dict(state)
        if decision == "reject" or (decision == "approve" and origin == "accept"):
            current = await self._run_fallback_nodes(session_id, record, current, [("accept_case", accept_node)])
        elif decision == "modify":
            from backend.workflows.graph import human_review_node

            steps: list[tuple[str, Any]] = [
                ("evidence_assembly", evidence_node),
                ("strategy_formulation", strategy_node),
            ]
            current = await self._run_fallback_nodes(session_id, record, current, steps)
            if current.get("needs_human_approval"):
                current = await self._run_fallback_nodes(
                    session_id, record, current, [("human_review", human_review_node)]
                )
            elif not current.get("needs_human_approval"):
                tail: list[tuple[str, Any]] = [("response_drafting", response_node)]
                current = await self._run_fallback_nodes(session_id, record, current, tail)
                if not current.get("needs_human_approval"):
                    current = await self._run_fallback_nodes(session_id, record, current, [("filing", filing_node)])
                    if current.get("current_stage") == "awaiting_outcome":
                        current = await self._run_fallback_nodes(
                            session_id, record, current, [("awaiting_outcome", awaiting_outcome_node)]
                        )
        elif origin == "filing":
            current = await self._run_fallback_nodes(session_id, record, current, [("force_file", force_file_node)])
        else:
            current = await self._run_fallback_nodes(
                session_id, record, current, [("response_drafting", response_node)]
            )
            if not current.get("needs_human_approval"):
                current = await self._run_fallback_nodes(session_id, record, current, [("filing", filing_node)])
                if current.get("current_stage") == "awaiting_outcome":
                    current = await self._run_fallback_nodes(
                        session_id, record, current, [("awaiting_outcome", awaiting_outcome_node)]
                    )

        latest = load_case(current)
        stage = current.get("current_stage", "unknown")
        needs_human = bool(current.get("needs_human_approval"))
        pipeline = "awaiting_human_review" if needs_human or stage == "human_review" else stage
        record.pipeline_status = pipeline
        await self._save_case(record, latest, pipeline)
        await self.session.commit()
        return {"status": pipeline, "case": latest}
