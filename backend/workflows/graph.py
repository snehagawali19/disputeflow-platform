"""
DisputeFlow LangGraph workflow with durable human-review interrupts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from loguru import logger

from backend.agents import (
    evidence_assembly_agent,
    feedback_loop_agent,
    filing_agent,
    intake_agent,
    response_drafting_agent,
    strategy_agent,
)
from backend.agents.case_io import dump_case, load_case
from backend.models.dispute import DisputeStatus, FilingRecord, utcnow
from backend.workflows.state import DisputeFlowState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def intake_node(state: DisputeFlowState) -> dict:
    logger.info("[INTAKE] Processing case")
    return await intake_agent.run(state)


async def evidence_node(state: DisputeFlowState) -> dict:
    return await evidence_assembly_agent.run(state)


async def strategy_node(state: DisputeFlowState) -> dict:
    return await strategy_agent.run(state)


async def response_node(state: DisputeFlowState) -> dict:
    return await response_drafting_agent.run(state)


async def filing_node(state: DisputeFlowState) -> dict:
    return await filing_agent.run(state)


async def feedback_node(state: DisputeFlowState) -> dict:
    return await feedback_loop_agent.run(state)


async def human_review_node(state: DisputeFlowState) -> dict:
    """Record the HITL gate and pause via LangGraph interrupt() until /decide resumes."""
    case = load_case(state)
    origin = state.get("review_origin") or "strategy"
    already = any(entry.get("origin") == origin and entry.get("open") for entry in case.human_approvals)
    if not already:
        case.human_approvals.append(
            {
                "requested_at": _now(),
                "origin": origin,
                "stage": state.get("current_stage"),
                "win_probability": state.get("win_probability", 0),
                "risk_level": case.risk_level.value,
                "open": True,
            }
        )
    case.status = DisputeStatus.PENDING_HUMAN_REVIEW
    case.updated_at = utcnow()

    decision = state.get("human_decision")
    feedback = state.get("human_feedback") or ""
    if not decision:
        logger.info(f"[HUMAN REVIEW] Case {case.case_id} awaiting approval from {origin}")
        resume_payload = interrupt(
            {
                "origin": origin,
                "case_id": case.case_id,
                "win_probability": state.get("win_probability", 0),
                "risk_level": case.risk_level.value,
            }
        )
        if isinstance(resume_payload, dict):
            decision = resume_payload.get("decision", "approve")
            feedback = resume_payload.get("feedback", feedback)
        else:
            decision = str(resume_payload)

    for entry in reversed(case.human_approvals):
        if entry.get("open"):
            entry["open"] = False
            entry["decision"] = decision
            break
    case.updated_at = utcnow()
    logger.info(f"[HUMAN REVIEW] Case {case.case_id} decision={decision} origin={origin}")
    return {
        "case": dump_case(case),
        "human_decision": decision,
        "human_feedback": feedback,
        "needs_human_approval": False,
        "current_stage": "human_review",
        "review_origin": origin,
    }


async def accept_node(state: DisputeFlowState) -> dict:
    case = load_case(state)
    case.status = DisputeStatus.ACCEPTED
    case.updated_at = utcnow()
    if case.human_approvals:
        case.human_approvals[-1]["open"] = False
        case.human_approvals[-1]["decision"] = state.get("human_decision")
    return {"case": dump_case(case), "current_stage": "accepted", "needs_human_approval": False}


async def force_file_node(state: DisputeFlowState) -> dict:
    """Human override after a rejected simulated filing."""
    import uuid

    case = load_case(state)
    case.filing = FilingRecord(
        filed_at=utcnow(),
        filing_channel="dashboard",
        confirmation_reference=f"DF-SIM-HUMAN-{uuid.uuid4().hex[:8].upper()}",
        status="submitted",
        simulated=True,
        notes="Human override of simulated filing validation. Not an external processor confirmation.",
    )
    case.status = DisputeStatus.AWAITING_OUTCOME
    case.updated_at = utcnow()
    if case.human_approvals:
        case.human_approvals[-1]["open"] = False
        case.human_approvals[-1]["decision"] = "approve"
    return {"case": dump_case(case), "current_stage": "awaiting_outcome", "needs_human_approval": False}


async def awaiting_outcome_node(state: DisputeFlowState) -> dict:
    case = load_case(state)
    case.status = DisputeStatus.AWAITING_OUTCOME
    return {"case": dump_case(case), "current_stage": "awaiting_outcome", "needs_human_approval": False}


def route_after_intake(state: DisputeFlowState) -> str:
    return "failed_end" if state.get("current_stage") == "failed" else "evidence_assembly"


def route_after_evidence(state: DisputeFlowState) -> str:
    if state.get("current_stage") == "failed":
        return "failed_end"
    if state.get("needs_human_approval") or state.get("current_stage") == "human_review":
        return "human_review"
    return "strategy_formulation"


def route_after_strategy(state: DisputeFlowState) -> str:
    if state.get("current_stage") == "failed":
        return "failed_end"
    if state.get("needs_human_approval") or state.get("current_stage") == "human_review":
        return "human_review"
    return "response_drafting"


def route_after_human_review(state: DisputeFlowState) -> str:
    decision = state.get("human_decision")
    if decision in (None, ""):
        return "wait"
    if decision == "reject":
        return "accept_case"
    if decision == "modify":
        return "evidence_assembly"
    origin = state.get("review_origin") or "strategy"
    if origin == "evidence":
        return "strategy_formulation"
    if origin == "accept":
        return "accept_case"
    if origin == "filing":
        return "force_file"
    return "response_drafting"


def route_after_response(state: DisputeFlowState) -> str:
    if state.get("current_stage") == "failed":
        return "failed_end"
    if state.get("needs_human_approval") or state.get("current_stage") == "human_review":
        return "human_review"
    return "filing"


def route_after_filing(state: DisputeFlowState) -> str:
    if state.get("current_stage") == "failed":
        return "failed_end"
    if state.get("needs_human_approval") or state.get("current_stage") == "human_review":
        return "human_review"
    return "awaiting_outcome"


def build_dispute_flow_graph(checkpointer: Any | None = None, include_feedback: bool = False):
    workflow = StateGraph(DisputeFlowState)
    workflow.add_node("intake", intake_node)
    workflow.add_node("evidence_assembly", evidence_node)
    workflow.add_node("strategy_formulation", strategy_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("response_drafting", response_node)
    workflow.add_node("filing", filing_node)
    workflow.add_node("force_file", force_file_node)
    workflow.add_node("awaiting_outcome", awaiting_outcome_node)
    workflow.add_node("accept_case", accept_node)
    if include_feedback:
        workflow.add_node("feedback_loop", feedback_node)

    workflow.set_entry_point("intake")
    workflow.add_conditional_edges(
        "intake",
        route_after_intake,
        {"evidence_assembly": "evidence_assembly", "failed_end": END},
    )
    workflow.add_conditional_edges(
        "evidence_assembly",
        route_after_evidence,
        {
            "strategy_formulation": "strategy_formulation",
            "human_review": "human_review",
            "failed_end": END,
        },
    )
    workflow.add_conditional_edges(
        "strategy_formulation",
        route_after_strategy,
        {"human_review": "human_review", "response_drafting": "response_drafting", "failed_end": END},
    )
    workflow.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "wait": END,
            "response_drafting": "response_drafting",
            "strategy_formulation": "strategy_formulation",
            "evidence_assembly": "evidence_assembly",
            "force_file": "force_file",
            "accept_case": "accept_case",
        },
    )
    workflow.add_conditional_edges(
        "response_drafting",
        route_after_response,
        {"filing": "filing", "human_review": "human_review", "failed_end": END},
    )
    workflow.add_conditional_edges(
        "filing",
        route_after_filing,
        {"awaiting_outcome": "awaiting_outcome", "human_review": "human_review", "failed_end": END},
    )
    workflow.add_edge("force_file", "awaiting_outcome")
    workflow.add_edge("awaiting_outcome", END)
    workflow.add_edge("accept_case", END)
    if include_feedback:
        workflow.add_edge("feedback_loop", END)

    saver = checkpointer or MemorySaver()
    return workflow.compile(checkpointer=saver)


def build_feedback_graph(checkpointer: Any | None = None):
    workflow = StateGraph(DisputeFlowState)
    workflow.add_node("feedback_loop", feedback_node)
    workflow.set_entry_point("feedback_loop")
    workflow.add_edge("feedback_loop", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_dispute_flow_graph()
    return _graph


dispute_flow_graph = None


def __getattr__(name: str):
    if name == "dispute_flow_graph":
        return get_graph()
    raise AttributeError(name)
