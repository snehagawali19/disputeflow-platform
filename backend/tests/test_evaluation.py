import json
from pathlib import Path

from backend.agents.evidence_factory import evidence_from_artifacts, evidence_from_supplied_facts
from backend.models.dispute import DisputeCase, DisputeReasonCode, TransactionData
from backend.models.ml_predictor import FEATURE_COLUMNS, _heuristic, evaluate_holdout, extract_features
from backend.models.dispute import EvidenceItem
from backend.utils.evidence_quality import credible_delivery_proof, deterministic_evidence_gaps


def test_heuristic_baseline_metrics_are_labeled():
    import numpy as np

    y = np.array([0, 1, 0, 1, 1, 0, 1, 0])
    model = np.array([0.2, 0.8, 0.3, 0.7, 0.6, 0.4, 0.9, 0.1])
    heur = np.array([0.25, 0.7, 0.35, 0.65, 0.55, 0.45, 0.8, 0.2])
    report = evaluate_holdout(y, model, heur)
    assert "model_roc_auc" in report
    assert "heuristic_roc_auc" in report
    assert "not issuer" in report["note"].lower() or "not" in report["note"].lower()


def test_agent_eval_set_does_not_promote_address_to_delivery():
    cases = json.loads(Path(__file__).with_name("eval_cases.json").read_text(encoding="utf-8"))
    for spec in cases:
        case = DisputeCase(
            dispute_amount=spec["dispute_amount"],
            reason_code=DisputeReasonCode(spec["reason_code"]),
            transaction=TransactionData(
                amount=spec["dispute_amount"],
                avs_match=spec.get("avs_match"),
                cvv_verified=spec.get("cvv_verified"),
                three_ds_authenticated=spec.get("three_ds_authenticated"),
                shipping_address=spec.get("shipping_address"),
            ),
        )
        types = {item.evidence_type for item in evidence_from_supplied_facts(case)}
        for forbidden in spec.get("expected_no_types", []):
            assert forbidden not in types, spec["notes"]
        for required in spec.get("expected_types", []):
            assert required in types, spec["notes"]
        features = extract_features(case)
        assert list(features) == FEATURE_COLUMNS
        prob, _ = _heuristic(features)
        assert 0.05 <= prob <= 0.95


def test_supplied_artifacts_are_kept_as_typed_evidence():
    items = evidence_from_artifacts(
        [{"evidence_type": "delivery_proof", "title": "Tracking", "content": "Carrier scan delivered 2026-01-02"}]
    )
    assert items[0].evidence_type == "delivery_proof"
    assert items[0].source == "supplied_artifact"


def test_delivery_label_alone_is_not_proof():
    weak = EvidenceItem(evidence_type="delivery_proof", title="Tracking", content="tracking number")
    strong = EvidenceItem(
        evidence_type="delivery_proof",
        title="Carrier delivery confirmation",
        content="Carrier confirmed delivered 2026-01-02, tracking ID ZXCVB12345.",
    )
    assert credible_delivery_proof(weak) is False
    assert credible_delivery_proof(strong) is True

    case = DisputeCase(dispute_amount=10, reason_code=DisputeReasonCode.PRODUCT_NOT_RECEIVED)
    assert any("delivery" in gap.lower() for gap in deterministic_evidence_gaps(case, [weak]))
