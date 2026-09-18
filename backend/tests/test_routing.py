from backend.agents.strategy_agent import _apply_decision_policy
from backend.models.dispute import DisputeCase, DisputeReasonCode, TransactionData
from backend.workflows.graph import (
    route_after_evidence,
    route_after_filing,
    route_after_human_review,
    route_after_response,
    route_after_strategy,
)


def test_strategy_policy_cannot_contest_without_delivery_proof():
    case = DisputeCase(
        dispute_amount=15000,
        reason_code=DisputeReasonCode.PRODUCT_NOT_RECEIVED,
        transaction=TransactionData(amount=15000),
        evidence_gaps=["Verified delivery confirmation"],
    )
    action, reasoning = _apply_decision_policy(case, "contest", win_prob=0.62, confidence=0.2)
    assert action == "escalate_to_human"
    assert reasoning is not None
    assert "probability alone cannot replace missing factual proof" in reasoning


def test_strategy_routes_to_human_when_flagged():
    assert route_after_strategy({"needs_human_approval": True, "current_stage": "human_review"}) == "human_review"
    assert route_after_strategy({"needs_human_approval": False, "current_stage": "response_drafting"}) == "response_drafting"
    assert route_after_strategy({"current_stage": "failed"}) == "failed_end"


def test_evidence_failure_routes_to_human_review():
    assert route_after_evidence({"needs_human_approval": True, "current_stage": "human_review"}) == "human_review"
    assert route_after_evidence({"current_stage": "strategy_formulation"}) == "strategy_formulation"


def test_human_review_does_not_default_to_approve():
    assert route_after_human_review({}) == "wait"
    assert route_after_human_review({"human_decision": "reject"}) == "accept_case"
    assert route_after_human_review({"human_decision": "modify"}) == "evidence_assembly"
    assert route_after_human_review({"human_decision": "approve", "review_origin": "evidence"}) == "strategy_formulation"
    assert route_after_human_review({"human_decision": "approve", "review_origin": "accept"}) == "accept_case"
    assert route_after_human_review({"human_decision": "approve", "review_origin": "filing"}) == "force_file"
    assert route_after_human_review({"human_decision": "approve", "review_origin": "strategy"}) == "response_drafting"


def test_response_and_filing_routes():
    assert route_after_response({"current_stage": "filing"}) == "filing"
    assert route_after_response({"current_stage": "human_review", "needs_human_approval": True}) == "human_review"
    assert route_after_filing({"current_stage": "awaiting_outcome"}) == "awaiting_outcome"
    assert route_after_filing({"needs_human_approval": True, "current_stage": "human_review"}) == "human_review"
