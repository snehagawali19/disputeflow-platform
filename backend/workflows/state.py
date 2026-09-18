"""
LangGraph state definition for DisputeFlow.
Adapted from FinancialAnalysisAgent/backend/workflows/state.py
"""

from typing import Annotated, Any, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages


class DisputeFlowState(TypedDict, total=False):
    """State schema shared across all nodes in the DisputeFlow graph."""

    case: dict[str, Any]
    current_stage: Literal[
        "intake",
        "evidence_assembly",
        "strategy_formulation",
        "response_drafting",
        "filing",
        "awaiting_outcome",
        "feedback_loop",
        "human_review",
        "accepted",
        "completed",
        "failed",
    ]
    messages: Annotated[list, add_messages]
    needs_human_approval: bool
    human_decision: Optional[Literal["approve", "reject", "modify"]]
    human_feedback: str
    review_origin: Optional[Literal["evidence", "strategy", "filing", "accept"]]
    resume_target: Optional[str]
    win_probability: float
    retry_count: int
    max_retries: int
    last_error: str
    session_id: str
    run_id: str
    case_version: int
