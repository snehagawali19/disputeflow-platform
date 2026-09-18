"""Holdout comparison of XGBoost vs the labeling heuristic. Not real-world accuracy."""

from __future__ import annotations

import json
import os
import sys

import pandas as pd
from sklearn.model_selection import train_test_split

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.ml_predictor import (
    FEATURE_COLUMNS,
    _heuristic,
    _load_bundle,
    default_model_path,
    evaluate_holdout,
)


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backend",
        "data",
        "synthetic",
        "training_disputes.csv",
    )
    if not os.path.exists(csv_path):
        raise SystemExit(f"No CSV at {csv_path}")
    df = pd.read_csv(csv_path)
    _, X_test, _, y_test = train_test_split(
        df[FEATURE_COLUMNS], df["outcome_won"].astype(int), test_size=0.2, random_state=42, stratify=df["outcome_won"]
    )
    y_heuristic = X_test.apply(lambda row: _heuristic(row.to_dict())[0], axis=1)
    bundle = _load_bundle(default_model_path())
    if bundle is None:
        raise SystemExit("No trained model artifact. Run scripts/retrain_model.py first.")
    y_proba = bundle["model"].predict_proba(X_test[bundle.get("feature_columns", FEATURE_COLUMNS)])[:, 1]
    report = evaluate_holdout(y_test, y_proba, y_heuristic)
    report["dataset"] = csv_path
    report["rows_holdout"] = int(len(y_test))
    print(json.dumps(report, indent=2))
    print("These numbers measure fit to this CSV (often synthetic labels), not card-network win rates.")


if __name__ == "__main__":
    main()
