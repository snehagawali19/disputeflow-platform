import json

import pytest
from sqlalchemy import select

from backend.db.models import TrainingOutcome
from backend.db.session import SessionLocal, init_db
from backend.models.dispute import DisputeCase, DisputeReasonCode, DisputeStatus, DisputeStrategy, TransactionData
from backend.services.learning import persist_training_outcome, prediction_log


@pytest.mark.asyncio
async def test_persist_training_outcome_logs_prediction_vs_actual():
    case = DisputeCase(
        dispute_amount=2000,
        reason_code=DisputeReasonCode.GENERAL,
        status=DisputeStatus.WON,
        actual_outcome="won",
        transaction=TransactionData(amount=2000, avs_match=True, cvv_verified=True),
        strategy=DisputeStrategy(recommended_action="contest", win_probability=0.62, confidence=0.4, reasoning="test"),
    )
    log = prediction_log(case)
    assert "predicted_win_probability" in log
    assert "heuristic_win_probability" in log
    assert "issuer accuracy" in log["note"].lower() or "not a claim" in log["note"].lower()

    await init_db()
    async with SessionLocal() as session:
        first = await persist_training_outcome(session, case)
        await session.commit()
        second = await persist_training_outcome(session, case)
        assert first is not None and second is not None
        assert first.id == second.id
        rows = (await session.scalars(select(TrainingOutcome).where(TrainingOutcome.case_id == case.case_id))).all()
        assert len(rows) == 1
        payload = json.loads(rows[0].features_json)
        assert payload["actual_outcome"] == "won"
        assert rows[0].outcome_won is True
