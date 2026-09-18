import pytest

from backend.models.agent_outputs import (
    EvidenceResult,
    FeedbackResult,
    FilingResult,
    IntakeResult,
    ResponseResult,
    StrategyResult,
)


def _intake(**kwargs):
    data = dict(
        reason_code="product_not_received",
        risk_level="low",
        intake_summary="Low value product-not-received dispute with card checkout facts.",
        dispute_phase="chargeback",
        classification_notes="ok",
        urgency_flags=[],
    )
    data.update(kwargs)
    return IntakeResult(**data)


@pytest.mark.asyncio
async def test_create_list_and_unauthorized(client, api_headers, monkeypatch):
    denied = await client.post("/disputes", json={"dispute_amount": 100})
    assert denied.status_code == 401
    created = await client.post(
        "/disputes",
        json={
            "dispute_amount": 2000,
            "reason_code": "general",
            "merchant_name": "Acme",
            "avs_match": True,
            "cvv_verified": True,
        },
        headers=api_headers,
    )
    assert created.status_code == 200
    listed = await client.get("/disputes", headers=api_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["currency"] == "INR"
    with_artifact = await client.post(
        "/disputes",
        json={
            "dispute_amount": 500,
            "reason_code": "product_not_received",
            "shipping_address": "Warehouse Lane",
            "artifacts": [
                {"evidence_type": "delivery_proof", "title": "POD", "content": "Signed by recipient 2026-01-02"}
            ],
        },
        headers=api_headers,
    )
    assert with_artifact.status_code == 200
    detail = await client.get(f"/disputes/{with_artifact.json()['session_id']}", headers=api_headers)
    types = {item["evidence_type"] for item in detail.json()["case"]["evidence_items"]}
    assert "delivery_proof" in types


@pytest.mark.asyncio
async def test_model_status_is_transparent(client, api_headers):
    response = await client.get("/model/status", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] in {"xgboost", "heuristic fallback"}
    assert "note" in body


@pytest.mark.asyncio
async def test_pipeline_low_value_and_outcome(client, api_headers, monkeypatch):
    async def fake_structured(schema, *_args, **_kwargs):
        if schema.__name__ == "IntakeResult":
            return _intake()
        if schema.__name__ == "EvidenceResult":
            return EvidenceResult(scores=[], evidence_gaps=[], overall_evidence_strength="moderate")
        if schema.__name__ == "StrategyResult":
            return StrategyResult(
                recommended_action="contest",
                reasoning="Enough supplied checkout evidence to contest this low-value case.",
                key_arguments=["Transaction record exists"],
                evidence_strength="moderate",
                estimated_effort_hours=1,
                risk_of_escalation=0.2,
            )
        if schema.__name__ == "ResponseResult":
            return ResponseResult(
                rebuttal_letter="We contest this dispute based on the supplied transaction record and verification flags. " * 4,
                evidence_summary="- Transaction record",
                word_count=80,
            )
        if schema.__name__ == "FilingResult":
            return FilingResult(filing_approved=True, validation_notes="ok", missing_requirements=[], filing_confidence=0.7)
        if schema.__name__ == "FeedbackResult":
            return FeedbackResult(lessons_learned=["Recorded outcome for evaluation"], outcome_analysis="Synthetic test")
        raise AssertionError(schema)

    monkeypatch.setattr("backend.agents.intake_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.evidence_assembly_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.strategy_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.response_drafting_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.filing_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.feedback_loop_agent.invoke_structured", fake_structured)

    created = await client.post(
        "/disputes",
        json={
            "dispute_amount": 2000,
            "reason_code": "general",
            "merchant_name": "Acme",
            "avs_match": True,
            "cvv_verified": True,
            "shipping_address": "1 Test Street",
        },
        headers=api_headers,
    )
    session_id = created.json()["session_id"]
    started = await client.post(f"/disputes/{session_id}/start", headers=api_headers)
    assert started.status_code == 200
    body = started.json()
    assert body["status"] in {"awaiting_outcome", "awaiting_human_review"}
    if body["status"] == "awaiting_human_review":
        decided = await client.post(
            f"/disputes/{session_id}/decide",
            json={"decision": "approve"},
            headers=api_headers,
        )
        assert decided.status_code == 200
        detail = await client.get(f"/disputes/{session_id}", headers=api_headers)
        case = detail.json()["case"]
        assert case.get("response") is not None, "approve after strategy HITL must produce a rebuttal"
        assert any(t.get("agent") == "response_drafting" for t in case.get("agent_trace", []))
    detail = await client.get(f"/disputes/{session_id}", headers=api_headers)
    case_status = detail.json()["case"]["status"]
    if case_status in {"awaiting_outcome", "filed"}:
        outcome = await client.post(
            f"/disputes/{session_id}/outcome",
            json={"outcome": "won", "feedback": "issuer reversed"},
            headers=api_headers,
        )
        assert outcome.status_code == 200
        assert outcome.json()["outcome"] == "won"
        again = await client.post(
            f"/disputes/{session_id}/outcome",
            json={"outcome": "lost", "feedback": "duplicate"},
            headers=api_headers,
        )
        assert again.status_code == 409
    duplicate = await client.post(f"/disputes/{session_id}/start", headers=api_headers)
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_hitl_approve_runs_response_drafting(client, api_headers, monkeypatch):
    async def fake_structured(schema, *_args, **_kwargs):
        if schema.__name__ == "IntakeResult":
            return _intake(risk_level="high")
        if schema.__name__ == "EvidenceResult":
            return EvidenceResult(scores=[], evidence_gaps=[], overall_evidence_strength="moderate")
        if schema.__name__ == "StrategyResult":
            return StrategyResult(
                recommended_action="escalate_to_human",
                reasoning="Borderline case requires human review before drafting a response.",
                key_arguments=["Transaction record exists"],
                evidence_strength="moderate",
                estimated_effort_hours=2,
                risk_of_escalation=0.4,
            )
        if schema.__name__ == "ResponseResult":
            return ResponseResult(
                rebuttal_letter="Human-approved rebuttal based on supplied transaction and verification facts. " * 4,
                evidence_summary="- Transaction record",
                word_count=80,
            )
        if schema.__name__ == "FilingResult":
            return FilingResult(filing_approved=True, validation_notes="ok", missing_requirements=[], filing_confidence=0.7)
        if schema.__name__ == "FeedbackResult":
            return FeedbackResult(lessons_learned=["Recorded outcome for evaluation"], outcome_analysis="Synthetic test")
        raise AssertionError(schema)

    monkeypatch.setattr("backend.agents.intake_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.evidence_assembly_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.strategy_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.response_drafting_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.filing_agent.invoke_structured", fake_structured)
    monkeypatch.setattr("backend.agents.feedback_loop_agent.invoke_structured", fake_structured)

    created = await client.post(
        "/disputes",
        json={
            "dispute_amount": 15000,
            "reason_code": "product_not_received",
            "merchant_name": "Acme",
            "avs_match": True,
            "cvv_verified": True,
            "shipping_address": "1 Test Street",
        },
        headers=api_headers,
    )
    session_id = created.json()["session_id"]
    started = await client.post(f"/disputes/{session_id}/start", headers=api_headers)
    assert started.status_code == 200
    assert started.json()["status"] == "awaiting_human_review"

    gated = await client.get(f"/disputes/{session_id}", headers=api_headers)
    approvals = gated.json()["case"]["human_approvals"]
    assert approvals and approvals[-1]["open"] is True
    assert approvals[-1]["origin"] == "strategy"

    decided = await client.post(
        f"/disputes/{session_id}/decide",
        json={"decision": "approve"},
        headers=api_headers,
    )
    assert decided.status_code == 200
    assert decided.json()["status"] in {"awaiting_outcome", "awaiting_human_review", "filing"}

    detail = await client.get(f"/disputes/{session_id}", headers=api_headers)
    case = detail.json()["case"]
    assert case.get("response") is not None
    assert case["response"]["rebuttal_letter"]
    assert any(t.get("agent") == "response_drafting" for t in case.get("agent_trace", []))


@pytest.mark.asyncio
async def test_health_endpoints(client):
    live = await client.get("/health/live")
    ready = await client.get("/health/ready")
    assert live.status_code == 200
    assert ready.status_code == 200
    assert live.json()["status"] == "ok"
