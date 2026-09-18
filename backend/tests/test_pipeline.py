"""End-to-end graph tests with mocked LLM structured output."""

from backend.models.agent_outputs import EvidenceResult, FilingResult, IntakeResult, ResponseResult, StrategyResult
from backend.models.dispute import DisputeCase, DisputePhase, DisputeReasonCode, RiskLevel, TransactionData
from backend.workflows.graph import build_dispute_flow_graph
from backend.workflows.state import DisputeFlowState
from backend.agents.case_io import dump_case, load_case


def create_test_case() -> DisputeCase:
    return DisputeCase(
        dispute_amount=2000,
        dispute_currency="INR",
        reason_code=DisputeReasonCode.GENERAL,
        dispute_phase=DisputePhase.CHARGEBACK,
        risk_level=RiskLevel.LOW,
        transaction=TransactionData(
            amount=2000,
            currency="INR",
            payment_method="card",
            merchant_name="TestMerchant",
            customer_email="test@example.com",
            avs_match=True,
            cvv_verified=True,
            three_ds_authenticated=True,
            ip_address="203.0.113.42",
            shipping_address="Warehouse Lane",
        ),
    )


async def _fake_structured(schema, *_args, **_kwargs):
    if schema is IntakeResult:
        return IntakeResult(
            reason_code="general",
            risk_level="low",
            intake_summary="Low-value general dispute with complete checkout verification.",
            dispute_phase="chargeback",
        )
    if schema is EvidenceResult:
        return EvidenceResult(overall_evidence_strength="strong", evidence_gaps=[])
    if schema is StrategyResult:
        return StrategyResult(
            recommended_action="contest",
            reasoning="Verification flags and a transaction record support a contest.",
            key_arguments=["AVS matched", "CVV verified"],
            evidence_strength="strong",
            estimated_effort_hours=1,
            risk_of_escalation=0.1,
        )
    if schema is ResponseResult:
        return ResponseResult(
            rebuttal_letter="We contest this dispute and rely only on the supplied processor verification flags and transaction record. " * 5,
            evidence_summary="- Transaction record\n- AVS\n- CVV",
            word_count=90,
        )
    if schema is FilingResult:
        return FilingResult(filing_approved=True, validation_notes="complete", missing_requirements=[], filing_confidence=0.8)
    raise AssertionError(schema)


async def test_full_pipeline_auto_approve(monkeypatch):
    monkeypatch.setattr("backend.agents.intake_agent.invoke_structured", _fake_structured)
    monkeypatch.setattr("backend.agents.evidence_assembly_agent.invoke_structured", _fake_structured)
    monkeypatch.setattr("backend.agents.strategy_agent.invoke_structured", _fake_structured)
    monkeypatch.setattr("backend.agents.response_drafting_agent.invoke_structured", _fake_structured)
    monkeypatch.setattr("backend.agents.filing_agent.invoke_structured", _fake_structured)

    case = create_test_case()
    graph = build_dispute_flow_graph()
    initial_state: DisputeFlowState = {
        "case": dump_case(case),
        "current_stage": "intake",
        "messages": [],
        "needs_human_approval": False,
        "human_decision": None,
        "human_feedback": "",
        "win_probability": 0.0,
        "retry_count": 0,
        "max_retries": 3,
        "last_error": "",
        "session_id": "test-001",
    }
    result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": "test-001"}})
    loaded = load_case(result)
    assert loaded.intake_summary
    assert len(loaded.evidence_items) > 0
    assert loaded.strategy is not None
    assert result["current_stage"] in {"awaiting_outcome", "human_review"}
    if result["current_stage"] == "awaiting_outcome":
        assert loaded.filing and loaded.filing.simulated
        assert loaded.filing.confirmation_reference.startswith("DF-SIM-")
