from datetime import timezone

import pytest

from backend.models.dispute import DisputeCase, DisputeReasonCode, TransactionData


def test_case_defaults_and_enums():
    case = DisputeCase(dispute_amount=100, reason_code=DisputeReasonCode.GENERAL)
    assert case.status.value == "open"
    assert case.created_at.tzinfo is not None
    dumped = case.model_dump()
    restored = DisputeCase.model_validate(dumped)
    assert restored.case_id == case.case_id


def test_rejects_invalid_currency():
    with pytest.raises(Exception):
        TransactionData(amount=10, currency="XXX")


def test_rejects_negative_amount():
    with pytest.raises(Exception):
        DisputeCase(dispute_amount=-1)
