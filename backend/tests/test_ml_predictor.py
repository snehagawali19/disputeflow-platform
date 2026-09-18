from backend.models.dispute import DisputeCase, DisputeReasonCode, TransactionData
from backend.models.ml_predictor import FEATURE_COLUMNS, extract_features, predict_win_probability


def test_feature_schema_and_heuristic_prediction():
    case = DisputeCase(
        dispute_amount=15000,
        reason_code=DisputeReasonCode.PRODUCT_NOT_RECEIVED,
        transaction=TransactionData(amount=15000, avs_match=True, cvv_verified=True),
    )
    features = extract_features(case)
    assert list(features) == FEATURE_COLUMNS
    win_prob, confidence = predict_win_probability(case)
    assert 0 <= win_prob <= 1
    assert 0 <= confidence <= 1
