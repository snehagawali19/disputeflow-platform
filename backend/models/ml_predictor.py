"""
XGBoost win-probability model for dispute outcomes.
Adapted from MiladShahidi/Fraud-Detection-XGBoost with leak-free training.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from hyperopt import STATUS_OK, Trials, fmin, hp, tpe
from imblearn.over_sampling import SMOTE
from loguru import logger
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    classification_report,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from backend.models.dispute import DisputeCase
from backend.utils.evidence_quality import credible_delivery_proof, positive_authentication


FEATURE_COLUMNS = [
    "dispute_amount",
    "evidence_count",
    "avg_evidence_relevance",
    "max_evidence_relevance",
    "has_delivery_proof",
    "has_auth_proof",
    "has_customer_communication",
    "has_transaction_receipt",
    "reason_code_fraudulent",
    "reason_code_product_not_received",
    "reason_code_product_unacceptable",
    "reason_code_unauthorized",
    "reason_code_duplicate",
    "reason_code_subscription",
    "payment_method_card",
    "payment_method_upi",
    "avs_match",
    "cvv_verified",
    "three_ds",
    "risk_level_numeric",
]


def default_model_path() -> str:
    try:
        from backend.core.config import get_settings

        return get_settings().model_path
    except Exception:
        return os.path.join(os.path.dirname(__file__), "..", "data", "models", "win_probability_model.pkl")


def extract_features(case: DisputeCase) -> dict[str, float]:
    evidence_count = len(case.evidence_items)
    avg_relevance = sum(e.relevance_score for e in case.evidence_items) / max(evidence_count, 1)
    evidence_types = {e.evidence_type for e in case.evidence_items}
    txn = case.transaction
    return {
        "dispute_amount": float(case.dispute_amount),
        "evidence_count": float(evidence_count),
        "avg_evidence_relevance": float(avg_relevance),
        "max_evidence_relevance": float(max((e.relevance_score for e in case.evidence_items), default=0)),
        "has_delivery_proof": float(any(credible_delivery_proof(item) for item in case.evidence_items)),
        "has_auth_proof": float(positive_authentication(case, case.evidence_items)),
        "has_customer_communication": float("customer_communication" in evidence_types),
        "has_transaction_receipt": float("transaction_receipt" in evidence_types),
        "reason_code_fraudulent": float(case.reason_code.value == "fraudulent"),
        "reason_code_product_not_received": float(case.reason_code.value == "product_not_received"),
        "reason_code_product_unacceptable": float(case.reason_code.value == "product_unacceptable"),
        "reason_code_unauthorized": float(case.reason_code.value == "unauthorized"),
        "reason_code_duplicate": float(case.reason_code.value == "duplicate"),
        "reason_code_subscription": float(case.reason_code.value == "subscription_canceled"),
        "payment_method_card": float(txn.payment_method == "card") if txn else 0.0,
        "payment_method_upi": float(txn.payment_method == "upi") if txn else 0.0,
        "avs_match": float(bool(txn and txn.avs_match)),
        "cvv_verified": float(bool(txn and txn.cvv_verified)),
        "three_ds": float(bool(txn and txn.three_ds_authenticated)),
        "risk_level_numeric": float({"low": 0, "medium": 1, "high": 2, "critical": 3}.get(case.risk_level.value, 1)),
    }


def _heuristic(features: dict[str, float]) -> tuple[float, float]:
    score = 0.3
    if features["evidence_count"] >= 3:
        score += 0.15
    if features["avg_evidence_relevance"] > 0.6:
        score += 0.15
    if features["has_delivery_proof"]:
        score += 0.1
    if features["has_auth_proof"]:
        score += 0.1
    if features["has_customer_communication"]:
        score += 0.05
    if features["three_ds"]:
        score += 0.1
    if features["reason_code_fraudulent"] and not features["has_auth_proof"]:
        score -= 0.2
    if features["dispute_amount"] > 100000:
        score -= 0.05
    return max(0.05, min(0.95, score)), 0.4


def _load_bundle(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    bundle = joblib.load(path)
    if isinstance(bundle, dict) and "model" in bundle:
        return bundle
    return {"model": bundle, "feature_columns": FEATURE_COLUMNS}


def predict_win_probability(case: DisputeCase) -> tuple[float, float]:
    features = extract_features(case)
    feature_df = pd.DataFrame([features])[FEATURE_COLUMNS]
    path = default_model_path()
    bundle = None
    try:
        bundle = _load_bundle(path)
    except Exception as exc:
        logger.warning(f"Model loading failed, using heuristic: {exc}")

    if bundle is None:
        from backend.core.config import get_settings

        if get_settings().app_env == "production" and not get_settings().allow_heuristic_model:
            raise RuntimeError("Win-probability model artifact is missing")
        return _heuristic(features)

    model = bundle["model"]
    columns = bundle.get("feature_columns", FEATURE_COLUMNS)
    proba = model.predict_proba(feature_df[columns])[0]
    win_prob = float(proba[1] if len(proba) > 1 else proba[0])
    confidence = float(abs(win_prob - 0.5) * 2)
    return max(0.0, min(1.0, win_prob)), max(0.0, min(1.0, confidence))


def get_model_status() -> dict[str, Any]:
    """Return transparent, user-safe metadata for the strategy model."""
    path = default_model_path()
    manifest_path = os.path.join(os.path.dirname(path), "model_manifest.json")
    manifest: dict[str, Any] = {}
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        pass
    metrics = manifest.get("metrics") or {}
    return {
        "available": os.path.exists(path),
        "kind": "xgboost" if os.path.exists(path) else "heuristic fallback",
        "dataset_kind": manifest.get("dataset_kind", "unknown"),
        "trained_from_real_outcomes": bool(manifest.get("trained_from_real_outcomes", False)),
        "rows": metrics.get("rows"),
        "roc_auc": metrics.get("roc_auc"),
        "accuracy": (metrics.get("report") or {}).get("accuracy"),
        "note": metrics.get("note", "Decision support only; not a final dispute outcome."),
    }


def _file_checksum(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_holdout(y_test, y_proba, y_heuristic) -> dict[str, float]:
    """Compare model vs the labeling heuristic on a holdout. Not real-world accuracy."""
    return {
        "model_roc_auc": float(roc_auc_score(y_test, y_proba)),
        "heuristic_roc_auc": float(roc_auc_score(y_test, y_heuristic)),
        "model_brier": float(brier_score_loss(y_test, y_proba)),
        "heuristic_brier": float(brier_score_loss(y_test, y_heuristic)),
        "note": "Holdout vs heuristic baseline on this CSV only. Not issuer accuracy.",
    }


def train_model(training_data_path: str | None = None, max_evals: int = 12, dataset_kind: str | None = None):
    if training_data_path is None:
        training_data_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "synthetic", "training_disputes.csv"
        )
    if not os.path.exists(training_data_path):
        logger.warning(f"No training data found at {training_data_path}. Using heuristic model.")
        return None

    df = pd.read_csv(training_data_path)
    missing = [col for col in FEATURE_COLUMNS + ["outcome_won"] if col not in df.columns]
    if missing:
        raise ValueError(f"Training data missing columns: {missing}")

    X = df[FEATURE_COLUMNS]
    y = df["outcome_won"].astype(int)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    k_neighbors = max(1, min(5, int(y_train.value_counts().min()) - 1))
    try:
        smote = SMOTE(random_state=42, sampling_strategy=0.5, k_neighbors=k_neighbors)
        X_train, y_train = smote.fit_resample(X_train, y_train)
    except ValueError as exc:
        logger.warning(f"SMOTE skipped: {exc}")

    space = {
        "n_estimators": hp.quniform("n_estimators", 80, 240, 20),
        "max_depth": hp.quniform("max_depth", 3, 8, 1),
        "learning_rate": hp.uniform("learning_rate", 0.03, 0.2),
        "subsample": hp.uniform("subsample", 0.7, 1.0),
        "colsample_bytree": hp.uniform("colsample_bytree", 0.7, 1.0),
    }

    def objective(params):
        clf = xgb.XGBClassifier(
            n_estimators=int(params["n_estimators"]),
            max_depth=int(params["max_depth"]),
            learning_rate=params["learning_rate"],
            subsample=params["subsample"],
            colsample_bytree=params["colsample_bytree"],
            eval_metric="logloss",
            random_state=42,
            n_jobs=1,
        )
        clf.fit(X_train, y_train)
        proba = clf.predict_proba(X_test)[:, 1]
        return {"loss": 1 - roc_auc_score(y_test, proba), "status": STATUS_OK}

    trials = Trials()
    best = fmin(objective, space=space, algo=tpe.suggest, max_evals=max_evals, trials=trials, rstate=np.random.default_rng(42))
    base = xgb.XGBClassifier(
        n_estimators=int(best["n_estimators"]),
        max_depth=int(best["max_depth"]),
        learning_rate=float(best["learning_rate"]),
        subsample=float(best["subsample"]),
        colsample_bytree=float(best["colsample_bytree"]),
        eval_metric="logloss",
        random_state=42,
        n_jobs=1,
    )
    model = CalibratedClassifierCV(base, method="isotonic", cv=3)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    y_heuristic = X_test.apply(lambda row: _heuristic(row.to_dict())[0], axis=1)
    kind = dataset_kind or ("synthetic_heuristic_labels" if "synthetic" in training_data_path.replace("\\", "/") else "csv")
    comparison = evaluate_holdout(y_test, y_proba, y_heuristic)
    metrics = {
        "roc_auc": comparison["model_roc_auc"],
        "pr_auc": float(average_precision_score(y_test, y_proba)),
        "brier": comparison["model_brier"],
        "heuristic_roc_auc": comparison["heuristic_roc_auc"],
        "heuristic_brier": comparison["heuristic_brier"],
        "report": classification_report(y_test, y_pred, output_dict=True),
        "best_params": {k: float(v) for k, v in best.items()},
        "rows": int(len(df)),
        "dataset_kind": kind,
        "note": (
            "Real recorded outcomes — still not a card-network accuracy claim"
            if kind == "real_outcomes"
            else "Synthetic simulator metrics, not real-world accuracy"
        ),
    }
    logger.info(
        "Model trained on {} rows ({}). Model AUC {:.3f} vs heuristic AUC {:.3f} — not issuer accuracy.",
        metrics["rows"],
        kind,
        metrics["roc_auc"],
        metrics["heuristic_roc_auc"],
    )

    path = default_model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bundle = {"model": model, "feature_columns": FEATURE_COLUMNS, "metrics": metrics}
    joblib.dump(bundle, path)
    checksum = _file_checksum(path)
    previous_version = 0
    manifest_path = os.path.join(os.path.dirname(path), "model_manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                previous_version = int(json.load(handle).get("version") or 0)
        except Exception:
            previous_version = 0
    manifest = {
        "version": previous_version + 1,
        "model_path": path,
        "checksum_sha256": checksum,
        "feature_columns": FEATURE_COLUMNS,
        "metrics": metrics,
        "dataset": training_data_path,
        "dataset_kind": kind,
        "trained_from_real_outcomes": kind == "real_outcomes",
    }
    with open(os.path.join(os.path.dirname(path), "model_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    logger.info(f"Model saved to {path}")
    return model
